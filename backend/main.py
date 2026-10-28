import os
import io
import asyncio
import contextlib
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
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
from scraper import presswhizz, linksme, collaborator, price_catalog                                              # noqa: E402


def _sb():
    """Return a Supabase service-role client (bypasses RLS)."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

CHECK_INTERVAL_MINUTES = 10


# How far back to look for a price fetch a restart left unfinished, and how
# many to pick up at once. Anything older than this was not interrupted by a
# restart — it is an article that has simply never been checked.
_ORPHAN_LOOKBACK_HOURS = 48
_ORPHAN_LIMIT = 25

# asyncio keeps only a weak reference to a task, so one with no strong
# reference anywhere can be collected before it runs. These are held until
# they finish.
_RECOVERY_TASKS: set = set()


def _recover_orphaned_price_fetches() -> None:
    """
    Re-queue price fetches that a restart killed mid-run.

    A fetch runs as a background task inside the web process, so a deploy ends
    it without a trace: prices_checked_at is never stamped, no error is
    recorded, and the dashboard shows "Fetching…" for good. Three articles
    submitted around the 12:07 deploy on 28 Oct were left exactly like that
    while articles submitted a minute later completed normally.

    Nothing else would ever retry them, so startup — the moment right after the
    restart that caused it — is where they get picked back up.
    """
    try:
        sb = _sb()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_ORPHAN_LOOKBACK_HOURS)).isoformat()
        rows = (sb.from_("articles")
                  .select("id, magazine, created_at")
                  .is_("prices_checked_at", "null")
                  .gte("created_at", cutoff)
                  .order("created_at", desc=True)
                  .limit(_ORPHAN_LIMIT)
                  .execute().data) or []
        pending = [r for r in rows if (r.get("magazine") or "").strip()]
        if not pending:
            print("[startup] no interrupted price fetches to recover")
            return
        print(f"[startup] recovering {len(pending)} interrupted price fetch(es)")
        for r in pending:
            task = asyncio.create_task(
                _bg_fetch_prices(r["id"], extract_domain(r["magazine"]).lower(), "")
            )
            _RECOVERY_TASKS.add(task)
            task.add_done_callback(_RECOVERY_TASKS.discard)
    except Exception as e:
        # Recovery is best-effort: never let it stop the app from starting.
        print(f"[startup] could not recover interrupted price fetches: {e}")


def _close_interrupted_price_checks() -> None:
    """
    Fail any Price Check run a restart left mid-flight.

    The run lives in a background task inside the web process, so a deploy
    ends it with its row still reading 'running' and the modal polling for a
    result that will never arrive. Nothing resumes it — the run is cheap to
    start again, and saying so beats a progress bar that never moves.
    """
    try:
        sb = _sb()
        stale = (sb.from_("price_check_runs")
                   .select("id, completed, total")
                   .eq("status", "running")
                   .execute().data) or []
        if not stale:
            return
        for row in stale:
            sb.from_("price_check_runs").update({
                "status": "error",
                "error": (f"Interrupted by a server restart after "
                          f"{row.get('completed', 0)} of {row.get('total', 0)} "
                          f"site(s). Please run it again."),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", row["id"]).execute()
        print(f"[startup] closed {len(stale)} price check run(s) a restart interrupted")
    except Exception as e:
        print(f"[startup] could not close interrupted price checks: {e}")


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
    _recover_orphaned_price_fetches()
    _close_interrupted_price_checks()
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
async def price_check_bulk(req: BulkPriceCheckRequest, background_tasks: BackgroundTasks):
    """
    Or-only ad-hoc price checker: PressWhizz live plus the catalog prices, for
    a pasted list of URLs/domains, exported to a new Google Sheet.

    Starts the run and returns its id at once rather than holding the request
    open. Only PressWhizz still opens a browser, but it does so per domain, so
    a long list takes minutes — far longer than a connection survives, and a
    38-domain list on 28 Oct showed the browser an error while the server was
    still working through it. Progress and the finished sheet are read back
    from /api/price-check/status/{run_id}.

    Role enforcement is on the frontend, same as the other Or-only endpoints —
    the service-role key used here must never be exposed to non-Or users.
    Does not touch the per-article scraping flow (prices.py) or its sessions.
    """
    urls = [u.strip() for u in req.urls if u.strip()]
    if not urls:
        raise HTTPException(status_code=422, detail="urls must contain at least one entry")

    sb = _sb()
    run = sb.from_("price_check_runs").insert({
        "urls": urls, "total": len(urls), "completed": 0, "status": "running",
    }).execute()
    run_id = (run.data or [{}])[0].get("id")
    if not run_id:
        raise HTTPException(status_code=500, detail="could not start the price check")

    print(f"[price-check/bulk] run {run_id} started — {len(urls)} url(s)")
    background_tasks.add_task(_run_price_check, run_id, urls)
    return {"run_id": run_id, "status": "running", "total": len(urls)}


async def _run_price_check(run_id: str, urls: list[str]) -> None:
    """Work through one Price Check run, recording progress as it goes."""
    sb = _sb()

    def record_progress(done: int, total: int) -> None:
        sb.from_("price_check_runs").update({"completed": done}).eq("id", run_id).execute()

    try:
        # Shares _PRICE_FETCH_SEMAPHORE with _bg_fetch_prices so the two never
        # run more than one Playwright session between them — otherwise a bulk
        # check running beside article fetches exhausts Railway's memory and
        # crashes Chromium.
        async with _PRICE_FETCH_SEMAPHORE:
            results = await run_in_threadpool(check_prices_bulk, urls, record_progress)

        # A single domain is read straight off the screen, so there is no
        # sheet to create. Anything more still exports as before.
        sheet_url = None
        if len(urls) > 1:
            print(f"[price-check/bulk] run {run_id}: prices fetched, creating sheet…")
            sheet_url = await run_in_threadpool(create_price_check_sheet, results)
            print(f"[price-check/bulk] run {run_id}: sheet created: {sheet_url}")

        sb.from_("price_check_runs").update({
            "status": "done", "results": results, "sheet_url": sheet_url,
            "completed": len(results), "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).execute()
        print(f"[price-check/bulk] run {run_id} finished")
    except Exception as e:
        print(f"[price-check/bulk] run {run_id} FAILED: {e}")
        try:
            sb.from_("price_check_runs").update({
                "status": "error", "error": str(e)[:500],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
        except Exception as e2:
            print(f"[price-check/bulk] run {run_id}: could not record the error: {e2}")


@app.get("/api/price-check/status/{run_id}")
async def price_check_status(run_id: str):
    """How far one Price Check run has got, and its results once it is done."""
    rows = (_sb().from_("price_check_runs")
                 .select("id, total, completed, status, results, sheet_url, error")
                 .eq("id", run_id).execute().data) or []
    if not rows:
        raise HTTPException(status_code=404, detail="price check run not found")
    return rows[0]


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
    all three sources (Links.me under the default client "mstone") and returns
    every log line printed during the run, so selector/flow issues can be
    diagnosed without Railway log access. Does not touch the per-article
    scraping flow.

    The three run one after another, each on its own, so a result here is free
    of the resource contention a concurrent article fetch would add — which is
    what makes it a useful control when a source times out in production.
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
    # Collaborator returns None on every failure path — missing API key, no
    # matching domain, no Article format — so the number alone cannot say which
    # happened. Its log lines can.
    co_result = await run_in_threadpool(
        _run_with_captured_logs, collaborator.get_price_detailed, domain, True
    )

    return {
        "domain": domain,
        "client_name": client_name,
        "presswhizz": pw_result,
        "linksme": lm_result,
        "collaborator": co_result,
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
# Links.me; Collaborator is a plain API call and costs nothing here).
# Railway's resources get exhausted when several articles are submitted at
# once and all their fetches run concurrently — this caps how many
# _bg_fetch_prices calls actually run at the same time; the rest queue and run
# as slots free up.
#
# One, not two. At two, a pair of fetches puts four Chromium instances on one
# Railway container, and PressWhizz then loses its 60s race: two re-checks
# started a second apart on 26 Oct both came back
# "presswhizz: timed out after 60s" while Links.me, in the same runs,
# succeeded. The Price Check tool never hits this — it holds a single slot for
# its whole batch and walks the domains one at a time, which is why the same
# domain prices fine there and times out here. Matching that gives the
# per-article path the two-browser ceiling that demonstrably works; several
# articles submitted together now queue instead of starving each other.
_PRICE_FETCH_SEMAPHORE = asyncio.Semaphore(1)


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
            "price_presswhizz":   result.get("presswhizz"),
            "price_linksme":      result.get("linksme"),
            "price_prnews":       result.get("prnews"),
            "price_collaborator": result.get("collaborator"),
            "prices_checked_at": datetime.now(timezone.utc).isoformat(),
            "price_fetch_error": "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None,
        }).eq("id", article_id).execute()
        print(f"[bg_prices] saved — pw={result.get('presswhizz')} lm={result.get('linksme')} "
              f"pr={result.get('prnews')} co={result.get('collaborator')} errors={errors}")
    except Exception as e:
        print(f"[bg_prices] ERROR for article {article_id}: {e}")
        try:
            sb.from_("articles").update({
                "prices_checked_at": datetime.now(timezone.utc).isoformat(),
                "price_fetch_error": str(e),
            }).eq("id", article_id).execute()
        except Exception as e2:
            print(f"[bg_prices] failed to record error for article {article_id}: {e2}")


def _import_price_file(source: str, content: bytes, filename: str) -> None:
    """Background half of an upload: parse, replace, and record the outcome."""
    sb = _sb()
    try:
        summary = price_catalog.sync_from_file(sb, source, content, filename)
        print(f"[publisher-prices/upload] {summary}")
    except Exception as e:
        print(f"[publisher-prices/upload] {source} FAILED: {e}")
        try:
            price_catalog.mark_failed(sb, source, filename, str(e))
        except Exception as e2:
            print(f"[publisher-prices/upload] could not record the error: {e2}")


@app.post("/api/publisher-prices/upload")
async def upload_publisher_prices(
    background_tasks: BackgroundTasks,
    source: str = Form(...),
    file: UploadFile = File(...),
):
    """
    Load one publisher's magazine/price list from an uploaded CSV or TSV,
    replacing what was held for that source. Or-only, enforced on the frontend
    like the other Or-only endpoints.

    The catalogs run to hundreds of thousands of rows, so the import runs in
    the background and this returns at once; the source is marked 'syncing'
    until it finishes, and /status reports when it lands. A failed import
    leaves the previous prices untouched.
    """
    src = (source or "").strip().lower()
    if src not in price_catalog.SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of {', '.join(price_catalog.SOURCES)}",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")

    filename = file.filename or "upload"
    price_catalog.mark_syncing(_sb(), src, filename)
    print(f"[publisher-prices/upload] {src}: queued {filename} ({len(content)} bytes)")
    background_tasks.add_task(_import_price_file, src, content, filename)
    return {"ok": True, "source": src, "filename": filename, "status": "syncing"}


@app.post("/api/publisher-prices/refresh-articles")
async def refresh_article_catalog_prices(background_tasks: BackgroundTasks):
    """
    Re-apply the catalog to every article already in the system.

    Runs automatically after each import; exposed separately so the prices can
    be re-applied without re-uploading a file. PressWhizz is untouched — it is
    the one source still fetched live.
    """
    background_tasks.add_task(_refresh_articles_bg)
    return {"ok": True, "status": "refreshing"}


def _refresh_articles_bg() -> None:
    try:
        price_catalog.refresh_articles(_sb())
    except Exception as e:
        print(f"[publisher-prices/refresh-articles] FAILED: {e}")


class SyncPriceSheetRequest(BaseModel):
    source:    str   # 'linksme' | 'prnews' | 'collaborator'
    sheet_url: str


@app.post("/api/publisher-prices/sync")
async def sync_publisher_prices(req: SyncPriceSheetRequest):
    """
    Pull one publisher's magazine/price sheet into the catalog, replacing what
    was held for that source. Or-only, enforced on the frontend like the other
    Or-only endpoints.

    A failed read leaves the existing prices untouched and records the reason,
    so a broken or unshared sheet degrades to "still using the previous sync"
    rather than wiping the catalog.
    """
    sb = _sb()
    source = (req.source or "").strip().lower()
    if source not in price_catalog.SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of {', '.join(price_catalog.SOURCES)}",
        )
    try:
        summary = await run_in_threadpool(
            price_catalog.sync_source, sb, source, req.sheet_url
        )
        print(f"[publisher-prices/sync] {summary}")
        return summary
    except Exception as e:
        print(f"[publisher-prices/sync] {source} FAILED: {e}")
        try:
            sb.from_("publisher_price_sheets").upsert({
                "source":     source,
                "sheet_url":  req.sheet_url,
                "last_error": str(e),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e2:
            print(f"[publisher-prices/sync] could not record the error: {e2}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/publisher-prices/status")
async def publisher_prices_status():
    """Each source's sheet, when it last synced, and how many prices it holds."""
    sb = _sb()
    rows = (sb.from_("publisher_price_sheets").select("*").execute().data) or []
    by_source = {r["source"]: r for r in rows}
    return {
        "sources": [
            {
                "source":         s,
                "sheet_url":      (by_source.get(s) or {}).get("sheet_url"),
                "source_label":   (by_source.get(s) or {}).get("source_label"),
                "status":         (by_source.get(s) or {}).get("status"),
                "last_synced_at": (by_source.get(s) or {}).get("last_synced_at"),
                "row_count":      (by_source.get(s) or {}).get("row_count"),
                "last_error":     (by_source.get(s) or {}).get("last_error"),
            }
            for s in price_catalog.SOURCES
        ]
    }


