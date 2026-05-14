"""
Check publication status for articles on PressWhizz.

Flow:
  1. Restore session (login if needed).
  2. Navigate to My Orders page (via sidebar or direct URL).
  3. Parse every order row on every page: domain + status text.
  4. For each article, match by magazine domain (+ client name when there are
     multiple orders for the same domain).
  5. Return status: 'published', 'not_published', or None (no change / not found).

Status mapping:
  "Completed"          → "published"
  "Publisher rejected" → "not_published"
  anything else        → None (order still in progress)
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright
from .browser import save_session, load_session_kwargs, screenshot

BASE_URL = "https://app.presswhizz.com"
NAV_WAIT = "load"

# Keys must match the lowercased status text from the orders table
_STATUS_MAP = {
    'completed':          'published',
    'publisher rejected': 'not_published',
    'rejected':           'not_published',
}


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
    print("  [check_presswhizz] logging in…")
    if not page.query_selector('input[type="password"]'):
        page.goto(BASE_URL, wait_until=NAV_WAIT)
        _wait(page, 3000)
    email_sel = 'input[type="email"], input[name="email"], input[placeholder*="email" i]'
    page.wait_for_selector(email_sel, timeout=10000)
    page.fill(email_sel, os.environ["PRESSWHIZZ_EMAIL"])
    page.fill('input[type="password"]', os.environ["PRESSWHIZZ_PASSWORD"])
    page.click('button[type="submit"], button:has-text("Login"), button:has-text("Sign in")')
    _wait(page, 3000)
    if _is_on_login(page):
        raise RuntimeError("PressWhizz login failed — check PRESSWHIZZ_EMAIL / PRESSWHIZZ_PASSWORD")


def _normalize_domain(text: str) -> str:
    t = text.strip().lower()
    for prefix in ('https://', 'http://', 'www.'):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.rstrip('/')


def _navigate_to_orders(page, debug: bool) -> bool:
    """Navigate to the My Orders page. Returns True on success."""
    # Known URL — confirmed from live nav inspection
    page.goto(f"{BASE_URL}/client/orders", wait_until=NAV_WAIT)
    _wait(page, 2500)
    screenshot(page, "cpw_01_orders", debug)

    if not _is_on_login(page) and 'order' in page.url.lower():
        return True

    # Fallback: click the sidebar link in case the URL ever changes
    for selector in [
        'a[href*="/client/orders"]',
        'a:has-text("My Orders")',
        'nav a:has-text("Orders")',
    ]:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                _wait(page, 2500)
                screenshot(page, "cpw_01_orders", debug)
                if not _is_on_login(page):
                    return True
        except Exception:
            pass

    screenshot(page, "cpw_orders_not_found", debug)
    if debug:
        (Path(__file__).parent / "debug_screenshots" / "cpw_orders_text.txt").write_text(
            page.inner_text("body")[:6000]
        )
    return False


def _map_status_text(text: str) -> str | None:
    """Convert raw status cell text to 'published' / 'not_published' / None."""
    lower = text.strip().lower()
    for key, val in _STATUS_MAP.items():
        if key in lower:
            return val
    return None


def _parse_orders_from_page(page, debug: bool) -> list[dict]:
    """
    Return all order rows on the current page as:
      [{
        'portal':      str,   # magazine domain (Portal column)
        'client_dom':  str,   # client domain   (Domain column, e.g. "mstone.ai")
        'status':      str|None,
      }, ...]

    Confirmed column layout (from live screenshot):
      td[0]  View Order button
      td[1]  Status badge  ("Completed", "Publisher working on it", …)
      td[2]  Domain        (the CLIENT's domain, e.g. "mstone.ai")
      td[3]  Created date
      td[4]  Updated date
      td[5]  Offer type    ("GP")
      td[6]  Price
      td[7]  Portal        (magazine URL, e.g. "https://thinkml.ai")
      td[8]  Guest Post URL (article link, may be absent)
    """
    orders: list[dict] = []

    if debug:
        (Path(__file__).parent / "debug_screenshots" / "cpw_orders_text.txt").write_text(
            page.inner_text("body")[:8000]
        )

    for row in page.query_selector_all('tr'):
        cells = row.query_selector_all('td')
        if len(cells) < 8:
            continue

        # ── Status (td[1]) ──────────────────────────────────────────────────
        status_text = cells[1].inner_text().strip()
        status = _map_status_text(status_text)
        # Skip rows whose status can never be actionable (no badge text at all)
        if not status_text:
            continue

        # ── Client domain (td[2]) ────────────────────────────────────────────
        client_dom = _normalize_domain(cells[2].inner_text().strip())

        # ── Portal / magazine (td[7]) ────────────────────────────────────────
        portal_cell = cells[7]
        portal_link = portal_cell.query_selector('a')
        portal_raw  = (portal_link.get_attribute('href') or portal_link.inner_text()
                       if portal_link else portal_cell.inner_text()).strip()
        portal = _normalize_domain(portal_raw)

        if not portal:
            continue

        orders.append({
            'portal':     portal,
            'client_dom': client_dom,
            'status':     status,       # None = in-progress, 'published'/'not_published' = settled
        })

    if debug:
        print(f"  [check_presswhizz] parsed {len(orders)} orders on this page")
        for o in orders[:5]:
            print(f"    portal={o['portal']!r:30} client={o['client_dom']!r:15} status={o['status']!r}")

    return orders


def _collect_all_orders(page, debug: bool) -> list[dict]:
    """Collect orders across all pages (up to 10 pages)."""
    all_orders: list[dict] = []

    for page_num in range(1, 11):
        orders = _parse_orders_from_page(page, debug)
        all_orders.extend(orders)

        if debug:
            print(f"  [check_presswhizz] page {page_num}: {len(orders)} rows, "
                  f"total so far: {len(all_orders)}")

        # Try to click "Next page" / ">" pagination button
        advanced = False
        for sel in [
            'a[rel="next"]',
            'button[aria-label*="next" i]',
            'a:has-text("Next")',
            'li.next a',
            '[class*="pagination"] a:has-text("›")',
            '[class*="pagination"] a:has-text(">")',
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible() and not btn.is_disabled():
                    btn.click()
                    _wait(page, 2000)
                    advanced = True
                    break
            except Exception:
                pass

        if not advanced:
            break  # no more pages

    return all_orders


def check_batch(
    articles: list[dict],
    debug: bool = False,
) -> dict[str, str]:
    """
    Check publication status for a batch of PressWhizz articles.

    articles: list of {'id': str, 'magazine': str, 'client_name': str}
    Returns: dict mapping article_id → 'published' | 'not_published'
             (only articles whose status should change are included)
    """
    results: dict[str, str] = {}
    if not articles:
        return results

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        kwargs  = load_session_kwargs("presswhizz")
        context = browser.new_context(**kwargs)
        page    = context.new_page()

        page.goto(BASE_URL, wait_until=NAV_WAIT)
        _wait(page, 2000)
        screenshot(page, "cpw_00_home", debug)

        if _is_on_login(page):
            _login(page, debug)
            save_session(context, "presswhizz")

        if not _navigate_to_orders(page, debug):
            print("  [check_presswhizz] could not navigate to My Orders — skipping batch")
            browser.close()
            return results

        all_orders = _collect_all_orders(page, debug)
        browser.close()

    if debug:
        print(f"  [check_presswhizz] total orders collected: {len(all_orders)}")

    for article in articles:
        article_id    = article['id']
        magazine      = _normalize_domain(article['magazine'])
        client_lower  = article['client_name'].lower()
        client_parts  = [p for p in re.split(r'[\s._-]+', client_lower) if len(p) > 3]

        # Match by Portal (magazine) column — primary key
        matches = [o for o in all_orders if o['portal'] == magazine]

        if not matches:
            if debug:
                print(f"  [check_presswhizz] no order found for portal '{magazine}'")
            continue

        # Narrow by client: Domain column should contain the client name/domain
        if len(matches) > 1 and client_parts:
            preferred = [
                m for m in matches
                if any(p in m['client_dom'] for p in client_parts)
            ]
            if preferred:
                matches = preferred

        order  = matches[0]
        mapped = order['status']  # None = in-progress

        if mapped in ('published', 'not_published'):
            results[article_id] = mapped
            if debug:
                print(f"  [check_presswhizz] article {article_id} portal={magazine!r} "
                      f"client_dom={order['client_dom']!r}: → {mapped}")
        elif debug:
            print(f"  [check_presswhizz] article {article_id} portal={magazine!r}: "
                  f"status not settled — no change")

    return results
