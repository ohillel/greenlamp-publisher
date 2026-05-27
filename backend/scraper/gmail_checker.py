"""
Monitor Gmail inbox for publication notification emails from PressWhizz and Links.me.
Called by the APScheduler job in main.py every 10 minutes alongside run_status_check().

PressWhizz: from=no-reply@app.presswhizz.com, subject contains "Your order has been completed"
Links.me:   from=info@links.me,               subject contains "status has changed"

Requires GOOGLE_TOKEN_JSON to include gmail.readonly (and optionally gmail.modify) scope.
If the current token only has gmail.send, add gmail.readonly via google_auth.py and
re-authorize, then update GOOGLE_TOKEN_JSON in Railway env vars.
"""
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────

def _supabase():
    from supabase import create_client
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def _gmail_service():
    """
    Build Gmail API service client.
    Returns (service, can_read, can_modify).

    The token must have been authorized with gmail.readonly (at minimum).
    If it only has gmail.send the function returns can_read=False and the
    caller skips processing entirely.
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_json = os.getenv("GOOGLE_TOKEN_JSON")
    raw = json.loads(token_json) if token_json else None
    if raw is None and os.path.exists("token.json"):
        with open("token.json") as f:
            raw = json.load(f)
    if raw is None:
        raise RuntimeError("GOOGLE_TOKEN_JSON not set")

    # Request all three scopes; the token may only honour a subset
    scopes = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    creds = Credentials.from_authorized_user_info(raw, scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("gmail", "v1", credentials=creds)

    # Probe whether the token actually allows reading
    can_read = can_modify = False
    try:
        service.users().labels().list(userId="me").execute()
        can_read = True
        # modify is a superset of readonly — assume it works if readonly works
        # (a 403 on the first modify call will be caught and logged per-message)
        can_modify = True
    except Exception as e:
        print(f"  [gmail_checker] read probe failed ({e}) — "
              f"re-authorize with gmail.readonly scope and update GOOGLE_TOKEN_JSON")

    return service, can_read, can_modify


def _decode_b64(data: str) -> str:
    padded = data + "==" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _get_message_body(msg: dict) -> str:
    """Return the best available text body (plain preferred, HTML as fallback)."""
    def _extract(part: dict) -> tuple[str, str]:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if mime == "text/plain" and data:
            return _decode_b64(data), ""
        if mime == "text/html" and data:
            return "", _decode_b64(data)
        plain_acc = html_acc = ""
        for sub in part.get("parts", []):
            p, h = _extract(sub)
            plain_acc = plain_acc or p
            html_acc  = html_acc  or h
        return plain_acc, html_acc

    plain, html = _extract(msg.get("payload", {}))
    return plain or html


def _mark_read(service, msg_id: str) -> None:
    try:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        print(f"  [gmail_checker] marked {msg_id} as read")
    except Exception as e:
        print(f"  [gmail_checker] could not mark {msg_id} as read: {e}")


def _normalize_domain(text: str) -> str:
    t = text.strip().lower()
    # Extract the URL portion if it's embedded in surrounding text
    # e.g. "- https://www.cpomagazine.com" → "https://www.cpomagazine.com"
    m = re.search(r'https?://\S+', t)
    if m:
        t = m.group(0)
    for prefix in ("https://", "http://", "www."):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.split("/")[0].split("?")[0].rstrip(".,")


def _notify(sb, new_status: str, client_name: str, magazine: str) -> None:
    """Push + email to Or and Denise — same as the automatic status checker."""
    from .push_notifications import send_push_to_roles
    from .email_notifications import send_email_to_roles
    bodies = {
        "published":     f"✅ Published for {client_name} → {magazine}",
        "not_published": f"❌ Rejected for {client_name} → {magazine}",
    }
    body = bodies.get(new_status, f"{new_status} for {client_name} → {magazine}")
    try:
        send_push_to_roles(sb, ["or", "denise"], "Greenlamp Publisher", body)
    except Exception as e:
        print(f"  [gmail_checker] push error: {e}")
    try:
        send_email_to_roles(["or", "denise"], body, body)
    except Exception as e:
        print(f"  [gmail_checker] email error: {e}")


# ── PressWhizz ────────────────────────────────────────────────────────────────

def _scrape_presswhizz_order(order_url: str, debug: bool) -> dict:
    """
    Open a PressWhizz order detail page and return:
      {'portal_domain': str, 'published_url': str | None}
    Returns {} if nothing useful could be extracted.
    """
    from playwright.sync_api import sync_playwright
    from .browser import load_session_kwargs, save_session

    BASE_PW = "https://app.presswhizz.com"
    result: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(**load_session_kwargs("presswhizz"))
        page    = context.new_page()

        page.goto(order_url, wait_until="load")
        page.wait_for_timeout(3000)

        # Detect session expiry: either redirect to login page OR a 500 error page
        _body_preview = ""
        try:
            _body_preview = page.inner_text("body")[:300].lower()
        except Exception:
            pass
        _is_error_page = (
            "500" in _body_preview[:80] or
            "internal server error" in _body_preview or
            "something went wrong" in _body_preview or
            "server error" in _body_preview
        )

        # Log in if redirected to auth page or got a 500 (expired session)
        if _is_error_page or \
           any(p in page.url for p in ("/login", "/signin", "/auth")) or \
           page.query_selector('input[type="password"]'):
            reason = "500 error page" if _is_error_page else "login redirect"
            print(f"  [gmail_checker/pw] session expired ({reason}) — logging in…")
            page.goto(BASE_PW, wait_until="load")
            page.wait_for_timeout(2000)
            page.fill(
                'input[type="email"], input[name="email"]',
                os.environ["PRESSWHIZZ_EMAIL"],
            )
            page.fill('input[type="password"]', os.environ["PRESSWHIZZ_PASSWORD"])
            page.click('button[type="submit"]')
            page.wait_for_timeout(3000)
            save_session(context, "presswhizz")
            page.goto(order_url, wait_until="load")
            page.wait_for_timeout(3000)

        body_text = page.inner_text("body")

        if debug:
            debug_dir = Path(__file__).parent / "debug_screenshots"
            debug_dir.mkdir(exist_ok=True)
            (debug_dir / "pw_order_detail.txt").write_text(body_text[:8000])
            print(f"  [gmail_checker/pw] page text: {body_text[:300]!r}")

        # ── Published article URL ──────────────────────────────────────────────
        # "Publisher provided the URL of the Guest Post: https://..."
        for pattern in [
            r'(?:Publisher provided the URL of the Guest Post|Guest Post URL|Published URL|Article URL)[:\s]+(https?://\S+)',
            r'Guest Post[:\s]+(https?://\S+)',
        ]:
            m = re.search(pattern, body_text, re.IGNORECASE)
            if m:
                result["published_url"] = m.group(1).rstrip(".,)")
                break

        # ── Portal / magazine domain ───────────────────────────────────────────
        m = re.search(r'Portal[:\s]+([^\n\r\t,]+)', body_text, re.IGNORECASE)
        if m:
            result["portal_domain"] = _normalize_domain(m.group(1))

        # Fallback: look for external links in the page
        if "portal_domain" not in result:
            for el in page.query_selector_all("a[href]"):
                try:
                    href = el.get_attribute("href") or ""
                    if (href.startswith("http") and
                            "presswhizz" not in href and
                            "google" not in href and
                            "mailto" not in href):
                        candidate = _normalize_domain(href)
                        if "." in candidate and len(candidate) > 4:
                            result["portal_domain"] = candidate
                            break
                except Exception:
                    pass

        browser.close()

    print(f"  [gmail_checker/pw] scraped: {result}")
    return result


def _process_presswhizz_email(service, msg: dict, sb, can_modify: bool, debug: bool) -> bool:
    msg_id = msg["id"]
    body   = _get_message_body(msg)

    print(f"  [gmail_checker/pw] processing msg {msg_id}")
    if debug:
        print(f"  [gmail_checker/pw] body preview: {body[:400]!r}")

    # Extract order URL from email body
    m = re.search(r'https://app\.presswhizz\.com/client/orders/[\w-]+', body)
    if not m:
        print("  [gmail_checker/pw] no order URL in email body — skipping")
        return False

    order_url = m.group(0)
    print(f"  [gmail_checker/pw] order URL: {order_url}")

    scraped      = _scrape_presswhizz_order(order_url, debug)
    portal       = scraped.get("portal_domain")
    published_url = scraped.get("published_url")

    if not portal:
        print("  [gmail_checker/pw] could not determine portal domain — skipping")
        return False

    # Find matching sent_to_publisher article
    res = sb.from_("articles") \
        .select("id, magazine, clients(name)") \
        .eq("status", "sent_to_publisher") \
        .eq("chosen_publisher", "presswhizz") \
        .execute()

    matched_id = client_name = None
    for row in (res.data or []):
        if _normalize_domain(row.get("magazine") or "") == portal:
            matched_id  = row["id"]
            client_name = (row.get("clients") or {}).get("name", "")
            break

    if not matched_id:
        print(f"  [gmail_checker/pw] no article with status=sent_to_publisher+presswhizz for portal={portal!r}")
        return False

    update: dict = {"status": "published", "published_at": datetime.now(timezone.utc).isoformat()}
    if published_url:
        update["published_url"] = published_url
    sb.from_("articles").update(update).eq("id", matched_id).execute()
    print(f"  [gmail_checker/pw] ✅ article {matched_id} → published (url={published_url})")

    _notify(sb, "published", client_name or "", portal)
    if can_modify:
        _mark_read(service, msg_id)
    return True


# ── Links.me ──────────────────────────────────────────────────────────────────

def _scrape_linksme_report(nav_url: str, debug: bool) -> list[dict]:
    """
    Open a Links.me report URL and return a list of settled rows:
      [{'domain': str, 'status': 'published'|'not_published', 'published_url': str|None}]
    """
    from playwright.sync_api import sync_playwright
    from .browser import load_session_kwargs, save_session
    from .check_linksme import _is_on_login, _login, _normalize_domain as lm_norm

    rows_out: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(**load_session_kwargs("linksme"))
        page    = context.new_page()

        print(f"  [gmail_checker/lm] navigating to: {nav_url}")
        page.goto(nav_url, wait_until="load")
        page.wait_for_timeout(3000)
        print(f"  [gmail_checker/lm] landed on: {page.url}")

        if _is_on_login(page):
            print("  [gmail_checker/lm] on login page — logging in…")
            _login(page, debug)
            save_session(context, "linksme")
            print(f"  [gmail_checker/lm] post-login URL: {page.url}")
            print(f"  [gmail_checker/lm] re-navigating to: {nav_url}")
            page.goto(nav_url, wait_until="load")
            page.wait_for_timeout(3000)
            print(f"  [gmail_checker/lm] post-redirect URL: {page.url}")

        # Wait up to 30 s for the report content to appear on the page.
        _content_found = False
        for _wait_sel in [
            'text="Resource"',
            'text="Publication status"',
            'text="Published"',
        ]:
            try:
                page.wait_for_selector(_wait_sel, timeout=30000)
                print(f"  [gmail_checker/lm] content signal found: {_wait_sel}")
                _content_found = True
                break
            except Exception:
                pass

        if not _content_found:
            print("  [gmail_checker/lm] timed out waiting for report content")

        # Log full page text so we can see what actually loaded
        try:
            _body_text = page.inner_text("body")
        except Exception:
            _body_text = "(error reading body)"
        print(f"  [gmail_checker/lm] URL after wait: {page.url}")
        print(f"  [gmail_checker/lm] page text (first 1000): {_body_text[:1000]!r}")

        if debug:
            debug_dir = Path(__file__).parent / "debug_screenshots"
            debug_dir.mkdir(exist_ok=True)
            (debug_dir / "lm_email_report.txt").write_text(_body_text[:8000])

        # Locate the report table by its known column headers.
        # Expected headers (case-insensitive): Resource, Purchase date/ID, Type,
        # Cost, Publication status.
        _REPORT_HEADERS = {"resource", "purchase", "type", "cost", "publication"}

        def _find_report_table(pg):
            for tbl in pg.query_selector_all("table"):
                header_cells = tbl.query_selector_all("th")
                if not header_cells:
                    # Some tables use <td> in the first row as headers
                    first_row = tbl.query_selector("tr")
                    header_cells = first_row.query_selector_all("td") if first_row else []
                header_text = {c.inner_text().strip().lower().split()[0]
                               for c in header_cells if c.inner_text().strip()}
                if len(header_text & _REPORT_HEADERS) >= 3:
                    return tbl
            return None

        # Wait up to 15 s for the report table to appear
        report_table = None
        for _ in range(15):
            report_table = _find_report_table(page)
            if report_table:
                break
            page.wait_for_timeout(1000)

        if not report_table:
            print("  [gmail_checker/lm] report table not found on page — logging all table headers for debug:")
            for i, tbl in enumerate(page.query_selector_all("table")):
                ths = [c.inner_text().strip() for c in tbl.query_selector_all("th")]
                print(f"    table[{i}] headers: {ths}")
        else:
            print("  [gmail_checker/lm] report table found")

        for page_num in range(1, 6):   # up to 5 pages
            if not report_table:
                break

            rows_on_page = 0
            for tr in report_table.query_selector_all("tr"):
                cells = tr.query_selector_all("td")
                if len(cells) < 2:
                    continue

                # Column 0: Resource — site / magazine domain
                site_link = cells[0].query_selector("a")
                site_raw  = (site_link.inner_text() if site_link else cells[0].inner_text()).strip()
                site_dom  = lm_norm(site_raw)
                if not site_dom or "." not in site_dom or len(site_dom) < 4:
                    continue

                rows_on_page += 1
                # Publication status is the last column; also scan all cells
                cell_texts = [c.inner_text().strip() for c in cells]
                matched_status = False
                for ct in cell_texts:
                    ct_lower = ct.lower()

                    if ct_lower.startswith("published") or ct_lower.startswith("confirmation request"):
                        pub_url = None
                        for c in cells:
                            lnk = c.query_selector("a[href]")
                            if lnk:
                                href = lnk.get_attribute("href") or ""
                                if (href.startswith("http") and
                                        "links.me" not in href and
                                        "google" not in href):
                                    pub_url = href
                                    break
                        rows_out.append({
                            "domain":        site_dom,
                            "status":        "published",
                            "published_url": pub_url,
                        })
                        print(f"  [gmail_checker/lm] {site_dom} → published (url={pub_url})")
                        matched_status = True
                        break

                    elif ct_lower.startswith("rejected"):
                        rows_out.append({
                            "domain":        site_dom,
                            "status":        "not_published",
                            "published_url": None,
                        })
                        print(f"  [gmail_checker/lm] {site_dom} → not_published")
                        matched_status = True
                        break

                if not matched_status:
                    print(f"  [gmail_checker/lm] row domain={site_dom!r} — no status match, cell texts: {cell_texts}")

            print(f"  [gmail_checker/lm] page {page_num}: {rows_on_page} data row(s) scanned, {len(rows_out)} settled so far")

            # Pagination
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
                break
            next_btn.click()
            page.wait_for_timeout(2000)
            report_table = _find_report_table(page)  # re-locate after navigation

        browser.close()

    return rows_out


def _process_linksme_email(service, msg: dict, sb, can_modify: bool, debug: bool) -> bool:
    msg_id = msg["id"]
    body   = _get_message_body(msg)

    print(f"  [gmail_checker/lm] processing msg {msg_id}")
    if debug:
        print(f"  [gmail_checker/lm] body preview: {body[:400]!r}")

    # Use the "Log in to your account" button href from the email.
    # This URL handles auth and redirects directly to the correct report page.
    nav_url = None
    m = re.search(r'href=["\']?(https://app\.links\.me/[^\s"<>\']+)["\']?[^>]*>\s*Log in to your account', body, re.IGNORECASE)
    if m:
        nav_url = m.group(1).rstrip(".,>)")
    # Fallback: any links.me href that contains /project/
    if not nav_url:
        m = re.search(r'href=["\']?(https://app\.links\.me/project/[^\s"<>\']+)["\']?', body)
        if m:
            nav_url = m.group(1).rstrip(".,>)")

    if not nav_url:
        print("  [gmail_checker/lm] no Links.me URL found in email body — skipping")
        return False
    print(f"  [gmail_checker/lm] email nav URL: {nav_url}")

    rows = _scrape_linksme_report(nav_url, debug)

    if not rows:
        print("  [gmail_checker/lm] no settled rows found on report page")
        return False

    # Build lookup: magazine domain → article row from Supabase
    res = sb.from_("articles") \
        .select("id, magazine, clients(name)") \
        .eq("status", "sent_to_publisher") \
        .eq("chosen_publisher", "linksme") \
        .execute()

    dom_to_article: dict[str, dict] = {}
    for row in (res.data or []):
        dom = _normalize_domain(row.get("magazine") or "")
        if dom:
            dom_to_article[dom] = row

    handled_any = False
    for row in rows:
        dom     = row["domain"]
        article = dom_to_article.get(dom)
        if not article:
            print(f"  [gmail_checker/lm] no article found for domain {dom!r}")
            continue

        new_status  = row["status"]
        pub_url     = row["published_url"]
        article_id  = article["id"]
        client_name = (article.get("clients") or {}).get("name", "")

        update: dict = {"status": new_status}
        if new_status == "published":
            update["published_at"] = datetime.now(timezone.utc).isoformat()
        if pub_url:
            update["published_url"] = pub_url
        sb.from_("articles").update(update).eq("id", article_id).execute()
        print(f"  [gmail_checker/lm] ✅ article {article_id} → {new_status} (url={pub_url})")

        _notify(sb, new_status, client_name, dom)
        handled_any = True

    if can_modify:
        _mark_read(service, msg_id)

    return handled_any


# ── Main entry point ──────────────────────────────────────────────────────────

def check_gmail_notifications(debug: bool = False) -> None:
    """
    Check Gmail inbox for publication notification emails and process them.
    Called by the APScheduler job every 10 minutes.
    """
    if not os.environ.get("GOOGLE_TOKEN_JSON"):
        print("[gmail_checker] GOOGLE_TOKEN_JSON not set — skipping")
        return

    print("[gmail_checker] checking Gmail for publication notifications…")

    try:
        service, can_read, can_modify = _gmail_service()
    except Exception as e:
        print(f"[gmail_checker] could not build Gmail service: {e}")
        return

    if not can_read:
        print("[gmail_checker] Gmail token lacks gmail.readonly — re-authorize and update GOOGLE_TOKEN_JSON")
        return

    print(f"[gmail_checker] Gmail service ready (can_read=True, can_modify={can_modify})")

    try:
        sb = _supabase()
    except Exception as e:
        print(f"[gmail_checker] Supabase error: {e}")
        return

    tasks = [
        (
            "PressWhizz",
            'is:unread from:no-reply@app.presswhizz.com subject:"Your order has been completed"',
            lambda msg: _process_presswhizz_email(service, msg, sb, can_modify, debug),
        ),
        (
            "Links.me",
            'from:info@links.me subject:"status has changed" newer_than:1d',
            lambda msg: _process_linksme_email(service, msg, sb, can_modify, debug),
        ),
    ]

    for label, query, handler in tasks:
        try:
            result = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=20,
            ).execute()
            msgs = result.get("messages", [])
            print(f"[gmail_checker] {label}: {len(msgs)} unread email(s)")

            for ref in msgs:
                try:
                    full_msg = service.users().messages().get(
                        userId="me",
                        id=ref["id"],
                        format="full",
                    ).execute()
                    handler(full_msg)
                except Exception as e:
                    print(f"[gmail_checker] error processing {label} msg {ref['id']}: {e}")

        except Exception as e:
            print(f"[gmail_checker] error querying {label} emails: {e}")

    print("[gmail_checker] done")
