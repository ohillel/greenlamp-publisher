"""
Scraper for prnews.io

Flow:
  1. Restore session (skip login if valid).
  2. If on login page, sign in and save session.
  3. Click "Catalog" in the top navigation.
  4. Type the bare domain into the catalog search box.
  5. From the results, pick the ONE card matching BOTH:
       - the card's domain is an EXACT match ("tmcnet.com" yes,
         "it.tmcnet.com" / "sports.tmcnet.com" no)
       - the card's top-right tag reads exactly "Article"
         (not "Mention", "Contributor Post" or "Press Release")
  6. Return that card's price as EUR (float), or None if no card matches both.

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
from .eur_price import parse_eur

BASE_URL  = "https://prnews.io"
NAV_WAIT  = "load"

SITE = "prnews"

# The tag that qualifies a card. Compared case-insensitively against the
# element's exact trimmed text so "Press Release" can never satisfy it.
WANTED_TAG = "article"
REJECTED_TAGS = {"mention", "contributor post", "press release"}

_DOMAIN_TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+', re.IGNORECASE)


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
        # Try the usual entry points until a password field appears.
        for path in ("/login", "/signin", "/sign-in", "/"):
            try:
                page.goto(f"{BASE_URL}{path}", wait_until=NAV_WAIT)
                _wait(page, 2500)
                if page.query_selector('input[type="password"]'):
                    break
                # Some sites open the form from a header link.
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

    screenshot(page, "pn_01_login", debug)

    email_sel = 'input[type="email"], input[name="email"], input[name="login"], input[placeholder*="mail" i]'
    page.wait_for_selector(email_sel, timeout=10000)
    page.fill(email_sel, os.environ["PRNEWS_EMAIL"])
    page.fill('input[type="password"]', os.environ["PRNEWS_PASSWORD"])
    screenshot(page, "pn_02_filled", debug)

    page.click(
        'button[type="submit"], input[type="submit"], '
        'button:has-text("Log in"), button:has-text("Login"), button:has-text("Sign in")'
    )
    _wait(page, 4000)
    screenshot(page, "pn_03_after_login", debug)

    if _is_on_login(page):
        screenshot(page, "pn_login_FAILURE", True)
        raise RuntimeError(
            "prnews.io login failed — still on the login page. "
            "Check PRNEWS_EMAIL / PRNEWS_PASSWORD."
        )


# ── Catalog navigation ────────────────────────────────────────────────────────

def _open_catalog(page, debug: bool) -> bool:
    """Click "Catalog" in the top nav. Falls back to known catalog URLs."""
    for sel in ('a:has-text("Catalog")', 'button:has-text("Catalog")',
                'nav a:has-text("Catalog")', '[role="navigation"] a:has-text("Catalog")'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _wait(page, 3000)
                print(f"  [{SITE}] opened catalog via {sel!r}")
                return True
        except Exception:
            continue

    for path in ("/catalog", "/en/catalog", "/sites"):
        try:
            page.goto(f"{BASE_URL}{path}", wait_until=NAV_WAIT)
            _wait(page, 3000)
            if "catalog" in page.url.lower() or "sites" in page.url.lower():
                print(f"  [{SITE}] opened catalog via URL {path!r}")
                return True
        except Exception:
            continue

    screenshot(page, "pn_no_catalog_FAILURE", True)
    return False


def _search_domain(page, domain: str, debug: bool) -> bool:
    """Type the domain into the catalog search box and submit."""
    for sel in [
        'input[type="search"]',
        'input[placeholder*="search" i]',
        'input[placeholder*="domain" i]',
        'input[placeholder*="site" i]',
        'input[name*="search" i]',
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

    screenshot(page, "pn_no_search_input_FAILURE", True)
    return False


# ── Card matching ─────────────────────────────────────────────────────────────

def _card_domain_matches(card, domain: str) -> bool:
    """
    True only when the card names exactly `domain`.

    Subdomains must NOT match, so every domain-looking token in the card is
    normalized and compared for equality — a substring test would wrongly
    accept "it.tmcnet.com" for "tmcnet.com".
    """
    text = _safe_text(card)
    for token in _DOMAIN_TOKEN_RE.findall(text):
        if _normalize_domain(token) == domain:
            return True
    # Some cards put the domain only in a link href.
    try:
        for a in card.query_selector_all('a[href]'):
            href = a.get_attribute('href') or ""
            if _normalize_domain(href) == domain:
                return True
    except Exception:
        pass
    return False


def _card_tag_is_article(card) -> bool:
    """
    True when the card carries a tag whose exact text is "Article".

    Checked against each descendant's own trimmed text rather than the card's
    full text, so "Press Release" cannot satisfy it and a card merely
    mentioning the word "article" in prose is not accepted.
    """
    try:
        for el in card.query_selector_all('span, div, p, small, em, b, strong, li'):
            t = _safe_text(el).strip().lower()
            if not t or len(t) > 40:
                continue
            if t in REJECTED_TAGS:
                continue
            if t == WANTED_TAG:
                return True
    except Exception:
        pass
    return False


def _find_matching_card(page, domain: str, debug: bool):
    """The one card matching BOTH the exact domain and the "Article" tag."""
    selectors = [
        '[class*="card" i]',
        '[class*="item" i]',
        '[class*="result" i]',
        '[class*="site" i]',
        'article',
        'li',
    ]
    seen_domain_cards = 0
    for sel in selectors:
        try:
            cards = page.query_selector_all(sel)
        except Exception:
            continue
        for card in cards:
            try:
                if not _card_domain_matches(card, domain):
                    continue
                seen_domain_cards += 1
                if _card_tag_is_article(card):
                    print(f"  [{SITE}] matched card via {sel!r} (exact domain + Article tag)")
                    return card
            except Exception:
                continue
        if seen_domain_cards:
            # Exact-domain cards existed at this level but none was an Article.
            break

    if seen_domain_cards:
        print(f"  [{SITE}] {seen_domain_cards} exact-domain card(s) found, none tagged 'Article'")
    else:
        print(f"  [{SITE}] no card with an exact domain match for {domain!r}")
    screenshot(page, "pn_no_matching_card", debug)
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def get_price(magazine_domain: str, debug: bool = False) -> float | None:
    """
    EUR price for an "Article" placement on `magazine_domain` at prnews.io,
    or None when the catalog has no exactly-matching Article card.

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
    screenshot(page, "pn_00_home", debug)

    if _is_on_login(page):
        try:
            _login(page, debug)
        except (RuntimeError, PlaywrightTimeoutError) as e:
            # A stale/corrupt cookie jar looks exactly like a failed login —
            # clear it and retry once with a guaranteed-fresh login.
            if not retried_login:
                print(f"  [{SITE}] login failed ({e}) — clearing session and retrying once")
                clear_session(SITE)
                context.close()
                return _get_price_inner(pw, browser, magazine_domain, debug, retried_login=True)
            raise
        save_session(context, SITE)

    # ── 2. Catalog ──────────────────────────────────────────────────────
    if not _open_catalog(page, debug):
        raise RuntimeError("prnews.io: could not open the Catalog. Check debug screenshots.")
    screenshot(page, "pn_04_catalog", debug)

    # A session can expire into a login wall only once inside the catalog.
    if _is_on_login(page) and not retried_login:
        print(f"  [{SITE}] hit login wall inside catalog — clearing session and retrying once")
        clear_session(SITE)
        context.close()
        return _get_price_inner(pw, browser, magazine_domain, debug, retried_login=True)

    # ── 3. Search ───────────────────────────────────────────────────────
    if not _search_domain(page, domain, debug):
        raise RuntimeError("prnews.io: could not find the catalog search box. Check debug screenshots.")
    screenshot(page, "pn_05_results", debug)

    # ── 4. Pick the card ────────────────────────────────────────────────
    card = _find_matching_card(page, domain, debug)
    if card is None:
        return None

    # ── 5. Price ────────────────────────────────────────────────────────
    price = parse_eur(_safe_text(card))
    if price is None:
        screenshot(page, "pn_no_price_in_card_FAILURE", True)
        print(f"  [{SITE}] matched the card but found no EUR price in it")
        return None

    print(f"  [{SITE}] {domain} → {price} EUR")
    return price
