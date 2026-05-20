"""
Scraper for app.links.me

Flow:
  1. Restore session (skip login if valid).
  2. If on login page, sign in and save session.
  3. On the dashboard, find the project whose name matches client_name.
  4. Navigate to that project's Guest Posting Sites List page.
  5. Click Filter, scroll to "Site Name or Keyword", enter the domain, click Apply.
  6. Read the Price column — return the USD price found, or None if no match.
  7. If no project matches client_name, return None (don't raise).
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright
from .browser import save_session, load_session_kwargs, screenshot

BASE_URL  = "https://app.links.me"
LOGIN_URL = f"{BASE_URL}/login"
NAV_WAIT  = "load"

_DOMAIN_RE = re.compile(r'^[a-z0-9][a-z0-9\-]*\.[a-z]{2,}$', re.IGNORECASE)

# Matches prices like "1 800.00 USD", "$240", "₪500", "60.00 USD"
_PRICE_RE = re.compile(
    r'(?:[₪$£€]\s*)?(\d[\d\s,]*(?:\.\d{1,2})?)\s*(?:USD|ILS|₪|\$|€|£)',
    re.IGNORECASE,
)


def _normalize_domain(raw: str) -> str:
    """
    Reduce any URL or domain string to a bare lowercase domain.
    Handles full URLs (https://www.blackdown.org/), www-prefixed domains,
    and domains with trailing slashes or paths.
    Examples:
      "https://www.blackdown.org/" → "blackdown.org"
      "www.blackdown.org"          → "blackdown.org"
      "blackdown.org"              → "blackdown.org"
    """
    s = raw.strip().lower()
    for scheme in ("https://", "http://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
    if s.startswith("www."):
        s = s[4:]
    return s.split("/")[0].strip()

_NAV_LABELS = {
    'Budget', 'Report', 'Guest Posting Sites List', 'Link Insertion Sites List',
    'Articles and links', 'Favorites', 'Stop List', 'Forum links',
    'Profile links', 'Profile  links', 'Tier 2–3 links', 'Articles submission',
    'Shared access', 'Contact us', 'Dashboard', 'White Niche', 'Best offers',
    'View', 'Export', 'Filter', 'Reset', 'Subscribe',
}


def _wait(page, ms: int = 1500):
    page.wait_for_timeout(ms)


def _safe_text(el) -> str:
    try:
        return el.inner_text() or ""
    except Exception:
        return ""


def _is_on_login(page) -> bool:
    if any(p in page.url for p in ('/login', '/signin', '/auth')):
        return True
    try:
        return bool(page.query_selector('input[type="password"]'))
    except Exception:
        return False


# ── Login ──────────────────────────────────────────────────────────────────────

def _login(page, debug: bool):
    print("  [linksme] logging in…")
    if not page.query_selector('input[type="password"]'):
        page.goto(LOGIN_URL, wait_until=NAV_WAIT)
        _wait(page, 3000)
    screenshot(page, "lm_01_login", debug)

    email_sel = 'input[type="email"], input[name="email"], input[placeholder*="email" i]'
    page.wait_for_selector(email_sel, timeout=10000)
    page.fill(email_sel, os.environ["LINKSME_EMAIL"])

    pw_sel = 'input[type="password"], input[name="password"]'
    try:
        page.wait_for_selector(pw_sel, timeout=5000)
        page.fill(pw_sel, os.environ["LINKSME_PASSWORD"])
    except Exception:
        for inp in page.query_selector_all('input'):
            t = (inp.get_attribute('type') or '').lower()
            p = (inp.get_attribute('placeholder') or '').lower()
            if t == 'password' or 'password' in p:
                inp.fill(os.environ["LINKSME_PASSWORD"])
                break

    screenshot(page, "lm_02_filled", debug)
    page.click('button[type="submit"], button:has-text("Login"), button:has-text("Sign in")')
    _wait(page, 3000)
    screenshot(page, "lm_03_after_login", debug)

    if _is_on_login(page):
        raise RuntimeError(
            "Links.me login failed — still on login page. "
            "Check LINKSME_EMAIL / LINKSME_PASSWORD in .env"
        )


# ── Project discovery ──────────────────────────────────────────────────────────

def _parse_project_names(page) -> list[str]:
    """
    Extract project domain names from the dashboard body text.
    Both the project name list and the catalog hrefs are in the same order.
    """
    body = page.inner_text("body")
    seen: set[str] = set()
    names: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if (
            _DOMAIN_RE.match(line)
            and line not in _NAV_LABELS
            and line.lower() not in seen
        ):
            seen.add(line.lower())
            names.append(line)
    return names


def _find_guest_posting_url(page, client_name: str, debug: bool) -> str | None:
    """
    Map client_name → Guest Posting Sites List URL by position-matching
    the ordered project name list against the ordered catalog hrefs.
    """
    screenshot(page, "lm_04_projects_list", debug)

    gp_hrefs = page.eval_on_selector_all(
        'a[href*="/catalog/guest-posting"]',
        'els => els.map(e => e.href)',
    )
    project_names = _parse_project_names(page)

    if debug:
        print(f"  [linksme] {len(project_names)} projects, {len(gp_hrefs)} catalog links")
        print(f"  [linksme] projects: {project_names[:10]}")
        (Path(__file__).parent / "debug_screenshots" / "lm_projects_text.txt").write_text(
            page.inner_text("body")[:5000]
        )

    name_lower = client_name.lower()

    # Exact match
    for i, pname in enumerate(project_names):
        if name_lower == pname.lower() and i < len(gp_hrefs):
            if debug:
                print(f"  [linksme] exact match: {pname} → {gp_hrefs[i]}")
            return gp_hrefs[i]

    # Partial match (client_name in project or vice versa)
    for i, pname in enumerate(project_names):
        if (name_lower in pname.lower() or pname.lower() in name_lower) and i < len(gp_hrefs):
            if debug:
                print(f"  [linksme] partial match: {pname} → {gp_hrefs[i]}")
            return gp_hrefs[i]

    # Fuzzy: any word > 3 chars
    parts = [p for p in re.split(r'[\s._-]+', name_lower) if len(p) > 3]
    for i, pname in enumerate(project_names):
        if any(p in pname.lower() for p in parts) and i < len(gp_hrefs):
            if debug:
                print(f"  [linksme] fuzzy match '{client_name}' → {pname} → {gp_hrefs[i]}")
            return gp_hrefs[i]

    if debug:
        print(f"  [linksme] no match for '{client_name}'")
    return None


# ── Filter & price extraction ──────────────────────────────────────────────────

def _apply_domain_filter(page, magazine_domain: str, debug: bool):
    """Open the Filter panel, fill 'Site Name or Keyword', click Apply."""
    # Filter is an <a href="#catalog-filters"> (not a <button>)
    filter_link = (
        page.query_selector('a[href="#catalog-filters"]')
        or page.query_selector('a.btn:has-text("Filter"), button:has-text("Filter")')
    )
    if filter_link:
        filter_link.click()
        _wait(page, 1500)
        screenshot(page, "lm_06_filter_open", debug)

    # "Site Name or Keyword" input — placeholder is an example domain
    domain_input = None
    for sel in [
        'input[placeholder*=".it" i]',
        'input[placeholder*=".com" i]',
        'input[placeholder*=".org" i]',
        'input[placeholder*="keyword" i]',
        'input[placeholder*="site name" i]',
        'input[placeholder*="domain" i]',
    ]:
        try:
            page.wait_for_selector(sel, timeout=3000, state="visible")
            el = page.query_selector(sel)
            if el and el.is_visible():
                domain_input = el
                break
        except Exception:
            pass

    if not domain_input:
        screenshot(page, "lm_no_filter_input", debug)
        if debug:
            print("  [linksme] could not find Site Name or Keyword input")
        return

    domain_input.fill("")
    domain_input.fill(magazine_domain)
    _wait(page, 500)
    screenshot(page, "lm_07_domain_filled", debug)

    # Click Apply (not "Apply and Save")
    apply_btn = None
    for sel in [
        'button.btn-success:has-text("Apply")',
        'button.btn-primary:has-text("Apply")',
        'button:has-text("Apply")',
        'a:has-text("Apply")',
    ]:
        try:
            page.wait_for_selector(sel, timeout=2000, state="visible")
            btn = page.query_selector(sel)
            if btn and btn.is_visible() and 'save' not in _safe_text(btn).lower():
                apply_btn = btn
                break
        except Exception:
            pass

    if apply_btn:
        apply_btn.click()
    else:
        page.keyboard.press("Enter")

    _wait(page, 3500)
    screenshot(page, "lm_08_filtered", debug)

    if debug:
        filtered_text = page.inner_text("body")
        (Path(__file__).parent / "debug_screenshots" / "lm_filtered_text.txt").write_text(
            filtered_text[:6000]
        )
        print(f"  [linksme] filter applied — "
              f"{'no records' if 'no records' in filtered_text.lower() else 'results found'}")


def _parse_price(text: str) -> int | None:
    """Extract a USD price from a text fragment. Returns int or None."""
    for m in _PRICE_RE.finditer(text):
        raw = m.group(1).replace(',', '').replace(' ', '')
        try:
            val = int(float(raw))
            if 10 < val < 100_000:
                return val
        except ValueError:
            pass
    return None


def _extract_prices(page, magazine_domain: str, debug: bool) -> list[int]:
    """
    Extract prices from the filtered table for exactly magazine_domain.

    The filter returns rows whose site name *contains* the search term, so we
    must compare the Site cell text exactly against magazine_domain to avoid
    returning prices for sites like 'rprinvesting.com' when searching 'investing.com'.
    """
    screenshot(page, "pw_09_price_scan", debug)
    prices: list[int] = []

    body_text = page.inner_text("body")
    if "no records" in body_text.lower():
        if debug:
            print("  [linksme] no records — domain not in catalog")
        return prices

    # Normalize the target domain so comparisons are scheme/www/slash-agnostic.
    domain_lower = _normalize_domain(magazine_domain)

    # Strategy 1: standard <tr>/<td> table
    for row in page.query_selector_all('tr'):
        cells = row.query_selector_all('td')
        if len(cells) < 2:
            continue
        # Site name is in the first cell; normalize before comparing so that
        # "www.blackdown.org" or "https://www.blackdown.org" both match.
        site_link = cells[0].query_selector('a')
        raw_text  = _safe_text(site_link) if site_link else _safe_text(cells[0])
        site_text = _normalize_domain(raw_text)
        if site_text != domain_lower:
            continue
        # Price is in the last cell
        price_text = _safe_text(cells[-1])
        val = _parse_price(price_text)
        if val:
            prices.append(val)

    # Strategy 2: walk from a site <a> link to its row and read last cell
    if not prices:
        for link in page.query_selector_all('td a, [class*="site"] a'):
            if _normalize_domain(_safe_text(link)) != domain_lower:
                continue
            try:
                row_el = link.evaluate_handle(
                    'el => el.closest("tr") || el.closest("[class*=row]")'
                )
                if row_el:
                    row_text = page.evaluate('el => el ? el.innerText : ""', row_el)
                    val = _parse_price(row_text)
                    if val:
                        prices.append(val)
            except Exception:
                pass

    # Strategy 3: scan text lines — find the line matching the domain
    if not prices:
        lines = body_text.splitlines()
        for i, line in enumerate(lines):
            if _normalize_domain(line.strip()) == domain_lower:
                # Price is usually on the same line or the next few lines
                chunk = ' '.join(lines[i:i+5])
                val = _parse_price(chunk)
                if val:
                    prices.append(val)
                break

    if debug:
        print(f"  [linksme] prices for exact '{magazine_domain}': {prices}")

    return prices


# ── Public entry point ─────────────────────────────────────────────────────────

def get_price(magazine_domain: str, client_name: str, debug: bool = False) -> int | None:
    """
    Returns the USD Guest Post price for magazine_domain in the Links.me project
    matching client_name, or None if not found.
    Does NOT raise if the client project is missing — returns None instead.
    """
    # Normalize early so a full URL like "https://www.blackdown.org/" is reduced
    # to "blackdown.org" before it ever reaches the filter input or comparison logic.
    magazine_domain = _normalize_domain(magazine_domain)
    if debug:
        print(f"  [linksme] normalized magazine_domain → {magazine_domain!r}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        kwargs  = load_session_kwargs("linksme")
        context = browser.new_context(**kwargs)
        page    = context.new_page()

        # ── 1. Navigate to app ──────────────────────────────────────────────
        page.goto(BASE_URL, wait_until=NAV_WAIT)
        _wait(page, 2000)
        screenshot(page, "lm_00_home", debug)

        if _is_on_login(page):
            _login(page, debug)
            save_session(context, "linksme")

        # ── 2. Find project matching client_name ────────────────────────────
        gp_url = _find_guest_posting_url(page, client_name, debug)

        if not gp_url:
            page.goto(f"{BASE_URL}/dashboard", wait_until=NAV_WAIT)
            _wait(page, 2000)
            gp_url = _find_guest_posting_url(page, client_name, debug)

        if not gp_url:
            print(f"  [linksme] no project found for '{client_name}'")
            browser.close()
            return None

        # ── 3. Navigate to Guest Posting Sites List ─────────────────────────
        print(f"  [linksme] navigating to: {gp_url}")
        page.goto(gp_url, wait_until=NAV_WAIT)
        _wait(page, 2500)
        screenshot(page, "lm_05_gp_list", debug)

        if debug:
            (Path(__file__).parent / "debug_screenshots" / "lm_gp_text.txt").write_text(
                page.inner_text("body")[:8000]
            )

        # ── 4. Filter by magazine domain ────────────────────────────────────
        _apply_domain_filter(page, magazine_domain, debug)

        # ── 5. Extract prices ───────────────────────────────────────────────
        prices = _extract_prices(page, magazine_domain, debug)

        browser.close()
        return min(prices) if prices else None
