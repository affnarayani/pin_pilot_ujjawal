import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

# Load environment variables
load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/blogger"]
ENV_FILE = Path(__file__).resolve().parent / ".env"


def update_env_token(token_json_str: str):
    """Update TOKEN_JSON_STR in .env without creating token.json."""
    key = "TOKEN_JSON_STR"
    new_line = f"{key}={token_json_str}\n"

    if ENV_FILE.exists():
        with ENV_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        replaced = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(new_line)

        with ENV_FILE.open("w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)
    else:
        with ENV_FILE.open("w", encoding="utf-8", newline="\n") as f:
            f.write(new_line)

    print(f"[OK] TOKEN_JSON_STR updated in: {ENV_FILE}", flush=True)


def generate_new_token():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("[ERROR] GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not found in environment.", flush=True)
        return

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }

    token_json_str = json.dumps(token_data, separators=(",", ":"))
    update_env_token(token_json_str)

    print("[OK] New token generated. json/token.json was not created.", flush=True)
    print("[INFO] Copy the TOKEN_JSON_STR value below to update GitHub Secret manually:", flush=True)
    print(token_json_str, flush=True)


if __name__ == "__main__":
    generate_new_token()
