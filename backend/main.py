import os
import io
import asyncio
import contextlib
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from dotenv import load_dotenv
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler
from supabase import create_client

load_dotenv()

from scraper.prices import fetch_prices                      # noqa: E402
from scraper.status_checker import run_status_check          # noqa: E402
from scraper.gmail_checker import check_gmail_notifications  # noqa: E402
from scraper.reminder_checker import check_stale_articles    # noqa: E402
from scraper.email_notifications import (  # noqa: E402
    send_email_to_roles,
    send_retainer_email,
    resolve_role_emails,
    resolve_retainer_email,
    sender_email,
)
from scraper.bulk_price_check import check_prices_bulk                                            # noqa: E402
from scraper.sheets_export import create_price_check_sheet                                        # noqa: E402
from scraper import presswhizz, linksme                                                            # noqa: E402


def _sb():
    """Return a Supabase service-role client (bypasses RLS)."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

CHECK_INTERVAL_MINUTES = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    # scheduler.add_job(
    #     run_status_check,
    #     trigger='interval',
    #     minutes=CHECK_INTERVAL_MINUTES,
    #     id='status_check',
    #     max_instances=1,       # never overlap — wait for previous run to finish
    #     coalesce=True,         # skip missed fires if the server was paused
    # )
    # scheduler.add_job(
    #     check_gmail_notifications,
    #     trigger='interval',
    #     minutes=CHECK_INTERVAL_MINUTES,
    #     id='gmail_check',
    #     max_instances=1,
    #     coalesce=True,
    # )
    scheduler.add_job(
        check_stale_articles,
        trigger='interval',
        hours=1,
        id='reminder_check',
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    print(f"[scheduler] started — status check + Gmail check every {CHECK_INTERVAL_MINUTES} minutes, reminder check every hour")
    print(f"[startup] CORS_ORIGINS={_CORS_ORIGINS}")
    # Print the values the email module actually resolves, so the log can never
    # drift from the addresses real emails are sent to.
    _role_map, _role_sources = resolve_role_emails()
    for _role in ("or", "publisher", "denise"):
        _addrs = ", ".join(_role_map[_role])
        print(f"[startup] {_role:<9} recipients = {_addrs}    ({_role_sources[_role]})")
    _retainer, _retainer_src = resolve_retainer_email()
    print(f"[startup] RETAINER_EMAIL  = {_retainer}    ({_retainer_src})")
    print(f"[startup] email sender    = {sender_email()}")
    yield
    scheduler.shutdown(wait=False)
    print("[scheduler] stopped")


_CORS_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173",
).split(",") if o.strip()]

app = FastAPI(title="Greenlamp Publisher API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "apikey", "x-client-info", "x-supabase-api-version"],
)


def extract_domain(url: str) -> str:
    url = url.strip()
    if url.startswith('http'):
        parsed = urlparse(url)
        domain = parsed.netloc
    else:
        domain = url
    domain = domain.replace('www.', '')
    domain = domain.split('/')[0]
    return domain.strip()


@app.get("/")
def health_check():
    return {"status": "ok", "email_backend": "gmail_api_v1"}


@app.get("/api/cors-test")
def cors_test():
    return {"cors_origins": _CORS_ORIGINS}


class PricesRequest(BaseModel):
    magazine: str    # magazine domain, e.g. "investing.com"
    client_name: str # Links.me project name, e.g. "echo.ai"


@app.post("/api/prices")
async def get_prices(req: PricesRequest):
    """
    Scrape PressWhizz and Links.me concurrently for the magazine domain.
    Returns: { presswhizz: int|null, linksme: int|null, errors?: {...} }
    Playwright is synchronous so we offload to a thread pool.
    """
    print(f"[api/prices] received magazine={req.magazine!r} client_name={req.client_name!r}")
    if not req.magazine or not req.client_name:
        raise HTTPException(status_code=422, detail="magazine and client_name are required")
    try:
        magazine = extract_domain(req.magazine).lower()
        client_name = req.client_name.strip()
        print(f"[api/prices] calling fetch_prices({magazine!r}, {client_name!r})")
        result = await run_in_threadpool(
            fetch_prices,
            magazine,
            client_name,
        )
        print(f"[api/prices] result={result!r}")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BulkPriceCheckRequest(BaseModel):
    urls: list[str]   # arbitrary pasted URLs or bare domains, one per line


@app.post("/api/price-check/bulk")
async def price_check_bulk(req: BulkPriceCheckRequest):
    """
    Or-only ad-hoc price checker: fetches PressWhizz + Links.me prices for a
    pasted list of URLs/domains (Links.me looked up under the "mstone"
    catalog) and exports the results to a new Google Sheet.
    Role enforcement is on the frontend, same as the other Or-only endpoints
    — the service-role key used here must never be exposed to non-Or users.
    Does not touch the per-article scraping flow (prices.py) or its sessions.
    """
    urls = [u.strip() for u in req.urls if u.strip()]
    if not urls:
        raise HTTPException(status_code=422, detail="urls must contain at least one entry")
    print(f"[price-check/bulk] checking {len(urls)} url(s)")
    try:
        # Shares _PRICE_FETCH_SEMAPHORE with _bg_fetch_prices so the two
        # never run more than 2 concurrent Playwright sessions combined —
        # otherwise simultaneous bg_fetch_prices + bulk price checks exhaust
        # Railway's memory and crash Chromium.
        async with _PRICE_FETCH_SEMAPHORE:
            results = await run_in_threadpool(check_prices_bulk, urls)

        # A single domain is read straight off the screen, so there is no
        # sheet to create. Anything more still exports as before.
        if len(urls) == 1:
            print("[price-check/bulk] single domain — returning results without a sheet")
            return {"results": results, "sheet_url": None}

        print(f"[price-check/bulk] fetched prices, creating sheet…")
        sheet_url = await run_in_threadpool(create_price_check_sheet, results)
        print(f"[price-check/bulk] sheet created: {sheet_url}")
        return {"results": results, "sheet_url": sheet_url}
    except Exception as e:
        print(f"[price-check/bulk] ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_with_captured_logs(fn, *args, **kwargs) -> dict:
    """Runs fn, capturing everything it prints to stdout, so callers without
    Railway log access can see exactly what a scraper run did."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            value = fn(*args, **kwargs)
        return {"value": value, "logs": buf.getvalue().splitlines(), "error": None}
    except Exception as e:
        return {"value": None, "logs": buf.getvalue().splitlines(), "error": str(e)}


