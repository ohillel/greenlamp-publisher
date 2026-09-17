"""
Scraper for collaborator.pro

Flow:
  1. Restore session (skip login if valid).
  2. If on login page, sign in and save session.
  3. Click "Catalog of websites" in the left sidebar.
  4. Type the bare domain into the "Search by domain" field.
  5. Pick the row whose domain is an EXACT match ("techloy.com" yes,
     "blog.techloy.com" no).
  6. Read the Price column. Each row shows TWO amounts — a large one
     ("160.95 EUR") and a smaller one beneath it ("+63.65 EUR"). Take the
     large one; ignore the "+" amount and any discount badge ("-10%").
  7. Return the price as EUR (float), or None if there is no exact match.

Prices stay in EUR — nothing here converts to USD.

Not tied to a client/project: prices are the same whoever is asking, so
get_price() takes only the domain (unlike linksme.get_price).

NOTE: the selectors below were written without access to the live site, so
each step tries several candidates and falls back to text matching. Every
failure path saves a screenshot to scraper/debug_screenshots/ so a wrong
guess can be corrected from the artefacts of a real run.
"""
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from .browser import (
    save_session, load_session_kwargs, clear_session,
    apply_default_timeouts, screenshot,
)
from .eur_price import parse_all_eur, normalize_amount

BASE_URL = "https://collaborator.pro"
NAV_WAIT = "load"

SITE = "collaborator"

_DOMAIN_TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+', re.IGNORECASE)

# "+63.65" / "+ 63,65" — the secondary amount shown under the main price.
_ADDON_RE = re.compile(r'\+\s*\d[\d\s., ]*')
# "-10%" / "10 %" — discount badges that must never be read as a price.
_PERCENT_RE = re.compile(r'[-+]?\s*\d[\d\s.,]*\s*%')


