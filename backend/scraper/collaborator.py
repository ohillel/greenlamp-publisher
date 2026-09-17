"""
Collaborator.pro price lookup via their official public API.

This replaces the Playwright scraper: collaborator.pro is behind Cloudflare
Turnstile, which a headless browser cannot get past, and the API is both
sanctioned and far more reliable. There is no browser code here any more —
it is a single HTTP GET.

  GET https://collaborator.pro/api/public/creator/list
      ?_url=<domain>&format_id=1&per-page=20&language=en
      X-Api-Key: <COLLABORATOR_API_KEY>

Flow:
  1. Call the endpoint with the bare domain.
  2. From items[], take the one whose "name" is an EXACT domain match
     ("techloy.com" yes, "blog.techloy.com" no).
  3. From that item's prices[], take the entry named "Article".
  4. Return its "pricePublication", parsed to a number.
  5. Return None for: no match, no Article price, 401, or any other error.

Currency is read from the response rather than assumed — the docs show
"2 768.78 USD" while the web UI shows EUR — and is never converted. Note the
thousands separator is a SPACE ("2 768.78"), which normalize_amount handles.

get_price() keeps the same signature and float return as the old scraper, so
bulk_price_check.py needs no change. get_price_detailed() additionally reports
the currency.
"""
import os
import requests

from .eur_price import normalize_amount

API_URL = "https://collaborator.pro/api/public/creator/list"

SITE = "collaborator"

# Placement format: 1 = Article.
FORMAT_ARTICLE = 1
# The prices[] entry we want, matched case-insensitively on its "name".
ARTICLE_PRICE_NAME = "article"

PER_PAGE = 20
LANGUAGE = "en"
REQUEST_TIMEOUT_SECONDS = 30

# Same sanity window the other price sources use, so a stray id or count
# cannot pass as a price.
MIN_PRICE = 1
MAX_PRICE = 1_000_000


def _normalize_domain(raw: str) -> str:
    """Reduce any URL/domain string to a bare lowercase domain."""
    s = (raw or "").strip().lower()
    for scheme in ("https://", "http://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
    if s.startswith("www."):
        s = s[4:]
    return s.split("/")[0].strip()


def _parse_amount_and_currency(raw: str) -> tuple[float, str] | None:
    """
    Split "2 768.78 USD" / "160.95 EUR" / "€160.95" into (amount, currency).

    The currency is taken from the string rather than assumed: the API docs
    show USD while the web UI shows EUR, and guessing wrong would mislabel
    every price. normalize_amount handles the space thousands separator and
    both decimal separators.
    """
    if not raw:
        return None
    text = str(raw).strip()

    currency = None
    for code, symbol in (("USD", "$"), ("EUR", "€"), ("GBP", "£")):
        if code in text.upper() or symbol in text:
            currency = code
            break

    # Strip everything that is not part of the number, leaving separators in
    # place for normalize_amount to interpret.
    digits = "".join(ch for ch in text if ch.isdigit() or ch in ". , ")
    amount = normalize_amount(digits)
    if amount is None or not (MIN_PRICE <= amount <= MAX_PRICE):
        return None

    return round(amount, 2), (currency or "UNKNOWN")


def _article_price(item: dict) -> tuple[float, str, str] | None:
    """(amount, currency, raw string) for the item's "Article" price entry."""
    for price in item.get("prices") or []:
        if not isinstance(price, dict):
            continue
        if (price.get("name") or "").strip().lower() != ARTICLE_PRICE_NAME:
            continue
        raw = price.get("pricePublication")
        parsed = _parse_amount_and_currency(raw)
        if parsed is None:
            print(f"  [{SITE}] Article price present but unparseable: {raw!r}")
            return None
        amount, currency = parsed
        return amount, currency, str(raw)
    return None


def get_price_detailed(magazine_domain: str, debug: bool = False) -> dict | None:
    """
    {"price": float, "currency": "EUR"|"USD"|…, "raw": "160.95 EUR"} or None.

    Never raises: every failure path logs and returns None, so one bad lookup
    cannot take down a bulk run.
    """
    domain = _normalize_domain(magazine_domain)
    api_key = os.environ.get("COLLABORATOR_API_KEY")
    if not api_key:
        print(f"  [{SITE}] COLLABORATOR_API_KEY is not set — skipping")
        return None

    params = {
        "_url": domain,
        "format_id": FORMAT_ARTICLE,
        "per-page": PER_PAGE,
        "language": LANGUAGE,
    }
    # Logged without the token.
    print(f"  [{SITE}] GET {API_URL} params={params}")

    try:
        resp = requests.get(
            API_URL,
            params=params,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:
        print(f"  [{SITE}] request failed for {domain!r}: {e}")
        return None

    print(f"  [{SITE}] HTTP {resp.status_code}")

    if resp.status_code == 401:
        print(f"  [{SITE}] 401 Unauthorized — check COLLABORATOR_API_KEY. "
              f"Body: {resp.text[:200]!r}")
        return None
    if resp.status_code != 200:
        print(f"  [{SITE}] unexpected status {resp.status_code}. "
              f"Body: {resp.text[:200]!r}")
        return None

    try:
        payload = resp.json()
    except Exception as e:
        print(f"  [{SITE}] response was not JSON: {e}. Body: {resp.text[:200]!r}")
        return None

    items = payload.get("items")
    if not isinstance(items, list):
        print(f"  [{SITE}] response has no items[] — keys: {sorted(payload)[:10]}")
        return None

    total = (payload.get("pagination") or {}).get("totalCount")
    print(f"  [{SITE}] {len(items)} item(s) returned (totalCount={total})")

    # Exact domain match only — a substring test would accept blog.techloy.com.
    for item in items:
        if not isinstance(item, dict):
            continue
        if _normalize_domain(item.get("name") or "") != domain:
            continue

        print(f"  [{SITE}] matched item id={item.get('id')} name={item.get('name')!r}")
        found = _article_price(item)
        if found is None:
            names = [(p or {}).get("name") for p in (item.get("prices") or [])]
            print(f"  [{SITE}] no usable 'Article' price on the matched item "
                  f"(price entries: {names})")
            return None

        amount, currency, raw = found
        print(f"  [{SITE}] {domain} → {amount} {currency} (raw {raw!r})")
        return {"price": amount, "currency": currency, "raw": raw}

    names = [(_normalize_domain((i or {}).get("name") or "")) for i in items[:10]]
    print(f"  [{SITE}] no exact domain match for {domain!r} "
          f"(first names returned: {names})")
    return None


def get_price(magazine_domain: str, debug: bool = False) -> float | None:
    """
    Article price for `magazine_domain`, or None.

    Signature and float return are unchanged from the Playwright version so
    bulk_price_check.py keeps working untouched. Use get_price_detailed() when
    the currency is needed as well.
    """
    found = get_price_detailed(magazine_domain, debug=debug)
    return found["price"] if found else None
