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
    if _is_on_login(page):
        raise RuntimeError("Links.me login failed — check LINKSME_EMAIL / LINKSME_PASSWORD")


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

    if debug:
        print(f"  [check_linksme] {len(project_names)} projects, {len(gp_hrefs)} GP links")

    name_lower = client_name.lower()

    def _gp_to_report(gp_url: str) -> str:
        # https://app.links.me/project/2587/catalog/guest-posting
        # → https://app.links.me/project/2587/report
        return re.sub(r'/catalog/guest-posting.*$', '/report', gp_url)

    # Exact match
    for i, pname in enumerate(project_names):
        if name_lower == pname.lower() and i < len(gp_hrefs):
            url = _gp_to_report(gp_hrefs[i])
            if debug:
                print(f"  [check_linksme] exact match: {pname} → {url}")
            return url

    # Partial match
    for i, pname in enumerate(project_names):
        if (name_lower in pname.lower() or pname.lower() in name_lower) and i < len(gp_hrefs):
            url = _gp_to_report(gp_hrefs[i])
            if debug:
                print(f"  [check_linksme] partial match: {pname} → {url}")
            return url

    # Fuzzy: any word > 3 chars
    parts = [p for p in re.split(r'[\s._-]+', name_lower) if len(p) > 3]
    for i, pname in enumerate(project_names):
        if any(p in pname.lower() for p in parts) and i < len(gp_hrefs):
            url = _gp_to_report(gp_hrefs[i])
            if debug:
                print(f"  [check_linksme] fuzzy match '{client_name}' → {pname} → {url}")
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
            if debug:
                print(f"  [check_linksme] strategy-B exact: {pname} → {url}")
            return url

    if debug:
        print(f"  [check_linksme] no report URL found for '{client_name}'")
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


def _parse_report_page(page, magazine_domain: str, debug: bool) -> str | None:
    """
    Parse the Links.me Report page and return the publication status for
    magazine_domain, or None if not found / still in progress.
    """
    _wait(page, 2000)
    screenshot(page, "clm_report", debug)

    if debug:
        (Path(__file__).parent / "debug_screenshots" / "clm_report_text.txt").write_text(
            page.inner_text("body")[:8000]
        )

    domain_lower = _normalize_domain(magazine_domain)

    # Strategy 1: standard <tr>/<td> table
    for row in page.query_selector_all('tr'):
        cells = row.query_selector_all('td')
        if len(cells) < 2:
            continue

        # Resource/site is usually the first cell
        site_link = cells[0].query_selector('a')
        site_text = (_safe_text(site_link) if site_link else _safe_text(cells[0])).strip()
        if _normalize_domain(site_text) != domain_lower:
            continue

        # Publication status: scan each cell for a status-like value
        for cell in cells[1:]:
            mapped = _map_publication_status(_safe_text(cell))
            if mapped is not None:
                if debug:
                    print(f"  [check_linksme] table match for '{domain_lower}': {_safe_text(cell)!r} → {mapped}")
                return mapped

        # Row matched domain but status not actionable
        if debug:
            print(f"  [check_linksme] '{domain_lower}' found in table — status not actionable yet")
        return None  # found but not published/rejected

    # Strategy 2: walk text lines
    body_lines = page.inner_text("body").splitlines()
    for i, line in enumerate(body_lines):
        if _normalize_domain(line.strip()) == domain_lower:
            # Check next few lines for a status
            window = ' '.join(body_lines[i:i + 6])
            for part in body_lines[i:i + 6]:
                mapped = _map_publication_status(part.strip())
                if mapped is not None:
                    if debug:
                        print(f"  [check_linksme] text scan match '{domain_lower}': {part.strip()!r} → {mapped}")
                    return mapped
            return None  # found, status not actionable

    if debug:
        print(f"  [check_linksme] '{domain_lower}' not found in report")
    return None


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
