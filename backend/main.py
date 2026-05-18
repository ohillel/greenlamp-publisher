import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
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
from scraper.push_notifications import send_push_to_roles    # noqa: E402
from scraper.email_notifications import send_email_to_roles  # noqa: E402


def _sb():
    """Return a Supabase service-role client (bypasses RLS)."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

CHECK_INTERVAL_MINUTES = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_status_check,
        trigger='interval',
        minutes=CHECK_INTERVAL_MINUTES,
        id='status_check',
        max_instances=1,       # never overlap — wait for previous run to finish
        coalesce=True,         # skip missed fires if the server was paused
    )
    scheduler.start()
    print(f"[scheduler] started — publication status check every {CHECK_INTERVAL_MINUTES} minutes")
    yield
    scheduler.shutdown(wait=False)
    print("[scheduler] stopped")


_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173",
).split(",") if o.strip()]

app = FastAPI(title="Greenlamp Publisher API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


class NotifyRequest(BaseModel):
    event:       str   # 'submitted' | 'approved' | 'sent'
    client_name: str
    magazine:    str


_NOTIFY_MAP: dict[str, tuple[list[str], str]] = {
    # event → (recipient_roles, body_template)
    "submitted": (["or"],           "New article for {client} → {magazine}"),
    "approved":  (["publisher"],    "Ready to send for {client} → {magazine}"),
    "sent":      (["or", "denise"], "Article sent for {client} → {magazine}"),
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
        "sender": "seojobisrael@gmail.com",
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


@app.post("/api/notify")
async def notify(req: NotifyRequest):
    """Send a push notification to the appropriate roles for a status-change event."""
    entry = _NOTIFY_MAP.get(req.event)
    if not entry:
        raise HTTPException(status_code=422, detail=f"Unknown event: {req.event!r}")
    roles, body_template = entry
    body  = body_template.format(
        client=req.client_name,
        magazine=extract_domain(req.magazine),
    )
    title = "Greenlamp Publisher"
    try:
        sb = _sb()
        await run_in_threadpool(send_push_to_roles, sb, roles, title, body)
        await run_in_threadpool(send_email_to_roles, roles, body, body)
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
