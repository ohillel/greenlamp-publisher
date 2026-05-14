"""
Web Push notification sender.

Uses VAPID (Voluntary Application Server Identification) to authenticate
push messages to the browser's push service.

Environment variables required:
  VAPID_PUBLIC_KEY   – base64url-encoded uncompressed P-256 public point
  VAPID_PRIVATE_KEY  – base64url-encoded 32-byte P-256 private scalar
  VAPID_EMAIL        – contact email included in VAPID claims (e.g. mailto:...)
"""
import json
import os
from pywebpush import webpush, WebPushException


def _vapid_claims() -> dict:
    email = os.environ.get("VAPID_EMAIL", "")
    sub   = email if email.startswith("mailto:") else f"mailto:{email}"
    return {"sub": sub}


def send_push(subscription: dict, title: str, body: str) -> None:
    """
    Send a single Web Push notification.

    subscription – the subscription object saved from the browser
                   (keys: endpoint, keys.p256dh, keys.auth)
    Raises WebPushException on delivery failure (caller should log and continue).
    """
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    if not private_key:
        print("[push] VAPID_PRIVATE_KEY not set — skipping notification")
        return

    payload = json.dumps({"title": title, "body": body})

    webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=private_key,
        vapid_claims=_vapid_claims(),
    )


def send_push_to_roles(sb, roles: list[str], title: str, body: str) -> None:
    """
    Look up all push subscriptions for users whose profile role is in `roles`,
    then send the notification to each one.

    sb – Supabase client (service role, so RLS is bypassed)
    """
    try:
        # Get user_ids for the target roles
        profiles_res = (
            sb.from_("profiles")
            .select("id")
            .in_("role", roles)
            .execute()
        )
        user_ids = [r["id"] for r in (profiles_res.data or [])]
        if not user_ids:
            return

        # Get all push subscriptions for those users
        subs_res = (
            sb.from_("push_subscriptions")
            .select("subscription")
            .in_("user_id", user_ids)
            .execute()
        )
        subscriptions = [r["subscription"] for r in (subs_res.data or [])]
    except Exception as e:
        print(f"[push] error fetching subscriptions: {e}")
        return

    for sub in subscriptions:
        try:
            send_push(sub, title, body)
        except WebPushException as e:
            # 410 Gone = subscription expired/revoked — log but don't crash
            print(f"[push] delivery failed ({e.response.status_code if e.response else '?'}): {e}")
        except Exception as e:
            print(f"[push] unexpected error: {e}")
