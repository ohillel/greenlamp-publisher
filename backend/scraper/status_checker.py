"""
Periodic status checker: queries Supabase for all articles with
status='sent_to_publisher', scrapes the relevant publisher site,
and updates the article status to 'published' or 'not_published' when changed.

Called by the APScheduler job in main.py every 10 minutes.
"""
import os
from supabase import create_client
from . import check_presswhizz, check_linksme
from .push_notifications import send_push_to_roles
from .email_notifications import send_email_to_roles

_PUSH_BODIES = {
    "published":     "✅ Published for {client} → {magazine}",
    "not_published": "❌ Rejected for {client} → {magazine}",
}


def _domain(url: str) -> str:
    """Strip protocol, www., and trailing path — return bare domain."""
    url = url.strip()
    if url.startswith("http"):
        from urllib.parse import urlparse
        url = urlparse(url).netloc
    return url.replace("www.", "").split("/")[0].strip()


def _supabase_client():
    url = os.environ["SUPABASE_URL"]
    # Use service role key so RLS does not block reads/writes
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def run_status_check(debug: bool = False) -> None:
    """
    Main entry point — run one full sweep of all sent_to_publisher articles.
    Safe to call from any thread; all Playwright work happens inside
    check_presswhizz.check_batch / check_linksme.check_batch.
    """
    print("[checker] starting status check run")

    try:
        sb = _supabase_client()
    except Exception as e:
        print(f"[checker] ERROR: could not connect to Supabase: {e}")
        return

    # ── 1. Fetch all sent_to_publisher articles with their client name ──────
    try:
        res = (
            sb.from_("articles")
            .select("id, magazine, chosen_publisher, clients(name)")
            .eq("status", "sent_to_publisher")
            .execute()
        )
    except Exception as e:
        print(f"[checker] ERROR fetching articles: {e}")
        return

    rows = res.data or []
    if not rows:
        print("[checker] no articles with status=sent_to_publisher — nothing to do")
        return

    print(f"[checker] found {len(rows)} article(s) to check")

    # ── 2. Normalise rows and group by publisher ────────────────────────────
    pw_articles: list[dict] = []
    lm_articles: list[dict] = []

    for row in rows:
        client_name = (row.get("clients") or {}).get("name", "")
        article = {
            "id":          row["id"],
            "magazine":    _domain(row.get("magazine") or ""),
            "client_name": client_name,
        }
        publisher = (row.get("chosen_publisher") or "").lower()

        print(
            f"[checker] checking article ID={row['id']} "
            f"magazine={article['magazine']!r} "
            f"client={client_name!r} "
            f"publisher={publisher!r}"
        )

        if publisher == "presswhizz":
            pw_articles.append(article)
        elif publisher == "linksme":
            lm_articles.append(article)
        else:
            print(f"[checker] unknown publisher {publisher!r} for article {row['id']} — skipping")

    # ── 3. Run scrapers (each opens its own browser session) ───────────────
    all_results: dict[str, str] = {}

    if pw_articles:
        print(f"[checker] running PressWhizz check for {len(pw_articles)} article(s)…")
        try:
            pw_results = check_presswhizz.check_batch(pw_articles, debug=debug)
            all_results.update(pw_results)
        except Exception as e:
            print(f"[checker] ERROR in PressWhizz check: {e}")

    if lm_articles:
        print(f"[checker] running Links.me check for {len(lm_articles)} article(s)…")
        try:
            lm_results = check_linksme.check_batch(lm_articles, debug=debug)
            all_results.update(lm_results)
        except Exception as e:
            print(f"[checker] ERROR in Links.me check: {e}")

    # ── 4. Write status changes back to Supabase ───────────────────────────
    if not all_results:
        print("[checker] no status changes detected this run")
        return

    # Build lookup: article_id → {client, magazine} for notification bodies
    article_meta = {
        a["id"]: {"client": a["client_name"], "magazine": a["magazine"]}
        for a in pw_articles + lm_articles
    }

    for article_id, new_status in all_results.items():
        try:
            sb.from_("articles").update({"status": new_status}).eq("id", article_id).execute()
            print(f"[checker] updated article ID={article_id} to status={new_status!r}")
        except Exception as e:
            print(f"[checker] ERROR updating article {article_id}: {e}")
            continue

        if new_status in _PUSH_BODIES:
            meta = article_meta.get(article_id, {})
            body = _PUSH_BODIES[new_status].format(
                client=meta.get("client", ""),
                magazine=_domain(meta.get("magazine", "")),
            )
            send_push_to_roles(sb, ["or", "denise"], "Greenlamp Publisher", body)
            send_email_to_roles(["or", "denise"], body, body)

    print(f"[checker] run complete — {len(all_results)} article(s) updated")
