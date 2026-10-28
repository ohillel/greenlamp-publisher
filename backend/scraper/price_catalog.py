"""
Magazine prices read from a per-publisher Google Sheet instead of scraped.

Links.me, PRNews and Collaborator each sync from one sheet of magazine +
price. A price lookup then becomes a table read: no browser, no login, no
session, no timeout — and no dependence on those sites' markup, which is what
every price failure so far has come down to.

PressWhizz is deliberately absent. There is no sheet for it, so it keeps its
live lookup in presswhizz.py.

A sync REPLACES everything held for that source, so a magazine dropped from
the sheet disappears instead of lingering at a stale price. A domain missing
from the sheet simply has no price; nothing falls back to scraping.
"""
import csv
import io
import os
import re
import sys
from datetime import datetime, timezone

from .eur_price import normalize_amount

SOURCES = ("linksme", "prnews", "collaborator")

# What a source's prices are in when the cells carry no currency marker of
# their own. Links.me and PRNews are supplied in dollars, Collaborator in
# euros. Nothing converts between currencies anywhere in this codebase — the
# figures are stored and shown exactly as given, only labelled correctly.
DEFAULT_CURRENCY = {
    "linksme":      "USD",
    "prnews":       "USD",
    "collaborator": "EUR",
}

# Sanity window. Far wider than the scrapers': they read prices out of page
# text, where a year or an element id sitting nearby could pass for one, so a
# tight window earned its keep. A supplied file has a price column and nothing
# to confuse it with, and a tight window only destroys real data — at 1..100k
# it dropped economist.com at $117,130.20 and nytimes.com at $194,670 from the
# PRNews catalog, and 19 sites priced under a dollar from Collaborator.
#
# What guards against a misread column is the header detection, plus the price
# range reported back after every import.
MIN_PRICE = 0.01
MAX_PRICE = 1_000_000

# Header names worth trusting, lowercased. Checked as substrings, most
# specific first, so "price usd" beats a bare "url".
_DOMAIN_HEADER_HINTS = ("domain", "website", "site", "url", "magazine", "publication", "media")
_PRICE_HEADER_HINTS  = ("price", "cost", "guest post", "article", "amount", "rate")

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_CURRENCY_RE = re.compile(r"(EUR|USD|GBP|ILS|€|\$|£|₪)", re.IGNORECASE)
_CURRENCY_BY_SYMBOL = {"€": "EUR", "$": "USD", "£": "GBP", "₪": "ILS"}


def extract_spreadsheet_id(url_or_id: str) -> str:
    """The spreadsheet id out of a full Sheets URL, or the id itself."""
    s = (url_or_id or "").strip()
    if not s:
        raise ValueError("no sheet URL given")
    m = _SHEET_ID_RE.search(s)
    if m:
        return m.group(1)
    if "/" in s or " " in s:
        raise ValueError(f"not a Google Sheets URL: {url_or_id!r}")
    return s


def normalize_domain(text: str) -> str:
    """
    A bare lowercase domain: no scheme, no www, no path, no trailing slash.

    Must agree with how the scrapers and the article rows spell a domain, or a
    sheet row would never match the magazine it belongs to.
    """
    t = (text or "").strip().lower()
    t = re.sub(r"^[a-z]+://", "", t)
    if t.startswith("www."):
        t = t[4:]
    t = t.split("/")[0].split("?")[0].split("#")[0]
    return t.strip().rstrip(".")


def detect_decimal_style(samples: list[str]) -> str:
    """
    "comma" if this file writes decimals European-style (113,40), else "dot".

    Decided once per file rather than per cell, because a single value cannot
    be read reliably on its own: "516.864" is 516.864 in a dollar export and
    516864 in a European one. Guessing per cell is what dropped 32,371 of the
    233,820 Links.me rows — every price with three decimal places looked like
    a thousands separator and was discarded for exceeding MAX_PRICE.

    A comma followed by one or two final digits is the giveaway, since no
    thousands separator is ever followed by fewer than three.
    """
    for s in samples:
        if re.search(r",\d{1,2}\s*(?:[A-Za-z€$£₪]|$)", (s or "").strip()):
            return "comma"
    return "dot"


