"""
Check publication status for articles on Links.me.

Flow:
  1. Restore session (login if needed).
  2. Navigate to the dashboard.
  3. For each unique client project, derive the Report URL from the
     Guest Posting catalog URL (same position-matching logic as linksme.py).
  4. Navigate to the project's Report page.
  5. Find the row whose Resource column matches the article's magazine domain.
  6. Read the Publication status column.
  7. Return status: 'published', 'not_published', or None (no change / not found).

Status mapping (case-insensitive prefix match):
  starts with "Published" → "published"
  starts with "Rejected"  → "not_published"
  anything else           → None (still in progress)
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright
from .browser import save_session, load_session_kwargs, screenshot

BASE_URL = "https://app.links.me"
NAV_WAIT = "load"

_DOMAIN_RE = re.compile(r'^[a-z0-9][a-z0-9\-]*\.[a-z]{2,}$', re.IGNORECASE)

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
        return el.inner_text() or ''
    except Exception:
        return ''


def _is_on_login(page) -> bool:
    if any(p in page.url for p in ('/login', '/signin', '/auth')):
        return True
    try:
        return bool(page.query_selector('input[type="password"]'))
    except Exception:
        return False


def _login(page, debug: bool):
    print("  [check_linksme] logging in…")
    if not page.query_selector('input[type="password"]'):
        page.goto(f"{BASE_URL}/login", wait_until=NAV_WAIT)
        _wait(page, 3000)

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
            if t == 'password':
                inp.fill(os.environ["LINKSME_PASSWORD"])
                break

    page.click('button[type="submit"], button:has-text("Login"), button:has-text("Sign in")')
    _wait(page, 3000)

    print(f"  [check_linksme] post-login URL:   {page.url}")
    print(f"  [check_linksme] post-login title: {page.title()}")

    if _is_on_login(page):
        # Grab any visible error message from the page before raising
        error_text = ''
        for sel in [
            '[class*="error" i]', '[class*="alert" i]', '[class*="danger" i]',
            '[role="alert"]', 'p.text-red', '.text-red-500',
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    error_text = el.inner_text().strip()
                    if error_text:
                        break
            except Exception:
                pass
        detail = f' — page says: "{error_text}"' if error_text else ''
        raise RuntimeError(
            f"Links.me login failed — still on login page{detail}. "
            "Check LINKSME_EMAIL / LINKSME_PASSWORD"
        )


def _normalize_domain(text: str) -> str:
    t = text.strip().lower()
    for prefix in ('https://', 'http://', 'www.'):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.rstrip('/')


def _parse_project_names(page) -> list[str]:
    body = page.inner_text("body")
    seen: set[str] = set()
    names: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if _DOMAIN_RE.match(line) and line not in _NAV_LABELS and line.lower() not in seen:
            seen.add(line.lower())
            names.append(line)
    return names


def _find_report_url(page, client_name: str, debug: bool) -> str | None:
    """
    Map client_name → Report page URL using the same position-matching logic as
    linksme.py — but using /report hrefs instead of /catalog/guest-posting.

    Strategy A: derive from the guest-posting URL (replace the path suffix).
    Strategy B: directly match a[href*="/report"] by position.
    """
    # Strategy A: get the GP URL and swap the path
    gp_hrefs = page.eval_on_selector_all(
        'a[href*="/catalog/guest-posting"]',
        'els => els.map(e => e.href)',
    )
    project_names = _parse_project_names(page)

    # Always log so Railway shows these even without debug=True
    print(f"  [check_linksme] searching for client: '{client_name}'")
    print(f"  [check_linksme] dashboard projects ({len(project_names)}): {project_names}")
    print(f"  [check_linksme] GP hrefs found: {len(gp_hrefs)}")

    name_lower = client_name.lower()

    def _gp_to_report(gp_url: str) -> str:
        # https://app.links.me/project/2587/catalog/guest-posting
        # → https://app.links.me/project/2587/report
        return re.sub(r'/catalog/guest-posting.*$', '/report', gp_url)

    # Exact match
    for i, pname in enumerate(project_names):
        if name_lower == pname.lower() and i < len(gp_hrefs):
            url = _gp_to_report(gp_hrefs[i])
            print(f"  [check_linksme] exact match: '{pname}' → {url}")
            return url

    # Partial match
    for i, pname in enumerate(project_names):
        if (name_lower in pname.lower() or pname.lower() in name_lower) and i < len(gp_hrefs):
            url = _gp_to_report(gp_hrefs[i])
            print(f"  [check_linksme] partial match: '{pname}' → {url}")
            return url

    # Fuzzy: any word > 3 chars
    parts = [p for p in re.split(r'[\s._-]+', name_lower) if len(p) > 3]
    for i, pname in enumerate(project_names):
        if any(p in pname.lower() for p in parts) and i < len(gp_hrefs):
            url = _gp_to_report(gp_hrefs[i])
            print(f"  [check_linksme] fuzzy match '{client_name}' → '{pname}' → {url}")
            return url

    # Strategy B: use report hrefs directly (same position logic)
    report_hrefs = page.eval_on_selector_all(
        'a[href*="/report"]',
        'els => els.map(e => e.href)',
    )
    # filter to project-specific report URLs
    project_report_hrefs = [h for h in report_hrefs if re.search(r'/project/\d+/report', h)]

    for i, pname in enumerate(project_names):
        if name_lower == pname.lower() and i < len(project_report_hrefs):
            url = project_report_hrefs[i]
            print(f"  [check_linksme] strategy-B exact: '{pname}' → {url}")
            return url

    print(f"  [check_linksme] NO MATCH found for client '{client_name}' among: {project_names}")
    return None


def _map_publication_status(text: str) -> str | None:
    """
    'Published 14.05.2026'              → 'published'
    'Confirmation request 14.05.2026'   → 'published'
    'Rejected 14.05.2026'               → 'not_published'
    anything else                       → None
    """
    lower = text.strip().lower()
    if lower.startswith('published'):
        return 'published'
    if lower.startswith('confirmation request'):
        return 'published'
    if lower.startswith('rejected'):
        return 'not_published'
    return None


def _scan_one_report_page(page, domain_lower: str, page_num: int, debug: bool) -> str | None:
    """
    Scan the currently-loaded report page for domain_lower.
    Returns 'published' | 'not_published' | None.
    Logs all domains found on the page unconditionally.
    """
    _wait(page, 2000)
    screenshot(page, f"clm_report_p{page_num}", debug)

    # ── Save full HTML for offline inspection (debug only) ────────────────────
    if debug:
        debug_dir = Path(__file__).parent / "debug_screenshots"
        debug_dir.mkdir(exist_ok=True)
        (debug_dir / f"clm_report_p{page_num}.html").write_text(page.content(), encoding="utf-8")
        (debug_dir / f"clm_report_p{page_num}_text.txt").write_text(
            page.inner_text("body")[:12000], encoding="utf-8"
        )

    # ── Collect all site domains visible on this page (always logged) ─────────
    found_domains: list[str] = []
    for row in page.query_selector_all('tr'):
        cells = row.query_selector_all('td')
        if len(cells) < 2:
            continue
        site_link = cells[0].query_selector('a')
        raw = _safe_text(site_link) if site_link else _safe_text(cells[0])
        norm = _normalize_domain(raw.strip())
        if norm:
            found_domains.append(norm)

    # Fallback: pick domains from text lines if table yielded nothing
    if not found_domains:
        for line in page.inner_text("body").splitlines():
            norm = _normalize_domain(line.strip())
            if norm and '.' in norm and len(norm) > 4:
                found_domains.append(norm)

    print(f"  [check_linksme] page {page_num} — {len(found_domains)} domains found: {found_domains}")

    # ── Strategy 1: <tr>/<td> table ──────────────────────────────────────────
    for row in page.query_selector_all('tr'):
        cells = row.query_selector_all('td')
        if len(cells) < 2:
            continue
        site_link = cells[0].query_selector('a')
        site_text = (_safe_text(site_link) if site_link else _safe_text(cells[0])).strip()
        if _normalize_domain(site_text) != domain_lower:
            continue
        for cell in cells[1:]:
            mapped = _map_publication_status(_safe_text(cell))
            if mapped is not None:
                print(f"  [check_linksme] table match '{domain_lower}': {_safe_text(cell)!r} → {mapped}")
                return mapped
        print(f"  [check_linksme] '{domain_lower}' found in table — status not actionable yet")
        return None

    # ── Strategy 2: text-line scan ────────────────────────────────────────────
    body_lines = page.inner_text("body").splitlines()
    for i, line in enumerate(body_lines):
        if _normalize_domain(line.strip()) == domain_lower:
            for part in body_lines[i:i + 6]:
                mapped = _map_publication_status(part.strip())
                if mapped is not None:
                    print(f"  [check_linksme] text scan '{domain_lower}': {part.strip()!r} → {mapped}")
                    return mapped
            print(f"  [check_linksme] '{domain_lower}' found in text — status not actionable yet")
            return None

    return None   # domain not on this page


def _parse_report_page(page, magazine_domain: str, debug: bool) -> str | None:
    """
    Parse the Links.me Report page (all pagination pages) and return the
    publication status for magazine_domain, or None if not found.
    """
    domain_lower = _normalize_domain(magazine_domain)
    print(f"  [check_linksme] scanning report for '{domain_lower}'…")

    page_num = 1
    while True:
        result = _scan_one_report_page(page, domain_lower, page_num, debug)

        # Found the domain — result is a status or None (found but not actionable)
        if result is not None or _domain_was_found_this_page(page, domain_lower):
            return result

        # Try to advance to next page
        next_btn = None
        for sel in [
            'a[rel="next"]',
            'li.next:not(.disabled) a',
            'a:has-text("Next")',
            'a:has-text("›")',
            'a:has-text("»")',
        ]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    next_btn = el
                    break
            except Exception:
                pass

        if not next_btn:
            print(f"  [check_linksme] '{domain_lower}' not found after {page_num} page(s)")
            return None

        print(f"  [check_linksme] not found on page {page_num} — advancing to page {page_num + 1}")
        next_btn.click()
        page_num += 1
        if page_num > 20:   # safety cap
            print(f"  [check_linksme] pagination cap reached ({page_num} pages) — stopping")
            return None


def _domain_was_found_this_page(page, domain_lower: str) -> bool:
    """Return True if domain_lower appeared anywhere in the current page text."""
    return domain_lower in page.inner_text("body").lower()


def check_batch(
    articles: list[dict],
    debug: bool = False,
) -> dict[str, str]:
    """
    Check publication status for a batch of Links.me articles.

    articles: list of {'id': str, 'magazine': str, 'client_name': str}
    Returns: dict mapping article_id → 'published' | 'not_published'
             (only articles whose status should change are included)
    """
    results: dict[str, str] = {}
    if not articles:
        return results

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        kwargs  = load_session_kwargs("linksme")
        context = browser.new_context(**kwargs)
        page    = context.new_page()

        page.goto(BASE_URL, wait_until=NAV_WAIT)
        _wait(page, 2000)
        screenshot(page, "clm_00_home", debug)

        if _is_on_login(page):
            _login(page, debug)
            save_session(context, "linksme")

        # ── Ensure we're on the dashboard ──────────────────────────────────
        if '/dashboard' not in page.url:
            page.goto(f"{BASE_URL}/dashboard", wait_until=NAV_WAIT)
            _wait(page, 2000)

        # ── Cache report URLs per client to avoid re-navigating ─────────────
        report_url_cache: dict[str, str | None] = {}

        for article in articles:
            article_id  = article['id']
            magazine    = article['magazine']
            client_name = article['client_name']

            if client_name not in report_url_cache:
                # Return to dashboard before each new client lookup
                page.goto(f"{BASE_URL}/dashboard", wait_until=NAV_WAIT)
                _wait(page, 2000)
                report_url = _find_report_url(page, client_name, debug)
                report_url_cache[client_name] = report_url

            report_url = report_url_cache[client_name]

            if not report_url:
                print(f"  [check_linksme] no report URL for client '{client_name}' — skipping article {article_id}")
                continue

            page.goto(report_url, wait_until=NAV_WAIT)
            _wait(page, 2500)

            status = _parse_report_page(page, magazine, debug)

            if status in ('published', 'not_published'):
                results[article_id] = status
                if debug:
                    print(f"  [check_linksme] article {article_id} ({magazine}): → {status}")
            elif debug:
                print(f"  [check_linksme] article {article_id} ({magazine}): in-progress or not found — no change")

        browser.close()

    return results
