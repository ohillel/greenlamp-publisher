"""
Email notifications via Gmail SMTP.

Environment variables required:
  GMAIL_USER         – Gmail address used to send (e.g. or@greenlamp.co)
  GMAIL_APP_PASSWORD – 16-character Gmail App Password (not the account password)
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

APP_URL = "https://greenlamp-publisher.vercel.app"

_ROLE_EMAILS: dict[str, str] = {
    "or":        "seojobisrael@gmail.com",
    "publisher": "office@greenlamp.co",
    "denise":    "denise@greenlamp.co",
}


def send_email_to_roles(roles: list[str], subject: str, body_text: str) -> None:
    """
    Send an email to every address mapped from `roles`.
    Silently skips if Gmail credentials are not configured.
    """
    gmail_user     = os.environ.get("GMAIL_USER", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_password:
        print("[email] GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping")
        return

    to_emails = [_ROLE_EMAILS[r] for r in roles if r in _ROLE_EMAILS]
    if not to_emails:
        print(f"[email] no addresses mapped for roles {roles!r} — skipping")
        return

    html = f"""\
<div style="font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#1a1a1a">
  <p style="font-size:16px;margin:0 0 20px">{body_text}</p>
  <a href="{APP_URL}"
     style="display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
            padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500">
    Open Greenlamp Publisher →
  </a>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = ", ".join(to_emails)
    msg.attach(MIMEText(html, "html"))

    try:
        import ssl
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=10) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.sendmail(gmail_user, to_emails, msg.as_string())
        print(f"[email] sent to {to_emails!r}: {subject!r}")
    except Exception as e:
        print(f"[email] error sending to {to_emails!r}: {e}")
