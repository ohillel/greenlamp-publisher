"""
Check for stale articles and send reminder emails.
Called by APScheduler every hour.

- 'submitted' for 52+ hours → remind Or to review/approve
- 'approved'  for 52+ hours → remind Eden (publisher) to send to publisher

Uses reminder_sent boolean column to ensure at most one reminder per article
per status. The flag is reset to FALSE whenever the article status changes
(handled in the frontend on status transitions).
"""
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client

REMINDER_HOURS = 52


def _sb():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def check_stale_articles() -> None:
    """Send reminder emails for articles stale in 'submitted' or 'approved' for 52+ hours."""
    from .email_notifications import send_email_to_roles

    print("[reminder] checking for stale articles…")
    try:
        sb     = _sb()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=REMINDER_HOURS)).isoformat()

        # ── Stale submitted articles → remind Or ──────────────────────────────
        submitted_res = sb.from_("articles") \
            .select("id, magazine, clients(name)") \
            .eq("status", "submitted") \
            .eq("reminder_sent", False) \
            .lt("updated_at", cutoff) \
            .execute()

        for article in (submitted_res.data or []):
            client_name = (article.get("clients") or {}).get("name", "Unknown")
            magazine    = article.get("magazine") or "Unknown"
            subject     = f"Reminder: Article from {client_name} waiting for approval"
            body        = (
                f"Article from {client_name} for {magazine} is waiting for your "
                f"approval for over {REMINDER_HOURS} hours"
            )
            print(f"[reminder] submitting reminder for article {article['id']} "
                  f"({client_name} → {magazine})")
            try:
                send_email_to_roles(["or"], subject, body)
            except Exception as e:
                print(f"[reminder] email error (submitted): {e}")
            sb.from_("articles").update({"reminder_sent": True}) \
              .eq("id", article["id"]).execute()

        # ── Stale approved articles → remind Publisher (Eden) ─────────────────
        approved_res = sb.from_("articles") \
            .select("id, magazine, clients(name)") \
            .eq("status", "approved") \
            .eq("reminder_sent", False) \
            .lt("updated_at", cutoff) \
            .execute()

        for article in (approved_res.data or []):
            client_name = (article.get("clients") or {}).get("name", "Unknown")
            magazine    = article.get("magazine") or "Unknown"
            subject     = f"Reminder: Article from {client_name} approved and waiting"
            body        = (
                f"Article from {client_name} for {magazine} has been approved and is "
                f"waiting to be sent to publisher for over {REMINDER_HOURS} hours"
            )
            print(f"[reminder] approved reminder for article {article['id']} "
                  f"({client_name} → {magazine})")
            try:
                send_email_to_roles(["publisher"], subject, body)
            except Exception as e:
                print(f"[reminder] email error (approved): {e}")
            sb.from_("articles").update({"reminder_sent": True}) \
              .eq("id", article["id"]).execute()

        total = len(submitted_res.data or []) + len(approved_res.data or [])
        print(f"[reminder] done — {total} reminder(s) sent")

    except Exception as e:
        print(f"[reminder] ERROR: {e}")
