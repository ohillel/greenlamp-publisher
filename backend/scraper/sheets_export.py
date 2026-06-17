"""
Creates a Google Sheet with bulk price-check results.

Uses the same OAuth2 token as Gmail (GOOGLE_TOKEN_JSON), with the
spreadsheets + drive.file scopes added — see google_auth.py / google_client.py.
"""
import os
import sys
from datetime import datetime


def _sheets_service():
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from google_client import get_credentials
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("sheets", "v4", credentials=creds)


def create_price_check_sheet(rows: list[dict]) -> str:
    """
    rows: list of {original_url, price_presswhizz, price_linksme}
    Creates a new spreadsheet titled "Price Check — DD.MM.YYYY", writes one
    row per entry, and returns the spreadsheet's URL.
    """
    title = f"Price Check — {datetime.now().strftime('%d.%m.%Y')}"
    service = _sheets_service()

    spreadsheet = service.spreadsheets().create(
        body={"properties": {"title": title}},
        fields="spreadsheetId,spreadsheetUrl",
    ).execute()
    spreadsheet_id = spreadsheet["spreadsheetId"]

    values = [["URL", "PressWhizz Price", "Links.me Price"]]
    for row in rows:
        pw = row.get("price_presswhizz")
        lm = row.get("price_linksme")
        values.append([
            row.get("original_url", ""),
            pw if pw is not None else "",
            lm if lm is not None else "",
        ])

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    return spreadsheet["spreadsheetUrl"]
