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
    apply_default_timeouts, screenshot, describe_inputs,
)
from .eur_price import parse_eur

BASE_URL  = "https://prnews.io"
NAV_WAIT  = "load"

# "/sites/" is the real catalog (that is where the search box was seen);
# "/catalog" follows as a fallback. Whichever actually carries the search box
# wins — see _open_catalog.
CATALOG_PATHS = ("/sites/", "/sites", "/catalog", "/en/sites/")

# _open_catalog outcomes.
CATALOG_OK     = "ok"       # a catalog page is open
CATALOG_LOGIN  = "login"    # the site redirected us to the login page
CATALOG_FAILED = "failed"   # nothing loaded at all

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

    print(f"  [{SITE}] login page url={page.url!r}")

    email_sel = 'input[type="email"], input[name="email"], input[name="login"], input[placeholder*="mail" i]'
    try:
        page.wait_for_selector(email_sel, timeout=10000)
        print(f"  [{SITE}] email field found via {email_sel!r}")
    except Exception:
        # The real field names are unknown — dump them so the next run says.
        print(f"  [{SITE}] email field NOT found — dumping the login form:")
        describe_inputs(page, SITE)
        screenshot(page, "pn_login_form_FAILURE", True)
        raise
    page.fill(email_sel, os.environ["PRNEWS_EMAIL"])

    try:
        page.fill('input[type="password"]', os.environ["PRNEWS_PASSWORD"])
    except Exception:
        print(f"  [{SITE}] password field NOT found — dumping the login form:")
        describe_inputs(page, SITE)
        screenshot(page, "pn_login_form_FAILURE", True)
        raise
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

