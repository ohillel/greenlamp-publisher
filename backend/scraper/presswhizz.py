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
from urllib.parse import urlencode
from .browser import save_session, load_session_kwargs, clear_session, apply_default_timeouts, screenshot, describe_inputs

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


def _marketplace_url_for(magazine_domain: str) -> str:
    """
    Marketplace URL pre-filtered to one portal.

    The site's own search writes the term into the `portalName` query
    parameter — visible in the address bar after using the magnifying glass in
    the Portal column header. Navigating straight to it is far more reliable
    than finding and driving that icon, and it survives the icon being
    restyled. _search_via_portal_icon is kept as a fallback.
    """
    params = {
        "offerTypesFilter": "all",
        "page": "1",
        "perPage": "25",
        "sortBy": "default_sort",
        "dir": "desc",
        "portalName": magazine_domain,
    }
    return f"{BASE_URL}/marketplace?{urlencode(params)}"


def _search_via_portal_icon(page, magazine_domain: str, debug: bool) -> bool:
    """
    Fallback: drive the magnifying glass in the Portal column header.

    Only used if the URL route above returns nothing, so the selectors here
    are allowed to be best-effort.
    """
    print(f"  [presswhizz] fallback: using the Portal column search icon")

    header = None
    for sel in ('th:has-text("Portal")', '[role="columnheader"]:has-text("Portal")',
                'th:has-text("Portal") , td:has-text("Portal")'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                header = el
                break
        except Exception:
            continue

    if header is not None:
        try:
            icon = header.query_selector(
                'button, [role="button"], svg, [class*="search" i], [class*="magnif" i], i'
            )
            if icon:
                icon.click()
                _wait(page, 1200)
                print("  [presswhizz] clicked the Portal header search icon")
        except Exception as e:
            print(f"  [presswhizz] could not click the Portal header icon: {e}")

    for sel in [
        'input[placeholder*="portal" i]',
        'input[placeholder*="domain" i]',
        'input[placeholder*="search" i]',
        'input[type="search"]',
        'input[name*="portal" i]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.fill("")
                el.fill(magazine_domain)
                _wait(page, 500)
                page.keyboard.press("Enter")
                _wait(page, 3500)
                print(f"  [presswhizz] fallback search submitted via {sel!r}")
                return True
        except Exception:
            continue

    print("  [presswhizz] fallback: no portal search input found")
    describe_inputs(page, "presswhizz")
    screenshot(page, "pw_no_portal_search_FAILURE", True)
    return False


def _fill_domain_filter(page, magazine_domain: str, debug: bool):
    """
    Fill the General Filters 'Enter Domain' / 'Portal' input with magazine_domain
    and click 'Apply Filters'.
    """
    print(f"  [presswhizz] filling domain filter with '{magazine_domain}'")
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
                print(f"  [presswhizz] domain input found via: {sel}")
                break
        except Exception:
            pass

    if not domain_input:
        print(f"  [presswhizz] FAILED to find a domain filter input — dumping visible <input> count: "
              f"{len(page.query_selector_all('input'))}")
        screenshot(page, "pw_no_domain_input", True)
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
                print(f"  [presswhizz] clicked Apply via: {sel}")
                break
        except Exception:
            pass

    if not applied:
        page.keyboard.press("Enter")
        print("  [presswhizz] no Apply button found — pressed Enter to apply filter")

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

    all_rows = page.query_selector_all('tr')
    all_links = page.query_selector_all(f'a[href*="{magazine_domain}"]')
    print(f"  [presswhizz] results page: {len(all_rows)} <tr> rows, "
          f"{len(all_links)} <a href> containing '{magazine_domain}'")

    # Strategy 1: find <a href> whose href contains the domain, walk up to row,
    # click the button with exact text "Offers"
    for link in all_links:
        try:
            href = (link.get_attribute('href') or '').lower()
            normalized_href = _normalize_domain(href)
            if normalized_href != domain_lower:
                print(f"  [presswhizz] strategy1: href '{href}' normalizes to "
                      f"'{normalized_href}' — does not match '{domain_lower}', skipping")
                continue
            row_el = link.evaluate_handle(
                'el => el.closest("tr") || el.closest("[class*=row]") || '
                'el.closest("[class*=item]") || el.closest("[class*=card]") || el.parentElement'
            )
            if not row_el:
                print(f"  [presswhizz] strategy1: matched href '{href}' but found no containing row")
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
                    print(f"  [presswhizz] clicked Offers via href match for {href}")
                    return True
                except Exception as e:
                    print(f"  [presswhizz] strategy1: found Offers button but click failed: {e}")
            else:
                row_text = page.evaluate('el => el ? el.innerText : ""', row_el)
                print(f"  [presswhizz] strategy1: matched row for '{href}' but no exact-text "
                      f"'Offers' button inside it. Row text: {row_text[:200]!r}")
        except Exception as e:
            print(f"  [presswhizz] strategy1 error: {e}")

    # Strategy 2: <tr>/<td> — match Portal cell by inner_text (text-based tables)
    checked_texts = []
    for row in all_rows:
        cells = row.query_selector_all('td')
        if not cells:
            continue
        try:
            link = cells[0].query_selector('a')
            raw = link.inner_text() if link else cells[0].inner_text()
            normalized_raw = _normalize_domain(raw)
            checked_texts.append(normalized_raw)
            if normalized_raw != domain_lower:
                continue
            btn = row.query_selector('button:has-text("Offers")')
            if btn:
                btn.click()
                print(f"  [presswhizz] clicked Offers via <tr> text match (cell text: {raw!r})")
                return True
            else:
                print(f"  [presswhizz] strategy2: row text '{raw}' matches domain but no "
                      f"'Offers' button found in that row")
        except Exception:
            pass
    if checked_texts:
        print(f"  [presswhizz] strategy2: first-cell texts seen across rows: {checked_texts[:20]}")

    # Strategy 3: single result — click the only <button> whose exact text is "Offers"
    # (the filter already scoped results to this domain, so there should be just one)
    exact_btns = page.query_selector_all('button')
    offer_like_texts = []
    for btn in exact_btns:
        try:
            text = btn.inner_text().strip()
        except Exception:
            continue
        if 'offer' in text.lower():
            offer_like_texts.append(text)
        if text == "Offers" and btn.is_visible():
            btn.click()
            print(f"  [presswhizz] clicked sole Offers button (exact text)")
            return True

    # Strategy 4 (fallback, logged loudly): click any visible button whose text
    # merely *contains* "offer" (case-insensitive) — covers label changes like
    # "View Offers" / "See Offers" without silently widening the exact-match
    # strategies above.
    for btn in exact_btns:
        try:
            text = btn.inner_text().strip()
            if 'offer' in text.lower() and btn.is_visible():
                btn.click()
                print(f"  [presswhizz] FALLBACK: clicked button with partial text match {text!r} "
                      f"(exact 'Offers' text not found — site label may have changed)")
                return True
        except Exception:
            pass

    print(f"  [presswhizz] could not find any Offers button for '{magazine_domain}'. "
          f"Buttons containing 'offer': {offer_like_texts[:20]}")
    screenshot(page, "pw_no_offers_btn", True)
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


def _is_guest_post_cell(text: str) -> bool:
    """
    True when an Offer Type cell is a Guest Post.

    The cell holds the type on its first line and any category tags
    (Crypto / Pharmacy / Loans) beneath it, so only the first line is
    examined. Tags do NOT disqualify an offer — the cheapest Guest Post wins
    whether or not it carries them. "Link Insert" must never match.
    """
    for line in (text or "").splitlines():
        line = line.strip().lower()
        if not line:
            continue
        return line.startswith("guest post")
    return False


def _price_from_cells(cells) -> int | None:
    """First parsable currency amount among a row's cells."""
    for cell in cells:
        try:
            text = cell.inner_text() or ""
        except Exception:
            continue
        for m in _PRICE_RE.finditer(text):
            val = _parse_price_from_match(m)
            if val:
                return val
    return None


def _guest_post_prices_from_table(page) -> list[int]:
    """
    Read Guest Post prices from the offers modal's table.

    The modal lists one row per offer with columns
    Offer Type | Included | Excluded | Duration | Price | Cart.
    Reading rows directly is safer than scanning page text: a text window that
    overruns a long Guest Post row would pick up the NEXT row's price, which
    may well be a Link Insert.
    """
    prices: list[int] = []
    seen_types: list[str] = []

    try:
        rows = page.query_selector_all('tr')
    except Exception:
        return prices

    for row in rows:
        try:
            cells = row.query_selector_all('td')
            if len(cells) < 2:
                continue
            offer_type = cells[0].inner_text() or ""
            first_line = next((l.strip() for l in offer_type.splitlines() if l.strip()), "")
            if first_line:
                seen_types.append(first_line)
            if not _is_guest_post_cell(offer_type):
                continue
            val = _price_from_cells(cells[1:])
            if val:
                prices.append(val)
                print(f"  [presswhizz] Guest Post row → {val} (type cell: {first_line!r})")
            else:
                print(f"  [presswhizz] Guest Post row with no parsable price: "
                      f"{row.inner_text()[:160]!r}")
        except Exception:
            continue

    if seen_types:
        print(f"  [presswhizz] offer types seen in the modal: {seen_types}")
    return prices


def _extract_guest_post_prices(page, debug: bool) -> list[int]:
    """
    All Guest Post prices from the Offers modal. Link Insert is never included,
    even when cheaper; the caller takes the minimum of what comes back.
    """
    _wait(page, 2500)
    screenshot(page, "pw_06_offers_popup", True)

    full_text = page.inner_text("body")
    print(f"  [presswhizz] offers modal: page text is {len(full_text)} chars")
    (Path(__file__).parent / "debug_screenshots" / "pw_popup_text.txt").write_text(
        full_text[-5000:]
    )

    # Preferred: read the modal's table rows.
    prices = _guest_post_prices_from_table(page)
    if prices:
        print(f"  [presswhizz] Guest Post prices from table rows: {prices}")
        return prices

    # Fallback: the older text-scanning approach, for a non-table layout.
    print("  [presswhizz] no Guest Post table rows — falling back to text scanning")
    return _guest_post_prices_from_text(page, full_text)


def _guest_post_prices_from_text(page, full_text: str) -> list[int]:
    """Text-scanning fallback for when the modal is not a <table>."""
    prices: list[int] = []
    lines = full_text.splitlines()

    panel_start = None
    for i, line in enumerate(lines):
        low = line.lower()
        if 'offers for:' in low or 'all offers' in low:
            panel_start = i
            break

    if panel_start is None:
        print("  [presswhizz] no offers-panel marker in page text — the modal may not have "
              f"opened. Last 600 chars: {full_text[-600:]!r}")
        return prices

    panel_lines = lines[panel_start:panel_start + 200]
    print(f"  [presswhizz] offers panel at line {panel_start}, scanning {len(panel_lines)} lines")

    guest_post_lines_found = 0
    for i, raw in enumerate(panel_lines):
        line = raw.strip()
        # Must be the START of the line so "Link Insert" can never match.
        if not re.match(r'guest post', line, re.IGNORECASE):
            continue
        guest_post_lines_found += 1
        chunk = '\t'.join(panel_lines[i:i + 15])
        for m in _PRICE_RE.finditer(chunk):
            val = _parse_price_from_match(m)
            if val:
                prices.append(val)
                break
        else:
            print(f"  [presswhizz] 'Guest Post' line at offset {i} had no parsable price. "
                  f"Chunk: {chunk[:200]!r}")

    print(f"  [presswhizz] found {guest_post_lines_found} 'Guest Post' line(s), "
          f"extracted prices: {prices}")
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

    # ── 2-3. Marketplace, pre-filtered to this portal ───────────────────
    # The site's own Portal search writes the term into ?portalName=, so we go
    # straight there instead of driving the magnifying-glass icon.
    search_url = _marketplace_url_for(magazine_domain)
    print(f"  [presswhizz] opening {search_url}")
    page.goto(search_url, wait_until=NAV_WAIT)
    _wait(page, 3500)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    screenshot(page, "pw_02_search_results", debug)

    # ── 4. Click "Offers" on the EXACT-domain row ───────────────────────
    # The filter is a substring match, so several portals can come back
    # (techloy.com, blog.techloy.com, techloy.com.ng). _click_offers_for_domain
    # normalises each row's href and accepts only an exact match.
    found = _click_offers_for_domain(page, magazine_domain, debug)

    if not found:
        # Fall back to the in-page search icon in case ?portalName= changes.
        print("  [presswhizz] no exact-domain row from the URL filter — trying the search icon")
        if _search_via_portal_icon(page, magazine_domain, debug):
            found = _click_offers_for_domain(page, magazine_domain, debug)

    if not found:
        screenshot(page, "pw_no_offers_btn_FAILURE", True)
        return None

    # ── 5. Cheapest Guest Post price from the Offers modal ──────────────
    prices = _extract_guest_post_prices(page, debug)
    if not prices:
        # No Guest Post offer => no price at all. Link Insert is never used.
        print(f"  [presswhizz] no Guest Post offer for {magazine_domain!r} — returning no price")
        screenshot(page, "pw_no_prices_FAILURE", True)
        return None

    cheapest = min(prices)
    print(f"  [presswhizz] {magazine_domain} → cheapest Guest Post {cheapest} of {sorted(prices)}")
    return cheapest
