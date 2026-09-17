"""
Creates a Google Sheet with bulk price-check results.

Uses the same OAuth2 token as Gmail (GOOGLE_TOKEN_JSON), with the
spreadsheets + drive.file scopes added — see google_auth.py / google_client.py.
"""
import os
import sys
from datetime import datetime

# Column order in the sheet. PressWhizz and Links.me keep their existing
# plain-number formatting; the two EUR columns are written as numbers and
# given a € number format so they still sort and sum.
HEADERS = ["URL", "PressWhizz Price", "Links.me Price", "PRNews.io", "Collaborator.pro"]

# 0-based indices of the EUR columns (D and E).
_EUR_COLUMN_INDICES = (3, 4)
_EUR_NUMBER_FORMAT = '#,##0.00" €"'


def _sheets_service():
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from google_client import get_credentials
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("sheets", "v4", credentials=creds)


def create_price_check_sheet(rows: list[dict]) -> str:
    """
    rows: list of {original_url, price_presswhizz, price_linksme,
                   price_prnews, price_collaborator}
    Creates a new spreadsheet titled "Price Check — DD.MM.YYYY", writes one
    row per entry, and returns the spreadsheet's URL.
    """
    title = f"Price Check — {datetime.now().strftime('%d.%m.%Y')}"
    service = _sheets_service()

    spreadsheet = service.spreadsheets().create(
        body={"properties": {"title": title}},
        fields="spreadsheetId,spreadsheetUrl,sheets.properties.sheetId",
    ).execute()
    spreadsheet_id = spreadsheet["spreadsheetId"]
    sheet_id = spreadsheet["sheets"][0]["properties"]["sheetId"]

    values = [HEADERS]
    for row in rows:
        values.append([
            row.get("original_url", ""),
            _cell(row.get("price_presswhizz")),
            _cell(row.get("price_linksme")),
            _cell(row.get("price_prnews")),
            _cell(row.get("price_collaborator")),
        ])

    # USER_ENTERED (not RAW) so the numbers land as numbers rather than text,
    # which is what makes the € columns sortable and summable.
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()

    _apply_eur_format(service, spreadsheet_id, sheet_id, len(values))

    return spreadsheet["spreadsheetUrl"]


def _cell(value):
    """Blank for a missing price; the raw number otherwise."""
    return "" if value is None else value


def _apply_eur_format(service, spreadsheet_id: str, sheet_id: int, row_count: int) -> None:
    """
    Give the two EUR columns a currency number format.

    Best-effort: a formatting failure must not lose the data that was already
    written, so the sheet is still returned if this call errors.

    The € symbol is appended by the pattern; the decimal separator shown
    follows the spreadsheet's locale, so a sheet in a en-US locale renders
    113.40 € rather than 113,40 €. The stored value is identical either way.
    """
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,               # skip the header row
                    "endRowIndex": max(row_count, 2),
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "NUMBER", "pattern": _EUR_NUMBER_FORMAT}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }
        for col in _EUR_COLUMN_INDICES
    ]
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
    except Exception as e:
        print(f"  [sheets_export] could not apply the EUR number format: {e}")
