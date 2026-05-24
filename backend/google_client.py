import os, json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_credentials():
    creds = None
    token_json = os.getenv("GOOGLE_TOKEN_JSON")
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds:
        raise Exception("Google credentials not configured. Set GOOGLE_TOKEN_JSON env var.")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds
