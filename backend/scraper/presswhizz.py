"""
Scraper for app.presswhizz.com

Flow:
  1. Restore session (skip login if valid).
  2. If on login page, sign in and save session.
  3. Navigate to /marketplace.
  4. Under General Filters, type the bare domain (e.g. "thebrandhopper.com").
  5. Press Enter / wait for results.
  6. Find the result row whose site name matches the domain exactly.
  7. Click the "Offers" button on that row — a popup appears.
  8. From the popup, collect only "Guest Post" rows.
  9. Return the lowest price (USD int), or None if not found.
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from .browser import save_session, load_session_kwargs, clear_session, apply_default_timeouts, screenshot

BASE_URL  = "https://app.presswhizz.com"
LOGIN_URL = f"{BASE_URL}/login"
NAV_WAIT  = "load"

# Matches "$119.00" (prefix) or "1800.00 USD" (suffix)
_PRICE_RE = re.compile(
    r'[₪$£€]\s*(\d[\d,]*(?:\.\d{1,2})?)'          # prefix:  $119.00
    r'|(\d[\d, ]*(?:\.\d{1,2})?) *(?:USD|ILS|₪|\$|€|£)',  # suffix: 1 800.00 USD (spaces only)
    re.IGNORECASE,
)


def _wait(page, ms: int = 1500):
    page.wait_for_timeout(ms)


def _is_on_login(page) -> bool:
    if any(p in page.url for p in ('/login', '/signin', '/auth')):
        return True
    try:
        return bool(page.query_selector('input[type="password"]'))
    except Exception:
        return False


def _login(page, debug: bool):
    print("  [presswhizz] logging in…")
    if not page.query_selector('input[type="password"]'):
        page.goto(BASE_URL, wait_until=NAV_WAIT)
        _wait(page, 3000)
    screenshot(page, "pw_01_login", debug)

    email_sel = 'input[type="email"], input[name="email"], input[placeholder*="email" i]'
    page.wait_for_selector(email_sel, timeout=10000)
    page.fill(email_sel, os.environ["PRESSWHIZZ_EMAIL"])
    page.fill('input[type="password"]', os.environ["PRESSWHIZZ_PASSWORD"])
    screenshot(page, "pw_02_filled", debug)

    page.click('button[type="submit"], button:has-text("Login"), button:has-text("Sign in")')
    _wait(page, 3000)
    screenshot(page, "pw_03_after_login", debug)

    if _is_on_login(page):
        raise RuntimeError(
            "PressWhizz login failed — still on login page. "
            "Check PRESSWHIZZ_EMAIL / PRESSWHIZZ_PASSWORD in .env"
        )


def _normalize_domain(text: str) -> str:
    """Strip URL scheme, www, and trailing slash from a domain/URL string."""
    t = text.strip().lower()
    for prefix in ('https://', 'http://', 'www.'):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.rstrip('/')


def _fill_domain_filter(page, magazine_domain: str, debug: bool):
    """
    Fill the General Filters 'Enter Domain' / 'Portal' input with magazine_domain
    and click 'Apply Filters'.
    """
    # The domain/portal input on PressWhizz marketplace
    domain_input = None
    for sel in [
        'input[placeholder*="domain" i]',
        'input[placeholder*="portal" i]',
        'input[placeholder*="site name" i]',
        'input[placeholder*="site" i]',
        'input[placeholder*="url" i]',
        'input[placeholder*="search" i]',
        'input[type="search"]',
        'input[name*="domain" i]',
        'input[name*="search" i]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                domain_input = el
                if debug:
                    print(f"  [presswhizz] domain input found via: {sel}")
                break
        except Exception:
            pass

    if not domain_input:
        screenshot(page, "pw_no_domain_input", debug)
        if debug:
            (Path(__file__).parent / "debug_screenshots" / "pw_marketplace_text.txt").write_text(
                page.inner_text("body")[:8000]
            )
        return False

    domain_input.fill("")
    domain_input.fill(magazine_domain)
    _wait(page, 500)

    # Click "Apply Filters" button (preferred over pressing Enter)
    applied = False
    for sel in [
        'button:has-text("Apply Filters")',
        'button:has-text("Apply filters")',
        'button:has-text("Apply")',
        'a:has-text("Apply Filters")',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                applied = True
                if debug:
                    print(f"  [presswhizz] clicked Apply via: {sel}")
                break
        except Exception:
            pass

    if not applied:
        page.keyboard.press("Enter")
        if debug:
            print("  [presswhizz] pressed Enter to apply filter")

    return True


def _click_offers_for_domain(page, magazine_domain: str, debug: bool) -> bool:
    """
    Find the result row whose Portal cell matches magazine_domain and click Offers.

    PressWhizz renders domain links as <a href="https://domain.com"> with the
    domain text inside a child <span>, so inner_text() on the <a> is empty.
    We match by href instead, walk up to the containing row, and click the
    button whose exact text is "Offers" (not "My Publishing Offers").
    """
    domain_lower = magazine_domain.lower().strip()

    # Strategy 1: find <a href> whose href contains the domain, walk up to row,
    # click the button with exact text "Offers"
    for link in page.query_selector_all(f'a[href*="{magazine_domain}"]'):
        try:
            href = (link.get_attribute('href') or '').lower()
            if _normalize_domain(href) != domain_lower:
                continue
            row_el = link.evaluate_handle(
                'el => el.closest("tr") || el.closest("[class*=row]") || '
                'el.closest("[class*=item]") || el.closest("[class*=card]") || el.parentElement'
            )
            if not row_el:
                continue
            offers_el = row_el.evaluate_handle(
                '''el => {
                    if (!el) return null;
                    const all = el.querySelectorAll("button, a");
                    for (const x of all) {
                        if (x.textContent.trim() === "Offers") return x;
                    }
                    return null;
                }'''
            )
            if offers_el:
                try:
                    offers_el.click()
                    if debug:
                        print(f"  [presswhizz] clicked Offers via href match for {href}")
                    return True
                except Exception:
                    pass
        except Exception as e:
            if debug:
                print(f"  [presswhizz] strategy1 error: {e}")

    # Strategy 2: <tr>/<td> — match Portal cell by inner_text (text-based tables)
    for row in page.query_selector_all('tr'):
        cells = row.query_selector_all('td')
        if not cells:
            continue
        try:
            link = cells[0].query_selector('a')
            raw = link.inner_text() if link else cells[0].inner_text()
            if _normalize_domain(raw) != domain_lower:
                continue
            btn = row.query_selector('button:has-text("Offers")')
            if btn:
                btn.click()
                if debug:
                    print(f"  [presswhizz] clicked Offers via <tr> text match")
                return True
        except Exception:
            pass

    # Strategy 3: single result — click the only <button> whose exact text is "Offers"
    # (the filter already scoped results to this domain, so there should be just one)
    exact_btns = page.query_selector_all('button')
    for btn in exact_btns:
        try:
            if btn.inner_text().strip() == "Offers" and btn.is_visible():
                btn.click()
                if debug:
                    print(f"  [presswhizz] clicked sole Offers button (exact text)")
                return True
        except Exception:
            pass

    screenshot(page, "pw_no_offers_btn", debug)
    if debug:
        print(f"  [presswhizz] could not find Offers button for '{magazine_domain}'")
        (Path(__file__).parent / "debug_screenshots" / "pw_results_text.txt").write_text(
            page.inner_text("body")[:8000]
        )
    return False


def _parse_price_from_match(m) -> int | None:
    """Extract integer price from a _PRICE_RE match (handles two capture groups)."""
    raw = (m.group(1) or m.group(2) or '').replace(',', '').replace(' ', '')
    try:
        val = int(float(raw))
        return val if 10 < val < 100_000 else None
    except (ValueError, AttributeError):
        return None


def _extract_guest_post_prices(page, debug: bool) -> list[int]:
    """
    After the Offers panel opens, find all 'Guest Post' rows and extract prices.

    PressWhizz uses an inline slide-in panel anchored by 'Offers for:' heading.
    We read the full page text, slice the offers block, and parse Guest Post lines.
    """
    _wait(page, 2000)
    screenshot(page, "pw_06_offers_popup", debug)

    full_text = page.inner_text("body")

    if debug:
        (Path(__file__).parent / "debug_screenshots" / "pw_popup_text.txt").write_text(
            full_text[-5000:]
        )

    prices: list[int] = []
    lines = full_text.splitlines()

    # Find the offers panel block: starts at "Offers for:" line
    panel_start = None
    for i, line in enumerate(lines):
        if 'offers for:' in line.lower():
            panel_start = i
            break

    if panel_start is None:
        if debug:
            print("  [presswhizz] 'Offers for:' marker not found in page text — panel may not have opened")
        return prices

    # Take everything from "Offers for:" onward (max 200 lines)
    panel_lines = lines[panel_start:panel_start + 200]

    if debug:
        print(f"  [presswhizz] offers panel: {len(panel_lines)} lines")

    # Scan panel lines: find each "Guest Post" offer row and read its price
    i = 0
    while i < len(panel_lines):
        line = panel_lines[i].strip()
        # Lines starting with "Guest Post" are offer type rows
        if re.match(r'guest post', line, re.IGNORECASE):
            # Rules text spans several lines before the price; use a wide window
            chunk = '\t'.join(panel_lines[i:i + 15])
            for m in _PRICE_RE.finditer(chunk):
                val = _parse_price_from_match(m)
                if val:
                    prices.append(val)
                    break
        i += 1

    if debug:
        print(f"  [presswhizz] Guest Post prices from popup: {prices}")

    return prices


def get_price(magazine_domain: str, debug: bool = False) -> int | None:
    """
    Returns the lowest Guest Post price for magazine_domain on PressWhizz,
    or None if not found. Always closes the browser, even on error, so a
    failed run doesn't leak Chromium processes into the next run.
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