def _normalize_domain(raw: str) -> str:
    """Reduce any URL/domain string to a bare lowercase domain."""
    s = (raw or "").strip().lower()
    for scheme in ("https://", "http://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
    if s.startswith("www."):
        s = s[4:]
    return s.split("/")[0].strip()


def _wait(page, ms: int = 1500):
    page.wait_for_timeout(ms)


def _safe_text(el) -> str:
    try:
        return el.inner_text() or ""
    except Exception:
        return ""


def _is_on_login(page) -> bool:
    if any(p in page.url for p in ('/login', '/signin', '/auth', '/sign-in')):
        return True
    try:
        return bool(page.query_selector('input[type="password"]'))
    except Exception:
        return False


# ── Login ─────────────────────────────────────────────────────────────────────

def _login(page, debug: bool):
    print(f"  [{SITE}] logging in…")
    if not page.query_selector('input[type="password"]'):
        for path in ("/login", "/signin", "/sign-in", "/"):
            try:
                page.goto(f"{BASE_URL}{path}", wait_until=NAV_WAIT)
                _wait(page, 2500)
                if page.query_selector('input[type="password"]'):
                    break
                for sel in ('a:has-text("Log in")', 'a:has-text("Sign in")',
                            'button:has-text("Log in")', 'button:has-text("Sign in")'):
                    el = page.query_selector(sel)
                    if el:
                        el.click()
                        _wait(page, 2000)
                        break
                if page.query_selector('input[type="password"]'):
                    break
            except Exception:
                continue

    screenshot(page, "cl_01_login", debug)

    email_sel = 'input[type="email"], input[name="email"], input[name="login"], input[placeholder*="mail" i]'
    page.wait_for_selector(email_sel, timeout=10000)
    page.fill(email_sel, os.environ["COLLABORATOR_EMAIL"])
    page.fill('input[type="password"]', os.environ["COLLABORATOR_PASSWORD"])
    screenshot(page, "cl_02_filled", debug)

    page.click(
        'button[type="submit"], input[type="submit"], '
        'button:has-text("Log in"), button:has-text("Login"), button:has-text("Sign in")'
    )
    _wait(page, 4000)
    screenshot(page, "cl_03_after_login", debug)

    if _is_on_login(page):
        screenshot(page, "cl_login_FAILURE", True)
        raise RuntimeError(
            "collaborator.pro login failed — still on the login page. "
            "Check COLLABORATOR_EMAIL / COLLABORATOR_PASSWORD."
        )


# ── Catalog navigation ────────────────────────────────────────────────────────

def _open_catalog(page, debug: bool) -> bool:
    """Click "Catalog of websites" in the left sidebar; fall back to URLs."""
    for sel in ('a:has-text("Catalog of websites")', 'button:has-text("Catalog of websites")',
                'a:has-text("Catalog")', 'button:has-text("Catalog")',
                '[class*="sidebar" i] a:has-text("Catalog")'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _wait(page, 3500)
                print(f"  [{SITE}] opened catalog via {sel!r}")
                return True
        except Exception:
            continue

    for path in ("/catalog", "/en/catalog", "/websites", "/sites"):
        try:
            page.goto(f"{BASE_URL}{path}", wait_until=NAV_WAIT)
            _wait(page, 3500)
            url = page.url.lower()
            if any(k in url for k in ("catalog", "websites", "sites")):
                print(f"  [{SITE}] opened catalog via URL {path!r}")
                return True
        except Exception:
            continue

    screenshot(page, "cl_no_catalog_FAILURE", True)
    return False


def _search_domain(page, domain: str, debug: bool) -> bool:
    """Type the domain into the "Search by domain" field and submit."""
    for sel in [
        'input[placeholder*="search by domain" i]',
        'input[placeholder*="domain" i]',
        'input[name*="domain" i]',
        'input[type="search"]',
        'input[placeholder*="search" i]',
        'input[name*="query" i]',
    ]:
        try:
            page.wait_for_selector(sel, timeout=3000, state="visible")
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.fill("")
                el.fill(domain)
                _wait(page, 500)
                page.keyboard.press("Enter")
                _wait(page, 4000)
                print(f"  [{SITE}] searched {domain!r} via {sel!r}")
                return True
        except Exception:
            continue

    screenshot(page, "cl_no_search_input_FAILURE", True)
    return False


# ── Row matching + price ──────────────────────────────────────────────────────

def _row_domain_matches(row, domain: str) -> bool:
    """True only for an exact domain match — subdomains must not qualify."""
    text = _safe_text(row)
    for token in _DOMAIN_TOKEN_RE.findall(text):
        if _normalize_domain(token) == domain:
            return True
    try:
        for a in row.query_selector_all('a[href]'):
            if _normalize_domain(a.get_attribute('href') or "") == domain:
                return True
    except Exception:
        pass
    return False


def _large_price_from_cell(cell_text: str) -> float | None:
    """
    The main (large) price from a Price cell.

    The cell also carries a secondary "+63.65 EUR" amount and sometimes a
    "-10%" badge. Both are stripped before parsing, so what remains is the
    headline price regardless of the order they appear in.
    """
    if not cell_text:
        return None
    cleaned = _PERCENT_RE.sub(" ", cell_text)
    cleaned = _ADDON_RE.sub(" ", cleaned)
    prices = parse_all_eur(cleaned)
    if prices:
        return prices[0]

    # Fallback: the currency marker may sit outside the number's element, so
    # retry on bare numbers once the add-on and percentage have been removed.
    for m in re.finditer(r'\d[\d\s., ]*\d|\d', cleaned):
        val = normalize_amount(m.group(0))
        if val is not None and 1 <= val <= 100_000:
            return round(val, 2)
    return None


def _price_for_row(row, debug: bool) -> float | None:
    """Read the Price column of a matched row."""
    # Prefer a cell that actually looks like the price column.
    try:
        cells = row.query_selector_all('td')
    except Exception:
        cells = []

    candidates = []
    for c in cells:
        t = _safe_text(c)
        if 'EUR' in t.upper() or '€' in t:
            candidates.append(t)
    # Right-most currency cell is the Price column on this layout.
    for t in reversed(candidates):
        val = _large_price_from_cell(t)
        if val is not None:
            return val

    # No <td> matched (card/grid layout) — fall back to the whole row.
    return _large_price_from_cell(_safe_text(row))


def _find_matching_row(page, domain: str, debug: bool):
    """The row whose domain is an exact match for `domain`."""
    for sel in ('tr', '[class*="row" i]', '[class*="item" i]', '[class*="card" i]', 'li'):
        try:
            rows = page.query_selector_all(sel)
        except Exception:
            continue
        for row in rows:
            try:
                if _row_domain_matches(row, domain):
                    print(f"  [{SITE}] matched row via {sel!r} (exact domain)")
                    return row
            except Exception:
                continue

    print(f"  [{SITE}] no row with an exact domain match for {domain!r}")
    screenshot(page, "cl_no_matching_row", debug)
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def get_price(magazine_domain: str, debug: bool = False) -> float | None:
    """
    EUR price for `magazine_domain` on collaborator.pro, or None when the
    catalog has no exactly-matching domain.

    Always closes the browser, even on error, so a failed run does not leak
    Chromium processes into the next one.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            return _get_price_inner(pw, browser, magazine_domain, debug)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _get_price_inner(pw, browser, magazine_domain: str, debug: bool,
                     retried_login: bool = False) -> float | None:
    domain = _normalize_domain(magazine_domain)
    print(f"  [{SITE}] looking up {domain!r}")

    kwargs  = load_session_kwargs(SITE)
    context = browser.new_context(**kwargs)
    page    = context.new_page()
    apply_default_timeouts(context, page)

    # ── 1. Navigate ─────────────────────────────────────────────────────
    page.goto(BASE_URL, wait_until=NAV_WAIT)
    _wait(page, 2000)
    screenshot(page, "cl_00_home", debug)

    if _is_on_login(page):
        try:
            _login(page, debug)
        except (RuntimeError, PlaywrightTimeoutError) as e:
            if not retried_login:
                print(f"  [{SITE}] login failed ({e}) — clearing session and retrying once")
                clear_session(SITE)
                context.close()
                return _get_price_inner(pw, browser, magazine_domain, debug, retried_login=True)
            raise
        save_session(context, SITE)

    # ── 2. Catalog ──────────────────────────────────────────────────────
    if not _open_catalog(page, debug):
        raise RuntimeError("collaborator.pro: could not open the website catalog. Check debug screenshots.")
    screenshot(page, "cl_04_catalog", debug)

    if _is_on_login(page) and not retried_login:
        print(f"  [{SITE}] hit login wall inside catalog — clearing session and retrying once")
        clear_session(SITE)
        context.close()
        return _get_price_inner(pw, browser, magazine_domain, debug, retried_login=True)

    # ── 3. Search ───────────────────────────────────────────────────────
    if not _search_domain(page, domain, debug):
        raise RuntimeError("collaborator.pro: could not find the 'Search by domain' field. Check debug screenshots.")
    screenshot(page, "cl_05_results", debug)

    # ── 4. Pick the row ─────────────────────────────────────────────────
    row = _find_matching_row(page, domain, debug)
    if row is None:
        return None

    # ── 5. Price ────────────────────────────────────────────────────────
    price = _price_for_row(row, debug)
    if price is None:
        screenshot(page, "cl_no_price_in_row_FAILURE", True)
        print(f"  [{SITE}] matched the row but found no EUR price in it")
        return None

    print(f"  [{SITE}] {domain} → {price} EUR")
    return price
