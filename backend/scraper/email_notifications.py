"""
Email notifications via Resend (resend.com).

Environment variables required:
  RESEND_API_KEY    – API key from resend.com dashboard
  RESEND_FROM_EMAIL – (optional) verified sender address, defaults to
                      "Greenlamp Publisher <onboarding@resend.dev>"
                      Switch to a verified domain address for production.
"""
import os
import resend

APP_URL = "https://greenlamp-publisher.vercel.app"

# Hard-coded role → email mapping (small internal team, no DB lookup needed)
_ROLE_EMAILS: dict[str, str] = {
    "or":        "seojobisrael@gmail.com",
    "publisher": "office@greenlamp.co",
    "denise":    "denise@greenlamp.co",
}


def send_email_to_roles(roles: list[str], subject: str, body_text: str) -> None:
    """
    Send an email to every address mapped from `roles`.
    Silently skips if RESEND_API_KEY is not configured.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        print("[email] RESEND_API_KEY not set — skipping")
        return

    resend.api_key = api_key

    to_emails = [_ROLE_EMAILS[r] for r in roles if r in _ROLE_EMAILS]
    if not to_emails:
        print(f"[email] no addresses mapped for roles {roles!r} — skipping")
        return

    from_addr = os.environ.get(
        "RESEND_FROM_EMAIL",
        "Greenlamp Publisher <onboarding@resend.dev>",
    )

    html = f"""
<div style="font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#1a1a1a">
  <p style="font-size:16px;margin:0 0 20px">{body_text}</p>
  <a href="{APP_URL}"
     style="display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
            padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500">
    Open Greenlamp Publisher →
  </a>
</div>
"""

    try:
        resend.Emails.send({
            "from":    from_addr,
            "to":      to_emails,
            "subject": subject,
            "html":    html,
        })
        print(f"[email] sent to {to_emails!r}: {subject!r}")
    except Exception as e:
        print(f"[email] error sending to {to_emails!r}: {e}")