def _get_price_inner(pw, browser, magazine_domain: str, debug: bool, retried_login: bool = False) -> int | None:
    kwargs  = load_session_kwargs("presswhizz")
    context = browser.new_context(**kwargs)
    page    = context.new_page()
    apply_default_timeouts(context, page)

    # ── 1. Navigate to app ──────────────────────────────────────────────
    page.goto(BASE_URL, wait_until=NAV_WAIT)
    _wait(page, 2000)
    screenshot(page, "pw_00_home", debug)

    if _is_on_login(page):
        try:
            _login(page, debug)
        except (RuntimeError, PlaywrightTimeoutError) as e:
            # Session may have been a stale/corrupt cookie jar — clear it and
            # retry once with a guaranteed-fresh login before giving up.
            if not retried_login:
                print(f"  [presswhizz] login failed ({e}) — clearing session and retrying once")
                clear_session("presswhizz")
                context.close()
                return _get_price_inner(pw, browser, magazine_domain, debug, retried_login=True)
            raise
        save_session(context, "presswhizz")

    # ── 2. Go to Marketplace ────────────────────────────────────────────
    page.goto(f"{BASE_URL}/marketplace", wait_until=NAV_WAIT)
    _wait(page, 2500)
    screenshot(page, "pw_01_marketplace", debug)

    # ── 3. Fill domain in General Filters ──────────────────────────────
    filled = _fill_domain_filter(page, magazine_domain, debug)
    if not filled:
        screenshot(page, "pw_no_domain_input_FAILURE", True)
        raise RuntimeError(
            f"PressWhizz: could not find domain filter input on marketplace. "
            f"Check debug screenshots."
        )

    screenshot(page, "pw_02_filter_filled", debug)

    # Press Enter to trigger search / wait for results
    page.keyboard.press("Enter")
    _wait(page, 3500)
    screenshot(page, "pw_03_results", debug)

    # ── 4. Click "Offers" on the matching row ───────────────────────────
    found = _click_offers_for_domain(page, magazine_domain, debug)
    if not found:
        screenshot(page, "pw_no_offers_btn_FAILURE", True)
        return None

    # ── 5. Extract Guest Post prices from popup ─────────────────────────
    prices = _extract_guest_post_prices(page, debug)
    if not prices:
        screenshot(page, "pw_no_prices_FAILURE", True)

    return min(prices) if prices else None