def parse_price_cell(raw: str, style: str = "comma") -> tuple[float | None, str | None]:
    """
    ("113,40 EUR") -> (113.4, "EUR"); ("$1,250") -> (1250.0, "USD").

    Returns (None, None) when the cell holds no usable number. Currency comes
    back only when the cell says so — the caller supplies the source default
    otherwise, rather than this guessing one.
    """
    s = (raw or "").strip()
    if not s:
        return None, None

    currency = None
    m = _CURRENCY_RE.search(s)
    if m:
        token = m.group(1)
        currency = _CURRENCY_BY_SYMBOL.get(token, token.upper())

    # Close up a space used as a thousands separator ("2 768.78" -> "2768.78")
    # before anything else, so the number can then be matched without spaces.
    # Only a run of exactly three digits counts, which is why the "3" in
    # "120 EUR / 3 links" is left alone instead of being glued onto the 120.
    closed = re.sub(r"(?<=\d)[   ]+(?=\d{3}(?!\d))", "", s)

    digits = re.sub(r"[^\d.,]", " ", closed).strip()
    if not digits:
        return None, None
    # A cell may hold more than the price ("120 EUR / 3 links"); the first
    # number-shaped run is the price.
    m = re.search(r"\d[\d.,]*", digits)
    if not m:
        return None, None
    number = m.group(0).strip()
    if style == "dot":
        # Dots are decimal points and commas group thousands, so 516.864 keeps
        # its three decimals instead of becoming half a million.
        try:
            value = float(number.replace(",", "").rstrip("."))
        except ValueError:
            value = None
    else:
        value = normalize_amount(number)
    if value is None or not (MIN_PRICE <= value <= MAX_PRICE):
        return None, None
    return round(value, 2), currency


def pick_columns(header: list[str]) -> tuple[int | None, int | None]:
    """
    (domain_index, price_index) for a sheet's header row, or None where no
    column matched. Hints are checked in order, so a sheet with both "URL" and
    "Domain" settles on "Domain".
    """
    cells = [(c or "").strip().lower() for c in header]

    def find(hints):
        for hint in hints:
            for i, cell in enumerate(cells):
                if hint in cell:
                    return i
        return None

    return find(_DOMAIN_HEADER_HINTS), find(_PRICE_HEADER_HINTS)


def _looks_like_header(row: list[str]) -> bool:
    d, p = pick_columns(row)
    return d is not None and p is not None


def parse_rows(rows: list[list[str]], source: str) -> dict:
    """
    Sheet values -> {"entries": [{domain, price, currency}], "skipped": int,
                     "header": [...], "columns": (d, p)}

    Entries are de-duplicated on domain, keeping the CHEAPEST price, which is
    the rule the scrapers already used when a magazine had several offers.
    """
    if not rows:
        return {"entries": [], "skipped": 0, "header": [], "columns": (None, None)}

    header = rows[0]
    body = rows[1:]
    d_idx, p_idx = pick_columns(header)

    # A sheet with no recognisable header: fall back to the first two columns,
    # and keep the first row as data rather than discarding it.
    if d_idx is None or p_idx is None:
        if _looks_like_header(header):
            pass
        d_idx = 0 if d_idx is None else d_idx
        p_idx = 1 if p_idx is None else p_idx
        body = rows

    default_currency = DEFAULT_CURRENCY.get(source)
    # Settled once, from the file's own price column, before any row is read.
    style = detect_decimal_style(
        [row[p_idx] for row in body[:5000] if len(row) > p_idx]
    )
    best: dict[str, dict] = {}
    skipped = 0

    for row in body:
        domain = normalize_domain(row[d_idx]) if len(row) > d_idx else ""
        raw_price = row[p_idx] if len(row) > p_idx else ""
        price, currency = parse_price_cell(raw_price, style)
        if not domain or "." not in domain or price is None:
            skipped += 1
            continue
        entry = {"domain": domain, "price": price, "currency": currency or default_currency}
        existing = best.get(domain)
        if existing is None or price < existing["price"]:
            best[domain] = entry

    return {
        "entries": list(best.values()),
        "skipped": skipped,
        "header": header,
        "columns": (d_idx, p_idx),
        "decimal_style": style,
    }


