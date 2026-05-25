"""
Run this script once locally to generate token.json for the greenlamp-publisher service.
Then copy its contents into the GOOGLE_TOKEN_JSON environment variable in Railway.

Usage:
    python google_auth.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=8080, prompt='consent')

with open("token.json", "w") as f:
    f.write(creds.to_json())

print("\n✅ Login successful! token.json created.")
print("Copy the content below into GOOGLE_TOKEN_JSON in Railway:\n")
print(open("token.json").read())
