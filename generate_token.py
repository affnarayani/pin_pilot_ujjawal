import os
import json
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

# .env se variables load karne ke liye
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/blogger']

def generate_new_token():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        print("❌ Error: .env file mein GOOGLE_CLIENT_ID ya GOOGLE_CLIENT_SECRET nahi mila!")
        return

    # Client configuration dictionary
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }
    
    # Auth flow start karein
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    
    # Naya token data structure
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None
    }
    
    # 1. Local token.json file save karein
    with open('token.json', 'w') as f:
        json.dump(token_data, f, indent=2)
    print("\n✅ 'token.json' file successfully aapke folder mein ban gayi hai!")
    
    # 2. .env ya GitHub Secrets ke liye string print karein
    print("\n👇 Agar aap GitHub Secrets ya .env mein string use karte hain, toh is POORI line ko copy karein:")
    print(json.dumps(token_data))

if __name__ == "__main__":
    generate_new_token()