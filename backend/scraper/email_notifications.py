"""
Email notifications via Gmail API (OAuth2).

Environment variable required:
  GOOGLE_TOKEN_JSON – contents of the OAuth2 token.json file for seojobisrael@gmail.com

Sender: seojobisrael@gmail.com
"""
import os
import base64
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

APP_URL = "https://greenlamp-publisher.vercel.app"

_ROLE_EMAILS: dict[str, str] = {
    "or":        "seojobisrael@gmail.com",
    "publisher": "office@greenlamp.co",
    "denise":    "denise@greenlamp.co",
}

SENDER = "seojobisrael@gmail.com"


def _gmail_service():
    # Import here so the module loads even if google libs aren't installed yet
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from google_client import get_credentials
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def send_email_to_roles(
    roles: list[str],
    subject: str,
    body_text: str,
    extra_links: list[dict] | None = None,
    deep_link: str | None = None,
) -> None:
    """
    Send an email to every address mapped from `roles` using the Gmail API.
    Silently skips if GOOGLE_TOKEN_JSON is not configured.

    deep_link:   if provided, the main CTA button links directly to the article
                 instead of the generic app URL.
    extra_links: optional list of {"url": str, "label": str} rendered as
                 additional link buttons in the HTML email and plain-text URLs.
    """
    if not os.environ.get("GOOGLE_TOKEN_JSON"):
        print("[email] GOOGLE_TOKEN_JSON not set — skipping")
        return

    to_emails = [_ROLE_EMAILS[r] for r in roles if r in _ROLE_EMAILS]
    if not to_emails:
        print(f"[email] no addresses mapped for roles {roles!r} — skipping")
        return

    # Build extra link buttons for HTML
    extra_html = ""
    extra_plain = ""
    if extra_links:
        for link in extra_links:
            url   = link.get("url",   "")
            label = link.get("label", url)
            if url:
                extra_html  += (
                    f'<a href="{url}" style="display:inline-block;margin-top:10px;'
                    f'background:#1d4ed8;color:#fff;text-decoration:none;'
                    f'padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500">'
                    f'{label} →</a><br>\n'
                )
                extra_plain += f"\n{label}: {url}"

    cta_url   = deep_link or APP_URL
    cta_label = "View Article →" if deep_link else "Open Greenlamp Publisher →"

    html = f"""\
<div style="font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#1a1a1a">
  <p style="font-size:16px;margin:0 0 20px">{body_text}</p>
  <a href="{cta_url}"
     style="display:inline-block;background:#16a34a;color:#fff;text-decoration:none;
            padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500">
    {cta_label}
  </a><br>
  {extra_html}
</div>
"""
    plain_text = body_text
    if deep_link:
        plain_text += f"\n\nView Article: {deep_link}"
    plain_text += extra_plain

    try:
        service = _gmail_service()
        for to_addr in to_emails:
            msg = MIMEMultipart("alternative")
            msg["From"]    = SENDER
            msg["To"]      = to_addr
            msg["Subject"] = subject
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
            msg.attach(MIMEText(html,       "html",  "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()
            print(f"[email] sent to {to_addr!r}: {subject!r}")
    except Exception as e:
        print(f"[email] error sending to {to_emails!r}: {e}")