class PriceCheckTestRequest(BaseModel):
    domain: str = "trinituner.com"
    client_name: str = "mstone"


@app.post("/api/price-check/test")
async def price_check_test(req: PriceCheckTestRequest = PriceCheckTestRequest()):
    """
    Diagnostic endpoint: runs a single domain (default "trinituner.com") through
    both PressWhizz and Links.me (default client "mstone") and returns every log
    line printed during the run, so selector/flow issues can be diagnosed without
    Railway log access. Does not touch the per-article scraping flow.
    """
    domain = req.domain
    client_name = req.client_name
    print(f"[price-check/test] running diagnostic check for {domain!r}")

    pw_result = await run_in_threadpool(
        _run_with_captured_logs, presswhizz.get_price, domain, True
    )
    lm_result = await run_in_threadpool(
        _run_with_captured_logs, linksme.get_price, domain, client_name, True
    )

    return {
        "domain": domain,
        "client_name": client_name,
        "presswhizz": pw_result,
        "linksme": lm_result,
    }


# ── Push notification endpoints ───────────────────────────────────────────────

class PushSubscribeRequest(BaseModel):
    user_id:      str
    subscription: dict   # {endpoint, keys: {p256dh, auth}}


@app.post("/api/push/subscribe")
async def push_subscribe(req: PushSubscribeRequest):
    """Save (or replace) a user's push subscription."""
    try:
        sb = _sb()
        endpoint = req.subscription.get("endpoint", "")
        # Delete ALL existing rows for this user, then insert fresh.
        # This clears stale subscriptions that accumulated from previous sessions.
        sb.from_("push_subscriptions") \
          .delete() \
          .eq("user_id", req.user_id) \
          .execute()
        sb.from_("push_subscriptions") \
          .insert({
              "user_id":      req.user_id,
              "endpoint":     endpoint,
              "subscription": req.subscription,
          }) \
          .execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Each price fetch spawns 2 headless Playwright browsers (PressWhizz +
# Links.me). Railway's resources get exhausted when several articles are
# submitted at once and all their fetches run concurrently — this caps how
# many _bg_fetch_prices calls actually run at the same time; the rest queue
# and run as slots free up.
_PRICE_FETCH_SEMAPHORE = asyncio.Semaphore(2)


async def _bg_fetch_prices(article_id: str, magazine: str, client_name: str) -> None:
    """
    Fetch prices in the background (after submission) and save them to the
    article row. prices_checked_at is always stamped on completion (success
    OR failure) so the frontend can tell "still fetching" (null timestamp)
    apart from "fetch finished, nothing found" (timestamp set, prices null) —
    without it, a failed fetch looks identical to one still in progress and
    the spinner never goes away.
    """
    sb = _sb()
    try:
        # Links.me's catalog is shared across all projects, so the actual
        # client_name (e.g. "converge") rarely matches a real Links.me
        # project and would return null. "apiiro" has the largest catalog,
        # so it's used as the Links.me lookup project regardless of the
        # article's real client.
        linksme_client_name = "apiiro"
        print(f"[bg_prices] fetching for article {article_id} ({magazine!r}, "
              f"client_name={client_name!r}, linksme_client_name={linksme_client_name!r})")
        async with _PRICE_FETCH_SEMAPHORE:
            result = await run_in_threadpool(fetch_prices, magazine, linksme_client_name)
        errors = result.get("errors")
        sb.from_("articles").update({
            "price_presswhizz":  result.get("presswhizz"),
            "price_linksme":     result.get("linksme"),
            "prices_checked_at": datetime.now(timezone.utc).isoformat(),
            "price_fetch_error": "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None,
        }).eq("id", article_id).execute()
        print(f"[bg_prices] saved — pw={result.get('presswhizz')} lm={result.get('linksme')} errors={errors}")
    except Exception as e:
        print(f"[bg_prices] ERROR for article {article_id}: {e}")
        try:
            sb.from_("articles").update({
                "prices_checked_at": datetime.now(timezone.utc).isoformat(),
                "price_fetch_error": str(e),
            }).eq("id", article_id).execute()
        except Exception as e2:
            print(f"[bg_prices] failed to record error for article {article_id}: {e2}")


class NotifyRequest(BaseModel):
    event:       str        # 'submitted' | 'approved' | 'sent' | 'returned' | 'published' | 'not_published'
    client_name: str
    magazine:    str
    reason:      str | None = None  # optional — included in email body for 'returned'
    article_id:  str | None = None  # triggers background price fetch on 'submitted'; used for deep link
    client_id:   str | None = None  # used to build the email deep link

_APP_URL = "https://greenlamp-publisher-psi.vercel.app"


_NOTIFY_MAP: dict[str, tuple[list[str], str]] = {
    # event → (recipient_roles, body_template)
    "submitted":              (["or"],           "New article for {client} → {magazine}"),
    "approved":               (["publisher"],    "Ready to send for {client} → {magazine}"),
    "approved_other_denise":  (["denise"],       "Article ready for you to send: {client} → {magazine}"),
    "sent":                   (["or", "denise"], "Article sent for {client} → {magazine}"),
    "returned":               (["or"],           "Article returned for {client} → {magazine}"),
    "published":              (["or", "denise"], "✅ Published for {client} → {magazine}"),
    "not_published":          (["or", "denise"], "❌ Rejected for {client} → {magazine}"),
}


@app.post("/api/push/test")
async def push_test():
    """
    Diagnostic endpoint — sends a test push to every subscribed user and
    returns a full report: VAPID config, profiles, subscriptions, delivery results.
    """
    from scraper.push_notifications import send_push, _vapid_claims
    from pywebpush import WebPushException

    report: dict = {}

    # 1. VAPID env vars
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    public_key  = os.environ.get("VAPID_PUBLIC_KEY",  "")
    email       = os.environ.get("VAPID_EMAIL",       "")
    report["vapid"] = {
        "private_key_set": bool(private_key),
        "public_key_set":  bool(public_key),
        "email":           email or "(not set)",
        "claims":          _vapid_claims(),
    }

    try:
        sb = _sb()

        # 2. All profiles + roles
        profiles_res = sb.from_("profiles").select("id, role").execute()
        profiles     = profiles_res.data or []
        report["profiles"] = [{"user_id": p["id"], "role": p.get("role")} for p in profiles]

        # 3. All subscriptions
        subs_res = sb.from_("push_subscriptions").select("user_id, endpoint, subscription").execute()
        subs     = subs_res.data or []
        report["subscriptions_count"] = len(subs)

        # 4. Attempt delivery to every subscription
        results = []
        for row in subs:
            endpoint = (row.get("endpoint") or "")[:80]
            sub      = row.get("subscription") or {}
            try:
                send_push(sub, "Greenlamp Test", "Push notifications are working!")
                results.append({"endpoint": endpoint, "status": "ok"})
            except WebPushException as e:
                status_code = e.response.status_code if e.response else None
                results.append({"endpoint": endpoint, "status": "webpush_error",
                                 "http_status": status_code, "detail": str(e)})
            except Exception as e:
                results.append({"endpoint": endpoint, "status": "error", "detail": str(e)})

        report["delivery"] = results

    except Exception as e:
        report["error"] = str(e)

    return report


@app.post("/api/email/test")
async def email_test():
    """
    Diagnostic endpoint — sends a test email via Gmail API and returns the result.
    """
    from scraper.email_notifications import send_email_to_roles

    token_set = bool(os.environ.get("GOOGLE_TOKEN_JSON"))
    report: dict = {
        "google_token_set": token_set,
        "sender": sender_email(),
    }

    if not token_set:
        report["result"] = "skipped — GOOGLE_TOKEN_JSON not set"
        return report

    def _do_send():
        send_email_to_roles(["or"], "Greenlamp email test", "Email notifications are working.")

    try:
        await run_in_threadpool(_do_send)
        report["result"] = "ok"
    except Exception as e:
        report["result"] = "error"
        report["detail"] = str(e)

    return report


@app.post("/api/check/run")
async def manual_status_check():
    """Manually trigger run_status_check(debug=True) for testing without waiting for the scheduler."""
    try:
        await run_in_threadpool(run_status_check, True)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gmail/check")
async def manual_gmail_check():
    """Manually trigger check_gmail_notifications(debug=True) for testing without waiting for the scheduler."""
    try:
        await run_in_threadpool(check_gmail_notifications, True)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _send_push_to_roles_with_cleanup(sb, roles: list[str], title: str, body: str) -> None:
    """
    Same as scraper.push_notifications.send_push_to_roles, but additionally
    deletes a subscription from push_subscriptions when delivery fails with
    410 Gone (subscription expired/revoked on the browser's push service),
    so we stop retrying dead endpoints on every future notification.
    """
    from scraper.push_notifications import send_push
    from pywebpush import WebPushException

    print(f"[push] send_push_to_roles: roles={roles!r} title={title!r} body={body!r}")
    try:
        profiles_res = sb.from_("profiles").select("id").in_("role", roles).execute()
        user_ids = [r["id"] for r in (profiles_res.data or [])]
        print(f"[push] found {len(user_ids)} profile(s) for roles {roles!r}: {user_ids}")
        if not user_ids:
            print(f"[push] no users found for roles {roles!r} — check profiles table role values")
            return

        subs_res = (
            sb.from_("push_subscriptions")
            .select("endpoint, subscription")
            .in_("user_id", user_ids)
            .execute()
        )
        rows = subs_res.data or []
        print(f"[push] found {len(rows)} subscription(s) for {len(user_ids)} user(s)")
        if not rows:
            print(f"[push] no subscriptions saved — user(s) have not subscribed yet")
    except Exception as e:
        print(f"[push] error fetching subscriptions: {e}")
        return

    for row in rows:
        sub = row.get("subscription") or {}
        endpoint = row.get("endpoint") or sub.get("endpoint") or ""
        try:
            send_push(sub, title, body)
            print(f"[push] delivered OK → {endpoint[:60]}…")
        except WebPushException as e:
            status_code = e.response.status_code if e.response else None
            print(f"[push] delivery failed ({status_code}): {e}")
            if status_code == 410 and endpoint:
                try:
                    sb.from_("push_subscriptions").delete().eq("endpoint", endpoint).execute()
                    print(f"[push] deleted expired subscription → {endpoint[:60]}…")
                except Exception as e2:
                    print(f"[push] failed to delete expired subscription: {e2}")
        except Exception as e:
            print(f"[push] unexpected error: {e}")


@app.post("/api/notify")
async def notify(req: NotifyRequest, background_tasks: BackgroundTasks):
    """Send a push notification to the appropriate roles for a status-change event."""
    entry = _NOTIFY_MAP.get(req.event)
    if not entry:
        raise HTTPException(status_code=422, detail=f"Unknown event: {req.event!r}")
    roles, body_template = entry
    body  = body_template.format(
        client=req.client_name,
        magazine=extract_domain(req.magazine),
    )
    # For returned articles, append the reason to the email body (but not the push)
    email_body = f"{body}\n\nReason: {req.reason}" if req.reason else body
    title = "Greenlamp Publisher"
    try:
        sb = _sb()
        await run_in_threadpool(_send_push_to_roles_with_cleanup, sb, roles, title, body)

        # Build the deep link directly to the article card
        deep_link: str | None = None
        if req.article_id and req.client_id:
            deep_link = (
                f"{_APP_URL}/clients/{req.client_id}"
                f"?article={req.article_id}"
            )

        # For 'published' events, look up the published URL and client Google Doc
        # and include them as extra links in the email.
        extra_links: list[dict] = []
        _client_doc_url: str | None = None
        if req.event == "published" and req.article_id:
            try:
                art_res = sb.from_("articles") \
                    .select("published_url, client_id") \
                    .eq("id", req.article_id) \
                    .single() \
                    .execute()
                if art_res.data:
                    if art_res.data.get("published_url"):
                        extra_links.append({
                            "url":   art_res.data["published_url"],
                            "label": "View Published Article",
                        })
                    if art_res.data.get("client_id"):
                        cli_res = sb.from_("clients") \
                            .select("google_doc_url") \
                            .eq("id", art_res.data["client_id"]) \
                            .single() \
                            .execute()
                        if cli_res.data:
                            _client_doc_url = cli_res.data.get("google_doc_url")
                            if _client_doc_url:
                                extra_links.append({
                                    "url":   _client_doc_url,
                                    "label": "Client Google Doc",
                                })
            except Exception as link_err:
                print(f"[notify] could not fetch extra links for published event: {link_err}")

        await run_in_threadpool(send_email_to_roles, roles, body, email_body,
                                extra_links or None, deep_link)
        print(f"[notify] send_email_to_roles completed for event={req.event!r}")

        # Send retainer email to office@greenlamp.co when an article is published
        if req.event == "published":
            print(f"[notify/retainer] firing send_retainer_email — client={req.client_name!r} magazine={req.magazine!r} doc_url={_client_doc_url!r}")
            try:
                await run_in_threadpool(
                    send_retainer_email,
                    req.client_name,
                    req.magazine,
                    _client_doc_url,
                )
                print("[notify/retainer] send_retainer_email completed OK")
            except Exception as retainer_err:
                print(f"[notify/retainer] ERROR: {retainer_err}")
        else:
            print(f"[notify/retainer] skipped — event is {req.event!r}, not 'published'")

        # When an article is submitted, kick off price fetching in the background
        # so Or sees prices already populated when he opens the article.
        if req.event == "submitted" and req.article_id and req.magazine:
            background_tasks.add_task(
                _bg_fetch_prices,
                req.article_id,
                extract_domain(req.magazine).lower(),
                req.client_name.strip(),
            )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── User management ───────────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    user_id:      str
    new_password: str


@app.post("/api/admin/change-password")
async def change_password(req: ChangePasswordRequest):
    """
    Change any user's password via the Supabase Auth admin API.
    Only callable by Or (role enforcement is on the frontend via route guard;
    the service-role key used here must never be exposed to non-Or users).
    """
    if len(req.new_password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters.")
    try:
        sb = _sb()
        result = sb.auth.admin.update_user_by_id(
            req.user_id,
            {"password": req.new_password},
        )
        if result.user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/users")
async def list_users():
    """Return id + email + role for all users (used by the admin page)."""
    try:
        sb = _sb()
        auth_resp   = sb.auth.admin.list_users()
        profile_resp = sb.from_("profiles").select("id, role").execute()
        role_map = {p["id"]: p.get("role") for p in (profile_resp.data or [])}

        users = [
            {
                "id":    u.id,
                "email": u.email,
                "role":  role_map.get(u.id),
            }
            for u in auth_resp
        ]
        return {"users": users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Dynamic user management (Or only) ─────────────────────────────────────────
# Role enforcement is on the frontend route guard, matching the existing
# /api/admin/* endpoints. The service-role key used here must never reach
# non-Or users.

# Must stay in sync with the profiles.role CHECK constraint.
ALLOWED_ROLES = {"or", "denise", "publisher"}


class CreateUserRequest(BaseModel):
    email:    str
    password: str
    role:     str


class UpdateUserRoleRequest(BaseModel):
    user_id: str
    role:    str


class DeleteUserRequest(BaseModel):
    user_id: str


def _count_users_with_role(sb, role: str) -> int:
    resp = sb.from_("profiles").select("id").eq("role", role).execute()
    return len(resp.data or [])


@app.post("/api/admin/create-user")
async def create_user(req: CreateUserRequest):
    """Create a Supabase Auth user and its matching profiles row."""
    email = (req.email or "").strip().lower()
    role  = (req.role  or "").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="A valid email is required.")
    if len(req.password or "") < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters.")
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail=f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}.")

    sb = _sb()
    try:
        created = await run_in_threadpool(
            sb.auth.admin.create_user,
            {"email": email, "password": req.password, "email_confirm": True},
        )
    except Exception as e:
        # Most commonly a duplicate address.
        raise HTTPException(status_code=400, detail=f"Could not create user: {e}")

    if created.user is None:
        raise HTTPException(status_code=500, detail="Auth user was not created.")
    user_id = created.user.id

    try:
        # upsert, not insert: tolerates a DB trigger having already made the row.
        await run_in_threadpool(
            lambda: sb.from_("profiles")
                      .upsert({"id": user_id, "email": email, "role": role})
                      .execute()
        )
    except Exception as e:
        # Roll back the auth user so a failed profile write cannot leave an
        # orphaned login that the Users page would never show.
        try:
            await run_in_threadpool(sb.auth.admin.delete_user, user_id)
        except Exception as cleanup_err:
            print(f"[create-user] rollback failed for {user_id}: {cleanup_err}")
        raise HTTPException(status_code=500, detail=f"Could not create profile: {e}")

    print(f"[create-user] created {email!r} with role {role!r} ({user_id})")
    return {"ok": True, "user": {"id": user_id, "email": email, "role": role}}


@app.post("/api/admin/update-user-role")
async def update_user_role(req: UpdateUserRoleRequest):
    """Change a user's role in the profiles table."""
    role = (req.role or "").strip()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail=f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}.")

    sb = _sb()
    try:
        current = await run_in_threadpool(
            lambda: sb.from_("profiles").select("role").eq("id", req.user_id).execute()
        )
        rows = current.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="User profile not found.")

        # Refuse to demote the last 'or' — nobody could reach this page again.
        if rows[0].get("role") == "or" and role != "or":
            if await run_in_threadpool(_count_users_with_role, sb, "or") <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot change the role of the only remaining Or user.",
                )

        updated = await run_in_threadpool(
            lambda: sb.from_("profiles")
                      .update({"role": role})
                      .eq("id", req.user_id)
                      .execute()
        )
        if not (updated.data or []):
            raise HTTPException(status_code=404, detail="User profile not found.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    print(f"[update-user-role] {req.user_id} → {role!r}")
    return {"ok": True}


@app.post("/api/admin/delete-user")
async def delete_user(req: DeleteUserRequest):
    """Delete a user from Supabase Auth and the profiles table."""
    sb = _sb()
    try:
        current = await run_in_threadpool(
            lambda: sb.from_("profiles").select("role").eq("id", req.user_id).execute()
        )
        rows = current.data or []

        # Refuse to delete the last 'or' — that would lock everyone out of admin.
        if rows and rows[0].get("role") == "or":
            if await run_in_threadpool(_count_users_with_role, sb, "or") <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete the only remaining Or user.",
                )

        # profiles.id is ON DELETE CASCADE from auth.users, so removing the auth
        # user clears the profile too; the explicit delete below covers a profile
        # row that has no matching auth user.
        await run_in_threadpool(sb.auth.admin.delete_user, req.user_id)
        try:
            await run_in_threadpool(
                lambda: sb.from_("profiles").delete().eq("id", req.user_id).execute()
            )
        except Exception as e:
            print(f"[delete-user] profile row cleanup skipped for {req.user_id}: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    print(f"[delete-user] deleted {req.user_id}")
    return {"ok": True}