class RefetchPricesRequest(BaseModel):
    article_id: str


@app.post("/api/prices/refetch")
async def refetch_prices(req: RefetchPricesRequest, background_tasks: BackgroundTasks):
    """
    Re-run the price fetch for a single article.

    The fetch otherwise happens exactly once, when the article is submitted, so
    an article whose scrape timed out keeps an empty price row forever and one
    submitted before a source was added never gets that source at all. This
    endpoint is the way out of both.

    The stored prices and prices_checked_at are cleared first: that is what the
    dashboard reads as "fetching", and clearing it reaches the open tables
    through the articles realtime channel, so the spinner appears right away.
    """
    sb = _sb()
    res = sb.from_("articles") \
            .select("id, magazine") \
            .eq("id", req.article_id) \
            .execute()
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="article not found")
    article = rows[0]
    magazine = article.get("magazine")
    if not magazine:
        raise HTTPException(status_code=422, detail="article has no magazine to look up")

    sb.from_("articles").update({
        "price_presswhizz":   None,
        "price_linksme":      None,
        "price_prnews":       None,
        "price_collaborator": None,
        "prices_checked_at":  None,
        "price_fetch_error":  None,
    }).eq("id", req.article_id).execute()

    print(f"[api/prices/refetch] article={req.article_id} magazine={magazine!r}")
    # The client name only reaches _bg_fetch_prices' log line — Links.me is
    # always looked up under the "apiiro" project — so it is not worth a second
    # query here.
    background_tasks.add_task(
        _bg_fetch_prices,
        req.article_id,
        extract_domain(magazine).lower(),
        "",
    )
    return {"ok": True}


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
