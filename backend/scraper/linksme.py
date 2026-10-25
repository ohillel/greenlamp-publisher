"""
Scraper for app.links.me

Flow:
  1. Restore session (skip login if valid).
  2. If on login page, sign in and save session.
  3. On the dashboard, find the project whose name matches client_name.
  4. Navigate to that project's Guest Posting Sites List page.
  5. Click Filter, scroll to "Site Name or Keyword", enter the domain, click Apply.
  6. Read the Price column — return the price found (Links.me quotes in EUR;
     USD is still parsed as a fallback), or None if no match.
  7. If no project matches client_name, return None (don't raise).
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from .browser import save_session, load_session_kwargs, clear_session, apply_default_timeouts, screenshot, describe_inputs
from .eur_price import parse_eur

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
    """
    Sign in to Links.me.

    The login sequence itself is unchanged. What is new is diagnostics: the
    screenshots here were all gated behind `debug`, which is False for the
    Price Check, and the failure path saved none at all — so a login failure in
    production left nothing to look at. The failure screenshot is now
    unconditional and each step reports what it actually found.
    """
    print("  [linksme] logging in…")

    # Confirm the credentials the process can actually see. Lengths only —
    # never the values — which still distinguishes "unset" from "stale" from
    # "has a stray space".
    email = os.environ.get("LINKSME_EMAIL")
    pw    = os.environ.get("LINKSME_PASSWORD")
    print(f"  [linksme] LINKSME_EMAIL set={bool(email)} "
          f"len={len(email) if email else 0} "
          f"domain={email.split('@')[-1] if email and '@' in email else 'n/a'} "
          f"stripped_len={len(email.strip()) if email else 0}")
    print(f"  [linksme] LINKSME_PASSWORD set={bool(pw)} "
          f"len={len(pw) if pw else 0} "
          f"stripped_len={len(pw.strip()) if pw else 0}")
    if not email or not pw:
        screenshot(page, "lm_login_FAILURE", True)
        raise RuntimeError(
            "Links.me login failed — LINKSME_EMAIL / LINKSME_PASSWORD is not set "
            "in this process's environment."
        )

    if not page.query_selector('input[type="password"]'):
        page.goto(LOGIN_URL, wait_until=NAV_WAIT)
        _wait(page, 3000)
    print(f"  [linksme] login page url={page.url!r}")
    screenshot(page, "lm_01_login", debug)

    email_sel = 'input[type="email"], input[name="email"], input[placeholder*="email" i]'
    try:
        page.wait_for_selector(email_sel, timeout=10000)
        print(f"  [linksme] email field found via {email_sel!r}")
    except Exception:
        print("  [linksme] email field NOT found — dumping the form:")
        describe_inputs(page, "linksme")
        screenshot(page, "lm_login_FAILURE", True)
        raise
    page.fill(email_sel, email)

    pw_sel = 'input[type="password"], input[name="password"]'
    try:
        page.wait_for_selector(pw_sel, timeout=5000)
        page.fill(pw_sel, pw)
        print(f"  [linksme] password field found via {pw_sel!r}")
    except Exception:
        filled = False
        for inp in page.query_selector_all('input'):
            t = (inp.get_attribute('type') or '').lower()
            p = (inp.get_attribute('placeholder') or '').lower()
            if t == 'password' or 'password' in p:
                inp.fill(pw)
                filled = True
                print("  [linksme] password field found via attribute scan")
                break
        if not filled:
            # A two-step form (email, then Next, then password) would land here.
            print("  [linksme] password field NOT found — dumping the form:")
            describe_inputs(page, "linksme")

    screenshot(page, "lm_02_filled", debug)
    page.click('button[type="submit"], button:has-text("Login"), button:has-text("Sign in")')

    if not _wait_for_logged_in(page):
        # Always save this one — it is the only view of what blocked the login.
        screenshot(page, "lm_login_FAILURE", True)
        _report_login_blockers(page)
        raise RuntimeError(
            "Links.me login failed — still on login page. "
            "Check LINKSME_EMAIL / LINKSME_PASSWORD in .env"
        )
    print(f"  [linksme] login OK — url={page.url!r}")
    screenshot(page, "lm_03_after_login", debug)


# Text that only appears once the dashboard has rendered. Used as a second
# success signal for the case where the SPA swaps the view without the URL
# changing in time.
_LOGGED_IN_MARKERS = ("my projects", "dashboard")

LOGIN_SUCCESS_TIMEOUT_MS = 20_000


def _wait_for_logged_in(page) -> bool:
    """
    Wait for evidence that the login SUCCEEDED, rather than for the absence of
    a login URL.

    The old check ran `'/login' in page.url` a fixed 3s after submitting. Links.me
    signs in over XHR and then routes client-side, so the URL can still read
    /login while the dashboard is already rendering — the logs showed
    "after submit url=.../login" immediately followed by dashboard content and
    url=/dashboard. That raced check failed a login that had in fact worked, and
    because it raised, save_session() never ran, so every later run had to log in
    again and failed the same way.

    Returns True as soon as either the URL leaves /login or a dashboard marker
    appears; False only if neither happens within the timeout.
    """
    # Primary signal: the URL leaves the login route.
    try:
        page.wait_for_url(
            lambda u: not any(p in u for p in ('/login', '/signin', '/auth')),
            timeout=LOGIN_SUCCESS_TIMEOUT_MS,
        )
        print(f"  [linksme] login confirmed by URL change → {page.url!r}")
        return True
    except Exception:
        pass

    # Secondary signal: the dashboard rendered even though the URL lags behind.
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        body = ""
    for marker in _LOGGED_IN_MARKERS:
        if marker in body:
            print(f"  [linksme] login confirmed by page content ({marker!r}) "
                  f"while url still reads {page.url!r}")
            return True

    # Last check: no password field left on the page means the form is gone.
    try:
        if not page.query_selector('input[type="password"]'):
            print(f"  [linksme] login confirmed — login form gone (url={page.url!r})")
            return True
    except Exception:
        pass

    print(f"  [linksme] no sign of a successful login after "
          f"{LOGIN_SUCCESS_TIMEOUT_MS}ms (url={page.url!r})")
    return False


def _report_login_blockers(page) -> None:
    """Log whatever the page says about a refused login: errors, captcha, 2FA."""
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""

    lowered = body.lower()
    for marker in ("captcha", "recaptcha", "hcaptcha", "verify you are human",
                   "two-factor", "2fa", "verification code", "confirm your email",
                   "too many attempts", "blocked", "invalid", "incorrect"):
        if marker in lowered:
            print(f"  [linksme] page mentions {marker!r} after the login attempt")

    try:
        for sel in ('iframe[src*="recaptcha" i]', 'iframe[src*="captcha" i]',
                    '.g-recaptcha', '[class*="captcha" i]'):
            if page.query_selector(sel):
                print(f"  [linksme] CAPTCHA element present: {sel!r}")
    except Exception:
        pass

    # Short, visible error text is usually the actual reason.
    try:
        for sel in ('[class*="error" i]', '[class*="alert" i]', '[role="alert"]',
                    '[class*="invalid" i]', '[class*="danger" i]'):
            for el in page.query_selector_all(sel):
                t = (el.inner_text() or "").strip()
                if t and len(t) <= 200 and el.is_visible():
                    print(f"  [linksme] on-page message: {t!r}")
    except Exception:
        pass

    describe_inputs(page, "linksme")
    print(f"  [linksme] first 400 chars of page text: {body[:400]!r}")


# ── Dashboard readiness ────────────────────────────────────────────────────────

# Per-project catalog links. These used to be rendered inline on the dashboard
# (36 projects → 36 links), but a run that clearly showed "MY PROJECTS (39)" and
# all 39 domains found ZERO of them, so their presence can no longer be the
# readiness signal — see _wait_for_projects. Several spellings are tried because
# the path may simply have been renamed.
PROJECT_LINK_SELECTORS = (
    'a[href*="/catalog/guest-posting"]',
    'a[href*="guest-posting"]',
    'a[href*="guest_posting"]',
    'a[href*="guest-post"]',
)
PROJECT_LINK_SELECTOR = PROJECT_LINK_SELECTORS[0]

PROJECTS_TIMEOUT_MS = 20_000
_POLL_INTERVAL_MS = 500


def _catalog_links(page) -> list[str]:
    """Every per-project catalog href on the page, trying each known spelling."""
    for sel in PROJECT_LINK_SELECTORS:
        try:
            hrefs = page.eval_on_selector_all(sel, 'els => els.map(e => e.href)')
        except Exception:
            continue
        if hrefs:
            if sel != PROJECT_LINK_SELECTOR:
                print(f"  [linksme] catalog links found via fallback selector {sel!r}")
            return hrefs
    return []


def _wait_for_projects(page) -> bool:
    """
    Wait for the dashboard's project list to render.

    Readiness is project NAMES, not catalog links. Waiting on the links was
    wrong: a dashboard showing "MY PROJECTS (39)" and every domain still had
    zero matching hrefs, so this returned False, the code concluded it was
    signed out, threw away a perfectly good session, logged in again, and burnt
    2 × 20s before the 60s per-site budget killed the lookup.

    Names are parsed from the page text, which the logs prove is populated.
    Links are still checked because they are the stronger signal when present.
    """
    import time

    deadline = time.monotonic() + PROJECTS_TIMEOUT_MS / 1000
    names: list[str] = []
    links: list[str] = []

    while True:
        try:
            links = _catalog_links(page)
        except Exception:
            links = []
        try:
            names = _parse_project_names(page)
        except Exception:
            names = []

        if names or links:
            print(f"  [linksme] dashboard ready — {len(names)} project name(s), "
                  f"{len(links)} catalog link(s) (url={page.url!r})")
            if names and not links:
                # Navigation will have to be derived from the row markup.
                print("  [linksme] NOTE: project names present but no catalog links — "
                      "dumping the project area so the markup can be seen")
                _dump_project_area(page)
            return True

        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(_POLL_INTERVAL_MS)

    print(f"  [linksme] no project names or catalog links after "
          f"{PROJECTS_TIMEOUT_MS}ms (url={page.url!r})")
    return False


_PROJECT_AREA_JS = """
() => {
  const all = Array.from(document.querySelectorAll('*'));
  const hits = all.filter(e => (e.textContent || '').toUpperCase().includes('MY PROJECTS'));
  if (!hits.length) return null;
  // The smallest element containing the heading, then one level out for context.
  hits.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);
  const node = hits[0];
  const box = node.parentElement ? node.parentElement.parentElement || node.parentElement : node;
  return box.outerHTML;
}
"""

_HREF_CENSUS_JS = """
() => {
  const as = Array.from(document.querySelectorAll('a[href]'));
  const pats = {};
  for (const a of as) {
    const h = a.getAttribute('href') || '';
    const key = h.split('?')[0].split('/').slice(0, 4).join('/') || '(empty)';
    pats[key] = (pats[key] || 0) + 1;
  }
  return {
    anchors: as.length,
    patterns: Object.entries(pats).sort((x, y) => y[1] - x[1]).slice(0, 25),
    clickableNonAnchors: document.querySelectorAll(
      'button, [role="button"], [onclick], [class*="row" i][tabindex]'
    ).length,
  };
}
"""


def _dump_project_area(page, limit: int = 4000) -> None:
    """
    Print the markup around "MY PROJECTS" plus a census of the page's links.

    This is the evidence needed to decide how to reach a project's
    guest-posting catalog now that inline catalog hrefs have disappeared: it
    shows whether the rows are anchors with a different path, buttons, or plain
    text in a clickable container.
    """
    try:
        census = page.evaluate(_HREF_CENSUS_JS)
        if not isinstance(census, dict):
            print(f"  [linksme] href census returned nothing usable ({type(census).__name__})")
        else:
            print(f"  [linksme] page has {census.get('anchors')} anchor(s), "
                  f"{census.get('clickableNonAnchors')} clickable non-anchor element(s)")
            print("  [linksme] href patterns (prefix → count):")
            for pattern, count in (census.get("patterns") or []):
                print(f"      {count:>4}  {pattern}")
    except Exception as e:
        print(f"  [linksme] href census failed: {e}")

    try:
        html = page.evaluate(_PROJECT_AREA_JS)
    except Exception as e:
        print(f"  [linksme] could not extract the project area: {e}")
        return
    if not html:
        print("  [linksme] no element containing 'MY PROJECTS' was found")
        return

    path = Path(__file__).parent / "debug_screenshots" / "lm_project_area.html"
    try:
        path.write_text(html[:200_000])
        print(f"  [linksme] project-area markup written to {path}")
    except Exception as e:
        print(f"  [linksme] could not write the project-area markup: {e}")
    print(f"  [linksme] project-area markup (first {limit} chars):\n{html[:limit]}")


def _report_dashboard_state(page) -> None:
    """
    Explain WHY the dashboard has no projects: signed out, rate limited,
    blocked, or simply changed.
    """
    try:
        body = page.inner_text("body") or ""
    except Exception:
        body = ""
    lowered = body.lower()

    try:
        has_pw = bool(page.query_selector('input[type="password"]'))
    except Exception:
        has_pw = False
    print(f"  [linksme] url={page.url!r} password_field={has_pw} body_len={len(body)}")

    # A rate limit or temporary block reads very differently from a login wall,
    # and we have logged in repeatedly today, so name it explicitly if present.
    for marker in ("too many", "rate limit", "try again later", "temporarily",
                   "blocked", "suspended", "429", "captcha", "unusual activity",
                   "maintenance", "session expired", "log in", "sign in"):
        if marker in lowered:
            print(f"  [linksme] page mentions {marker!r}")

    print(f"  [linksme] first 400 chars of page text: {body[:400]!r}")


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

    # Tries every known catalog-href spelling rather than the one hardcoded
    # path, which stopped matching.
    gp_hrefs = _catalog_links(page)
    project_names = _parse_project_names(page)

    print(f"  [linksme] {len(project_names)} projects, {len(gp_hrefs)} catalog links")
    print(f"  [linksme] projects: {project_names[:20]}")
    (Path(__file__).parent / "debug_screenshots" / "lm_projects_text.txt").write_text(
        page.inner_text("body")[:5000]
    )

    if project_names and not gp_hrefs:
        # The position-matching below needs hrefs. Names without links means the
        # rows are no longer plain anchors, so dump the markup: that is the
        # evidence needed to pick a new way in (a renamed path, a button, or a
        # clickable row).
        print(f"  [linksme] {len(project_names)} project name(s) but NO catalog links — "
              f"the dashboard markup has changed")
        _dump_project_area(page)
        screenshot(page, "lm_no_catalog_links_FAILURE", True)
    elif not project_names or not gp_hrefs:
        print(f"  [linksme] WARNING: project_names or gp_hrefs is empty — dashboard "
              f"structure may have changed. project_names={len(project_names)}, "
              f"gp_hrefs={len(gp_hrefs)}")

    name_lower = client_name.lower()

    # Exact match
    for i, pname in enumerate(project_names):
        if name_lower == pname.lower() and i < len(gp_hrefs):
            print(f"  [linksme] exact match: {pname} → {gp_hrefs[i]}")
            return gp_hrefs[i]

    # Partial match (client_name in project or vice versa)
    for i, pname in enumerate(project_names):
        if (name_lower in pname.lower() or pname.lower() in name_lower) and i < len(gp_hrefs):
            print(f"  [linksme] partial match: {pname} → {gp_hrefs[i]}")
            return gp_hrefs[i]

    # Fuzzy: any word > 3 chars
    parts = [p for p in re.split(r'[\s._-]+', name_lower) if len(p) > 3]
    for i, pname in enumerate(project_names):
        if any(p in pname.lower() for p in parts) and i < len(gp_hrefs):
            print(f"  [linksme] fuzzy match '{client_name}' → {pname} → {gp_hrefs[i]}")
            return gp_hrefs[i]

    # Fallback (logged loudly): if there's exactly one project, use it — covers
    # accounts with a single project where the name doesn't match client_name
    # due to a rename/typo, without silently widening matching for multi-project
    # accounts where picking the wrong one would be worse than returning None.
    if len(project_names) == 1 and gp_hrefs:
        print(f"  [linksme] FALLBACK: no name match for '{client_name}', but exactly "
              f"one project exists ({project_names[0]}) — using it")
        return gp_hrefs[0]

    # Last resort when the dashboard has names but no catalog hrefs: reach the
    # catalog the way a person would — click the project, then its "Guest
    # Posting Sites List" nav item. This uses the visible LABEL rather than a
    # guessed URL, so it survives the path being renamed.
    if project_names and not gp_hrefs:
        clicked = _navigate_by_click(page, client_name, project_names, debug)
        if clicked:
            return clicked

    print(f"  [linksme] no match for '{client_name}' among projects: {project_names}")
    return None


# The per-project nav item that opens the guest-posting catalog. Matched on its
# visible text, which is stable across URL changes.
GUEST_POSTING_LABEL = "Guest Posting Sites List"


def _pick_project_name(client_name: str, project_names: list[str]) -> str | None:
    """Same exact → partial → fuzzy order used for href matching."""
    target = client_name.lower()
    for name in project_names:
        if name.lower() == target:
            return name
    for name in project_names:
        if target in name.lower() or name.lower() in target:
            return name
    parts = [p for p in re.split(r'[\s._-]+', target) if len(p) > 3]
    for name in project_names:
        if any(p in name.lower() for p in parts):
            return name
    return None


def _navigate_by_click(page, client_name: str, project_names: list[str],
                       debug: bool) -> str | None:
    """
    Click through to a project's guest-posting catalog and return the URL.

    Used when the dashboard lists projects without catalog hrefs. Returns the
    landed URL on success, or None — in which case the markup dump from
    _dump_project_area is the evidence needed to fix this properly.
    """
    name = _pick_project_name(client_name, project_names)
    if not name:
        print(f"  [linksme] click-through: no project name matches '{client_name}'")
        return None

    print(f"  [linksme] click-through: opening project {name!r}")
    try:
        page.click(f'text="{name}"', timeout=8000)
        _wait(page, 2500)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        print(f"  [linksme] click-through: now at {page.url!r}")
    except Exception as e:
        print(f"  [linksme] click-through: could not click {name!r}: {e}")
        screenshot(page, "lm_click_project_FAILURE", True)
        return None

    # Prefer the catalog link if the project page exposes one.
    hrefs = _catalog_links(page)
    if hrefs:
        print(f"  [linksme] click-through: catalog link on the project page → {hrefs[0]}")
        return hrefs[0]

    # Otherwise click the nav item by its label.
    for sel in (f'a:has-text("{GUEST_POSTING_LABEL}")',
                f'button:has-text("{GUEST_POSTING_LABEL}")',
                f'text="{GUEST_POSTING_LABEL}"'):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _wait(page, 2500)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                print(f"  [linksme] click-through: opened the catalog via {sel!r} "
                      f"→ {page.url!r}")
                return page.url
        except Exception:
            continue

    print(f"  [linksme] click-through: no {GUEST_POSTING_LABEL!r} nav item on "
          f"{page.url!r}")
    _dump_project_area(page)
    screenshot(page, "lm_no_gp_nav_FAILURE", True)
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
        print("  [linksme] clicked Filter link")
        _wait(page, 1500)
        screenshot(page, "lm_06_filter_open", True)
    else:
        print("  [linksme] WARNING: no Filter link/button found — filter panel may "
              "already be open or site structure changed")

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
                print(f"  [linksme] domain filter input found via: {sel}")
                break
        except Exception:
            pass

    if not domain_input:
        print(f"  [linksme] FAILED to find Site Name or Keyword input — visible <input> "
              f"count: {len(page.query_selector_all('input'))}")
        screenshot(page, "lm_no_filter_input", True)
        (Path(__file__).parent / "debug_screenshots" / "lm_no_filter_text.txt").write_text(
            page.inner_text("body")[:8000]
        )
        return

    domain_input.fill("")
    domain_input.fill(magazine_domain)
    _wait(page, 500)
    screenshot(page, "lm_07_domain_filled", True)

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
                print(f"  [linksme] Apply button found via: {sel}")
                break
        except Exception:
            pass

    if apply_btn:
        apply_btn.click()
    else:
        page.keyboard.press("Enter")
        print("  [linksme] no Apply button found — pressed Enter to apply filter")

    _wait(page, 3500)
    screenshot(page, "lm_08_filtered", True)

    filtered_text = page.inner_text("body")
    (Path(__file__).parent / "debug_screenshots" / "lm_filtered_text.txt").write_text(
        filtered_text[:6000]
    )
    print(f"  [linksme] filter applied — "
          f"{'no records' if 'no records' in filtered_text.lower() else 'results found'}")


def _parse_price(text: str) -> float | None:
    """
    Extract a price from a text fragment. Returns a float or None.

    Links.me has moved from USD to EUR: the cell now reads "48.32 EUR". The
    regex below only ever accepted USD/ILS and the € SYMBOL — never the literal
    "EUR" — so every row parsed as None. EUR is handled by the shared parser in
    eur_price.py, the same one the Collaborator.pro client uses, rather
    than by teaching this regex a second currency.

    USD is still tried as a fallback in case any row is still priced in dollars.

    Cents are preserved (the articles.price_linksme column is NUMERIC(10,2));
    the previous int(float(...)) discarded them, which would have turned
    48.32 into 48.
    """
    # EUR first — this is the current format.
    eur = parse_eur(text)
    if eur is not None:
        return eur

    # USD / ILS fallback for any row still quoted in dollars.
    for m in _PRICE_RE.finditer(text):
        raw = m.group(1).replace(',', '').replace(' ', '')
        try:
            val = float(raw)
            if 10 < val < 100_000:
                return round(val, 2)
        except ValueError:
            pass
    return None


def _extract_prices(page, magazine_domain: str, debug: bool) -> list[float]:
    """
    Extract prices from the filtered table for exactly magazine_domain.

    The filter returns rows whose site name *contains* the search term, so we
    must compare the Site cell text exactly against magazine_domain to avoid
    returning prices for sites like 'rprinvesting.com' when searching 'investing.com'.
    """
    screenshot(page, "lm_09_price_scan", True)
    prices: list[float] = []

    body_text = page.inner_text("body")
    print(f"  [linksme] price scan: page text is {len(body_text)} chars")
    if "no records" in body_text.lower():
        print("  [linksme] no records — domain not in catalog under this client/project")
        return prices

    # Normalize the target domain so comparisons are scheme/www/slash-agnostic.
    domain_lower = _normalize_domain(magazine_domain)

    # Strategy 1: standard <tr>/<td> table
    all_rows = page.query_selector_all('tr')
    seen_site_texts = []
    for row in all_rows:
        cells = row.query_selector_all('td')
        if len(cells) < 2:
            continue
        # Site name is in the first cell; normalize before comparing so that
        # "www.blackdown.org" or "https://www.blackdown.org" both match.
        site_link = cells[0].query_selector('a')
        raw_text  = _safe_text(site_link) if site_link else _safe_text(cells[0])
        site_text = _normalize_domain(raw_text)
        if site_text:
            seen_site_texts.append(site_text)
        if site_text != domain_lower:
            continue
        # Price is in the last cell
        price_text = _safe_text(cells[-1])
        val = _parse_price(price_text)
        print(f"  [linksme] strategy1: row matched '{site_text}' — price cell text "
              f"{price_text!r} parsed as {val}")
        if val:
            prices.append(val)

    print(f"  [linksme] strategy1: scanned {len(all_rows)} rows, site texts seen: "
          f"{seen_site_texts[:20]}")

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
                    print(f"  [linksme] strategy2: matched link row text {row_text[:150]!r} "
                          f"parsed as {val}")
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
                print(f"  [linksme] strategy3: matched line {line.strip()!r} at offset {i}, "
                      f"chunk {chunk[:150]!r} parsed as {val}")
                if val:
                    prices.append(val)
                break

    # Strategy 4 (fallback, logged loudly): if exact match found nothing but
    # body text contains the domain as a substring (e.g. extra whitespace,
    # subdomain, or different cell markup not handled above), scan all lines
    # containing the domain and try to parse a price from a window around it.
    if not prices and domain_lower in body_text.lower():
        print(f"  [linksme] FALLBACK: exact-match strategies found nothing, but "
              f"'{domain_lower}' appears as a substring in page text — scanning "
              f"surrounding lines")
        lines = body_text.splitlines()
        for i, line in enumerate(lines):
            if domain_lower in line.strip().lower():
                chunk = ' '.join(lines[i:i+5])
                val = _parse_price(chunk)
                print(f"  [linksme] FALLBACK: line {line.strip()!r} at offset {i}, "
                      f"chunk {chunk[:150]!r} parsed as {val}")
                if val:
                    prices.append(val)
                    break

    print(f"  [linksme] prices for '{magazine_domain}' (normalized '{domain_lower}'): {prices}")

    return prices


# ── Public entry point ─────────────────────────────────────────────────────────

def get_price(magazine_domain: str, client_name: str, debug: bool = False) -> float | None:
    """
    Returns the USD Guest Post price for magazine_domain in the Links.me project
    matching client_name, or None if not found.
    Does NOT raise if the client project is missing — returns None instead.
    """
    # Normalize early so a full URL like "https://www.blackdown.org/" is reduced
    # to "blackdown.org" before it ever reaches the filter input or comparison logic.
    magazine_domain = _normalize_domain(magazine_domain)
    print(f"  [linksme] normalized magazine_domain → {magazine_domain!r}, client_name={client_name!r}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            return _get_price_inner(pw, browser, magazine_domain, client_name, debug)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _get_price_inner(pw, browser, magazine_domain: str, client_name: str, debug: bool, retried_login: bool = False) -> float | None:
    kwargs  = load_session_kwargs("linksme")
    # A restored session belongs to whichever account last logged in. After a
    # credential change that can be the OLD account, so say which path was taken.
    print(f"  [linksme] stored session restored={bool(kwargs)} retried_login={retried_login}")
    context = browser.new_context(**kwargs)
    page    = context.new_page()
    apply_default_timeouts(context, page)

    # ── 1. Open the dashboard, logging in if the projects do not appear ──
    # Auth state is NOT inferred from the landing page any more. That check
    # ("is there a password field / is the URL /login?") ran 2s after load, so
    # an SPA that had not yet redirected to /login looked signed-in: _login was
    # skipped entirely and the project list parsed as empty. The absence of a
    # "login OK" line in that failure is exactly this path. The dashboard's own
    # project links are the only trustworthy signal, so wait for those instead.
    page.goto(f"{BASE_URL}/dashboard", wait_until=NAV_WAIT)
    screenshot(page, "lm_00_home", debug)

    if not _wait_for_projects(page):
        print("  [linksme] no projects on the dashboard — signing in")
        _report_dashboard_state(page)
        try:
            _login(page, debug)
        except (RuntimeError, PlaywrightTimeoutError) as e:
            # Session may have been a stale/corrupt cookie jar — clear it and
            # retry once with a guaranteed-fresh login before giving up.
            if not retried_login:
                print(f"  [linksme] login failed ({e}) — clearing session and retrying once")
                clear_session("linksme")
                context.close()
                return _get_price_inner(pw, browser, magazine_domain, client_name, debug, retried_login=True)
            raise
        save_session(context, "linksme")
        print("  [linksme] session saved after login")

        page.goto(f"{BASE_URL}/dashboard", wait_until=NAV_WAIT)
        if not _wait_for_projects(page):
            # Signed in, yet still no projects: a changed dashboard, a block,
            # or a rate limit. Say which, rather than just reporting "0 projects".
            print("  [linksme] STILL no projects after a successful login")
            _report_dashboard_state(page)
            screenshot(page, "lm_no_projects_after_login_FAILURE", True)

    # ── 2. Find project matching client_name ────────────────────────────
    gp_url = _find_guest_posting_url(page, client_name, debug)

    if not gp_url:
        print(f"  [linksme] no project found for '{client_name}'")
        screenshot(page, "lm_no_project_FAILURE", True)
        return None

    # ── 3. Navigate to Guest Posting Sites List ─────────────────────────
    print(f"  [linksme] navigating to: {gp_url}")
    page.goto(gp_url, wait_until=NAV_WAIT)
    _wait(page, 2500)
    screenshot(page, "lm_05_gp_list", True)

    (Path(__file__).parent / "debug_screenshots" / "lm_gp_text.txt").write_text(
        page.inner_text("body")[:8000]
    )

    # ── 4. Filter by magazine domain ────────────────────────────────────
    _apply_domain_filter(page, magazine_domain, debug)

    # ── 5. Extract prices ───────────────────────────────────────────────
    prices = _extract_prices(page, magazine_domain, debug)
    if not prices:
        print(f"  [linksme] no prices found for '{magazine_domain}' under client "
              f"'{client_name}'")
        screenshot(page, "lm_no_prices_FAILURE", True)
    else:
        print(f"  [linksme] final result for '{magazine_domain}': prices={prices}, "
              f"returning min={min(prices)}")

    return min(prices) if prices else None
