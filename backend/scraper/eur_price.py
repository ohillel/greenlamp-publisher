"""
EUR price parsing shared by the Links.me scraper and the Collaborator.pro
API client.

Sources write the same currency differently — "113,40 EUR" (comma decimal)
and "160.95 EUR" (dot decimal) — so both separators have to be accepted,
and thousands separators are ambiguous between them ("1.234,56" and
"1,234.56" are the same number). The rule used here is "the last separator
is the decimal one", which resolves both spellings correctly.

Prices stay in EUR. Nothing here converts to USD.
"""
import re

# A number possibly carrying , . or spaces as separators, sitting next to a
# EUR marker on either side: "113,40 EUR", "160.95 EUR", "€113.40", "1.234,56 €".
_EUR_RE = re.compile(
    r'(?:EUR|€)\s*(\d[\d\s., ]*\d|\d)'      # prefix: € 113,40
    r'|(\d[\d\s., ]*\d|\d)\s*(?:EUR|€)',     # suffix: 113,40 EUR
    re.IGNORECASE,
)

# Same sanity window the existing scrapers use, so a stray year or count
# scraped from the wrong element cannot pass as a price.
MIN_PRICE = 1
MAX_PRICE = 100_000


def normalize_amount(raw: str) -> float | None:
    """
    Turn a scraped amount into a float, accepting either decimal separator.

    "113,40"    -> 113.4
    "160.95"    -> 160.95
    "1.234,56"  -> 1234.56
    "1,234.56"  -> 1234.56
    "1 800"     -> 1800.0
    "1.234"     -> 1234.0   (3 digits after the separator = thousands)
    """
    if not raw:
        return None
    s = raw.replace(" ", " ").replace(" ", "").strip()
    if not s:
        return None

    last_dot   = s.rfind(".")
    last_comma = s.rfind(",")
    last_sep   = max(last_dot, last_comma)

    if last_sep == -1:
        digits = s
    else:
        decimals = len(s) - last_sep - 1
        if decimals in (1, 2):
            # The final separator really is the decimal point.
            digits = s[:last_sep].replace(".", "").replace(",", "") + "." + s[last_sep + 1:]
        else:
            # 3+ digits after it (or none) => grouping separator, not a decimal.
            digits = s.replace(".", "").replace(",", "")

    try:
        return float(digits)
    except ValueError:
        return None


def parse_eur(text: str) -> float | None:
    """
    First plausible EUR amount in `text`, or None.

    Values outside MIN_PRICE..MAX_PRICE are skipped rather than returned, so a
    percentage or an id picked up from a neighbouring element does not become
    a price.
    """
    if not text:
        return None
    for m in _EUR_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        val = normalize_amount(raw)
        if val is not None and MIN_PRICE <= val <= MAX_PRICE:
            return round(val, 2)
    return None


def parse_all_eur(text: str) -> list[float]:
    """Every plausible EUR amount in `text`, in the order they appear."""
    out: list[float] = []
    if not text:
        return out
    for m in _EUR_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        val = normalize_amount(raw)
        if val is not None and MIN_PRICE <= val <= MAX_PRICE:
            out.append(round(val, 2))
    return out
