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
import os
import re
import sys
from datetime import datetime, timezone

from .eur_price import normalize_amount

SOURCES = ("linksme", "prnews", "collaborator")

# What a sheet's prices are in when the cells carry no currency marker of
# their own. Links.me and Collaborator quote in euros, PRNews in dollars.
DEFAULT_CURRENCY = {
    "linksme":      "EUR",
    "prnews":       "USD",
    "collaborator": "EUR",
}

# Sanity window, matching the scrapers: a year, a row id or a traffic figure
# picked out of the wrong column cannot pass as a price.
MIN_PRICE = 1
MAX_PRICE = 100_000

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


def parse_price_cell(raw: str) -> tuple[float | None, str | None]:
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
    value = normalize_amount(m.group(0).strip())
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
    best: dict[str, dict] = {}
    skipped = 0

    for row in body:
        domain = normalize_domain(row[d_idx]) if len(row) > d_idx else ""
        raw_price = row[p_idx] if len(row) > p_idx else ""
        price, currency = parse_price_cell(raw_price)
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
    }


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


def sync_source(sb, source: str, sheet_url: str) -> dict:
    """
    Pull `sheet_url` into publisher_prices for `source`, replacing what was
    there, and record the outcome on publisher_price_sheets.

    Returns {"source", "imported", "skipped", "header", "sheet_url"}.
    Raises on a sheet that cannot be read or yields nothing usable — the
    caller turns that into an error the user sees, and the previous prices are
    left untouched rather than wiped by a bad sync.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")

    spreadsheet_id = extract_spreadsheet_id(sheet_url)
    print(f"[price_catalog] {source}: reading spreadsheet {spreadsheet_id}")
    values = read_sheet_values(spreadsheet_id)
    parsed = parse_rows(values, source)
    entries = parsed["entries"]
    print(f"[price_catalog] {source}: {len(values)} sheet row(s) → "
          f"{len(entries)} price(s), {parsed['skipped']} skipped "
          f"(columns {parsed['columns']} of header {parsed['header'][:8]})")

    if not entries:
        raise ValueError(
            "no magazine/price pairs found in that sheet — check that it has a "
            "domain column and a price column"
        )

    # Replace, so a magazine dropped from the sheet stops being priced. Done
    # only once the new rows are in hand, so a failed read cannot empty the
    # table.
    sb.from_("publisher_prices").delete().eq("source", source).execute()
    for i in range(0, len(entries), 500):
        chunk = [{"source": source, **e} for e in entries[i:i + 500]]
        sb.from_("publisher_prices").insert(chunk).execute()

    sb.from_("publisher_price_sheets").upsert({
        "source":         source,
        "sheet_url":      sheet_url,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "row_count":      len(entries),
        "last_error":     None,
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }).execute()

    return {
        "source":    source,
        "imported":  len(entries),
        "skipped":   parsed["skipped"],
        "header":    parsed["header"],
        "sheet_url": sheet_url,
    }


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