def read_delimited(content: bytes | str) -> list[list[str]]:
    """
    An uploaded CSV/TSV as rows of strings.

    The delimiter is sniffed from the header rather than taken from the file
    extension, which is routinely wrong — the catalogs arrived as one comma
    file named .csv, one tab file named .tsv, and nothing guarantees the next
    one matches its name.
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    first_line = next((ln for ln in text.split("\n") if ln.strip()), "")
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[(c or "").strip() for c in row] for row in reader if any((c or "").strip() for c in row)]


def read_sheet_values(spreadsheet_id: str) -> list[list[str]]:
    """Every cell of the first tab, as rows of strings."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from google_client import get_credentials
    from googleapiclient.discovery import build

    service = build("sheets", "v4", credentials=get_credentials())
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = meta.get("sheets") or []
    if not sheets:
        raise ValueError("spreadsheet has no tabs")
    first_tab = sheets[0]["properties"]["title"]

    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=first_tab,
    ).execute()
    return [[str(c) for c in row] for row in (resp.get("values") or [])]


# Rows per insert request. Large enough that a 230k-row catalog is a few
# hundred requests rather than a few thousand, small enough to stay well
# inside PostgREST's payload limit.
_INSERT_CHUNK = 2000


def import_entries(sb, source: str, entries: list[dict], label: str) -> dict:
    """
    Replace everything held for `source` with `entries`, then record the
    outcome on publisher_price_sheets.

    The delete runs only once the new rows are parsed and in hand, so a file
    that cannot be read leaves the previous prices in place rather than
    emptying the catalog.
    """
    if not entries:
        raise ValueError(
            "no magazine/price pairs found — check that the file has a domain "
            "column and a price column"
        )

    sb.from_("publisher_prices").delete().eq("source", source).execute()
    for i in range(0, len(entries), _INSERT_CHUNK):
        chunk = [{"source": source, **e} for e in entries[i:i + _INSERT_CHUNK]]
        sb.from_("publisher_prices").insert(chunk).execute()
        done = min(i + _INSERT_CHUNK, len(entries))
        # Every tenth batch: a 230k-row catalog is 117 batches, and a line per
        # batch buries everything else in the Railway log.
        if done == len(entries) or (i // _INSERT_CHUNK) % 10 == 0:
            print(f"[price_catalog] {source}: inserted {done}/{len(entries)}")

    now = datetime.now(timezone.utc).isoformat()
    sb.from_("publisher_price_sheets").upsert({
        "source":         source,
        "source_label":   label,
        "last_synced_at": now,
        "row_count":      len(entries),
        "last_error":     None,
        "status":         "ok",
        "updated_at":     now,
    }).execute()

    return {"source": source, "imported": len(entries), "label": label}


def refresh_articles(sb) -> dict:
    """
    Rewrite every article's Links.me / PRNews / Collaborator price from the
    catalog. PressWhizz is left alone — it is still fetched live.

    Needed because an article keeps whatever its last price check wrote, and
    checks that ran before the catalog existed hold scraped figures that are
    both out of date and, having been scraped in euros, now mislabelled as
    dollars. Without this, a freshly imported catalog would only reach
    articles submitted after it.

    A domain the catalog does not carry has its three catalog prices cleared
    rather than left at the old scraped value: no price is honest, a stale one
    is not.
    """
    articles = (sb.from_("articles")
                  .select("id, magazine")
                  .not_.is_("magazine", "null")
                  .execute().data) or []

    by_domain: dict[str, list[str]] = {}
    for a in articles:
        domain = normalize_domain(a.get("magazine") or "")
        if domain and "." in domain:
            by_domain.setdefault(domain, []).append(a["id"])
    if not by_domain:
        return {"articles": 0, "domains": 0, "priced": 0}

    # One read of the catalog for every domain in play, chunked to keep the
    # query string a sane length.
    prices: dict[str, dict] = {}
    domains = list(by_domain)
    for i in range(0, len(domains), 200):
        rows = (sb.from_("publisher_prices")
                  .select("domain, source, price")
                  .in_("domain", domains[i:i + 200])
                  .execute().data) or []
        for r in rows:
            prices.setdefault(r["domain"], {})[r["source"]] = float(r["price"])

    priced = 0
    for domain, ids in by_domain.items():
        found = prices.get(domain) or {}
        if found:
            priced += len(ids)
        patch = {
            "price_linksme":      found.get("linksme"),
            "price_prnews":       found.get("prnews"),
            "price_collaborator": found.get("collaborator"),
        }
        for i in range(0, len(ids), 100):
            sb.from_("articles").update(patch).in_("id", ids[i:i + 100]).execute()

    summary = {"articles": len(articles), "domains": len(by_domain), "priced": priced}
    print(f"[price_catalog] refreshed article prices — {summary}")
    return summary


def mark_syncing(sb, source: str, label: str) -> None:
    """Flag a source as mid-import, so a long upload does not read as failure."""
    sb.from_("publisher_price_sheets").upsert({
        "source":       source,
        "source_label": label,
        "status":       "syncing",
        "last_error":   None,
        "updated_at":   datetime.now(timezone.utc).isoformat(),
    }).execute()


def mark_failed(sb, source: str, label: str, error: str) -> None:
    """Record why an import failed. The previous prices are left in place."""
    sb.from_("publisher_price_sheets").upsert({
        "source":       source,
        "source_label": label,
        "status":       "error",
        "last_error":   error[:500],
        "updated_at":   datetime.now(timezone.utc).isoformat(),
    }).execute()


def sync_from_file(sb, source: str, content: bytes, filename: str) -> dict:
    """Import an uploaded CSV/TSV of magazine + price for one source."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")

    rows = read_delimited(content)
    parsed = parse_rows(rows, source)
    row_count = len(rows)
    # A 230k-row catalog is held twice over at this point — once as raw cells,
    # once as parsed entries. Release the raw copy before the inserts begin,
    # which is where the container is tightest.
    del rows
    print(f"[price_catalog] {source}: {row_count} file row(s) from {filename!r} → "
          f"{len(parsed['entries'])} price(s), {parsed['skipped']} skipped "
          f"(columns {parsed['columns']} of header {parsed['header'][:8]})")
    summary = import_entries(sb, source, parsed["entries"], filename)
    # So a freshly imported catalog reaches the articles already in the
    # system, not only ones submitted from here on.
    summary["refreshed"] = refresh_articles(sb)
    summary["skipped"] = parsed["skipped"]
    summary["header"] = parsed["header"]
    return summary


def sync_source(sb, source: str, sheet_url: str) -> dict:
    """Import one source from a Google Sheet of magazine + price."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")

    spreadsheet_id = extract_spreadsheet_id(sheet_url)
    print(f"[price_catalog] {source}: reading spreadsheet {spreadsheet_id}")
    values = read_sheet_values(spreadsheet_id)
    parsed = parse_rows(values, source)
    print(f"[price_catalog] {source}: {len(values)} sheet row(s) → "
          f"{len(parsed['entries'])} price(s), {parsed['skipped']} skipped "
          f"(columns {parsed['columns']} of header {parsed['header'][:8]})")

    summary = import_entries(sb, source, parsed["entries"], sheet_url)
    sb.from_("publisher_price_sheets").update({"sheet_url": sheet_url}) \
      .eq("source", source).execute()
    summary["refreshed"] = refresh_articles(sb)
    summary["skipped"] = parsed["skipped"]
    summary["header"] = parsed["header"]
    summary["sheet_url"] = sheet_url
    return summary


def service_client():
    """A Supabase service-role client, for callers that do not already hold one."""
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def lookup(domain: str, sb=None) -> dict:
    """
    {"linksme": float|None, "prnews": float|None, "collaborator": float|None}
    for one magazine domain. One query, exact domain match.

    Never raises: a catalog that cannot be read logs and yields no prices,
    rather than taking down the PressWhizz lookup running beside it.
    """
    wanted = normalize_domain(domain)
    out = {s: None for s in SOURCES}
    if not wanted:
        return out
    try:
        sb = sb or service_client()
    except Exception as e:
        print(f"  [price_catalog] cannot reach the catalog: {e}")
        return out
    try:
        res = sb.from_("publisher_prices") \
                .select("source, price") \
                .eq("domain", wanted) \
                .execute()
    except Exception as e:
        print(f"  [price_catalog] lookup failed for {wanted!r}: {e}")
        return out

    for row in (res.data or []):
        src = row.get("source")
        if src in out and row.get("price") is not None:
            out[src] = float(row["price"])
    found = {k: v for k, v in out.items() if v is not None}
    print(f"  [price_catalog] {wanted} → {found or 'no catalog price'}")
    return out