def _open_catalog(page, debug: bool) -> str:
    """
    Open the site catalog, reporting CATALOG_OK / CATALOG_LOGIN / CATALOG_FAILED.

    A redirect to /login is the only reliable signal that a login is needed:
    prnews.io's homepage is a public marketing page, so inferring auth state
    from it (as this scraper originally did) meant the login step never ran.
    Returning CATALOG_LOGIN stops here and lets the caller sign in, rather than
    navigating away from the login page.

    If a candidate loads but has no search box, the first such page is kept and
    CATALOG_OK returned, so the run fails at the search step — with its own
    screenshot and input dump — rather than here.
    """
    first_loaded = None

    for path in CATALOG_PATHS:
        try:
            page.goto(f"{BASE_URL}{path}", wait_until=NAV_WAIT)
            _wait(page, 3500)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        except Exception as e:
            print(f"  [{SITE}] {path!r} did not load: {e}")
            continue

        if _is_on_login(page):
            print(f"  [{SITE}] {path!r} redirected to the login page ({page.url!r}) — signing in")
            return CATALOG_LOGIN

        has_box = _find_search_input(page) is not None
        print(f"  [{SITE}] tried {path!r} → url={page.url!r} search_box={'yes' if has_box else 'no'}")
        if has_box:
            print(f"  [{SITE}] opened catalog via URL {path!r}")
            return CATALOG_OK
        if first_loaded is None:
            first_loaded = path

    for sel in ('a:has-text("Catalog")', 'button:has-text("Catalog")',
                'nav a:has-text("Catalog")', '[role="navigation"] a:has-text("Catalog")'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _wait(page, 3500)
                if _is_on_login(page):
                    print(f"  [{SITE}] {sel!r} led to the login page — signing in")
                    return CATALOG_LOGIN
                if _find_search_input(page) is not None:
                    print(f"  [{SITE}] opened catalog via {sel!r}")
                    return CATALOG_OK
        except Exception:
            continue

    if first_loaded is not None:
        # Keep going on a page that loaded; the search step reports what is there.
        try:
            page.goto(f"{BASE_URL}{first_loaded}", wait_until=NAV_WAIT)
            _wait(page, 3000)
        except Exception:
            pass
        print(f"  [{SITE}] no search box found on any candidate — continuing on {first_loaded!r} "
              f"so the search step can report the page contents")
        return CATALOG_OK

    screenshot(page, "pn_no_catalog_FAILURE", True)
    return CATALOG_FAILED


def _find_search_input(page):
    """
    The catalog's single wide search box (placeholder "Search").

    Falls back to any visible text input on the page, since the placeholder is
    the only thing distinguishing it and that is the part most likely to change.
    """
    for sel in [
        'input[placeholder="Search"]',
        'input[placeholder*="search" i]',
        'input[type="search"]',
        'input[placeholder*="domain" i]',
        'input[placeholder*="site" i]',
        'input[name*="search" i]',
        'input[name*="query" i]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue

    # Last resort: the first visible free-text input that is not a checkbox,
    # radio or hidden field.
    try:
        for el in page.query_selector_all('input'):
            t = (el.get_attribute('type') or 'text').lower()
            if t in ('checkbox', 'radio', 'hidden', 'submit', 'button'):
                continue
            if el.is_visible():
                return el
    except Exception:
        pass
    return None


def _search_domain(page, domain: str, debug: bool) -> bool:
    """Type the domain into the catalog search box, submit, wait for results."""
    el = _find_search_input(page)
    if el is None:
        screenshot(page, "pn_no_search_input_FAILURE", True)
        describe_inputs(page, SITE)
        return False

    try:
        el.click()
        el.fill("")
        el.fill(domain)
        _wait(page, 600)
        page.keyboard.press("Enter")
        # Results re-render in place; give the grid time to swap.
        _wait(page, 4500)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        print(f"  [{SITE}] searched {domain!r}")
        return True
    except Exception as e:
        print(f"  [{SITE}] could not type into the search box: {e}")
        screenshot(page, "pn_search_failed_FAILURE", True)
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


def _card_tags(card) -> set[str]:
    """
    The short tag-like texts inside a card, lowercased.

    Read from each descendant's own trimmed text rather than the card's full
    text, so "Press Release" cannot be mistaken for "Article" and a card merely
    using the word "article" in a sentence is not counted.
    """
    tags: set[str] = set()
    try:
        for el in card.query_selector_all('span, div, p, small, em, b, strong, li'):
            t = _safe_text(el).strip().lower()
            if t and len(t) <= 40:
                tags.add(t)
    except Exception:
        pass
    return tags


def _card_tag_is_article(card) -> bool:
    """
    True when the card is tagged "Article" and carries no competing tag.

    The second half matters: a grid wrapper holding every result would contain
    an "Article" tag somewhere as well as "Press Release" and "Contributor
    Post", and would otherwise pass — handing back a price from the wrong card.
    """
    tags = _card_tags(card)
    if WANTED_TAG not in tags:
        return False
    return not (tags & REJECTED_TAGS)


def _find_matching_card(page, domain: str, debug: bool):
    """
    The one card matching BOTH the exact domain and the "Article" tag.

    Candidates are gathered from several selectors and the SMALLEST one wins.
    Broad selectors also match the container holding every result, and that
    container contains the domain too — picking the shortest text keeps the
    individual card rather than its ancestor.
    """
    selectors = [
        '[class*="card" i]',
        '[class*="item" i]',
        '[class*="result" i]',
        '[class*="site" i]',
        'article',
        'li',
    ]

    domain_cards = 0
    best = None
    best_len = None
    seen_tags: set[str] = set()

    for sel in selectors:
        try:
            cards = page.query_selector_all(sel)
        except Exception:
            continue
        for card in cards:
            try:
                if not _card_domain_matches(card, domain):
                    continue
                domain_cards += 1
                seen_tags |= (_card_tags(card) & (REJECTED_TAGS | {WANTED_TAG}))
                if not _card_tag_is_article(card):
                    continue
                text_len = len(_safe_text(card))
                if best_len is None or text_len < best_len:
                    best, best_len = card, text_len
            except Exception:
                continue

    if best is not None:
        print(f"  [{SITE}] matched card (exact domain + Article tag, {best_len} chars)")
        return best

    if domain_cards:
        print(f"  [{SITE}] {domain_cards} exact-domain card(s) found, none tagged 'Article' "
              f"(tags seen: {sorted(seen_tags) or 'none'})")
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

    # ── 1. Catalog, signing in if the site sends us to the login page ───
    # Go straight to the protected catalog rather than probing the homepage:
    # prnews.io's homepage is public, so it never reveals whether we are signed
    # in. The /login redirect is the real signal.
    status = _open_catalog(page, debug)

    if status == CATALOG_LOGIN:
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

        # Signed in — open the catalog again, following the redirect back.
        status = _open_catalog(page, debug)
        if status == CATALOG_LOGIN:
            screenshot(page, "pn_still_login_after_auth_FAILURE", True)
            if not retried_login:
                print(f"  [{SITE}] still redirected to login after signing in — "
                      f"clearing session and retrying once")
                clear_session(SITE)
                context.close()
                return _get_price_inner(pw, browser, magazine_domain, debug, retried_login=True)
            raise RuntimeError(
                "prnews.io: still redirected to the login page after signing in. "
                "The session is not being accepted — check debug screenshots."
            )

    if status == CATALOG_FAILED:
        raise RuntimeError("prnews.io: could not open the Catalog. Check debug screenshots.")
    screenshot(page, "pn_04_catalog", debug)

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
