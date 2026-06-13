import os
import sys
import json
import time
import base64
import random
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

# curl_cffi से requests इम्पोर्ट कर रहे हैं जो क्लाउडफ्लेयर बाईपास करेगा
from curl_cffi import requests as curl_requests


# =========================
# CONFIG
# =========================
COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

PBKDF2_ITERATIONS = 200_000

# Valid Pinterest Board options
ALLOWED_BOARDS = [
    "Anxiety & Mental Peace",
    "Calm Mind Habits",
    "Focus & Mental Discipline",
    "Mental Clarity",
    "Overthinking Help",
    "Self-Improvement Psychology"
]


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# CRYPTO
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = _derive_key(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise RuntimeError("❌ Decryption failed (InvalidTag)")


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print("[STEP] Loading cookies...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
    cookies = json.loads(plaintext.decode("utf-8"))

    # normalize SameSite and PartitionKey
    for c in cookies:
        if "partitionKey" in c and isinstance(c["partitionKey"], dict):
            if "topLevelSite" in c["partitionKey"]:
                c["partitionKey"] = str(c["partitionKey"]["topLevelSite"])
            else:
                del c["partitionKey"]

        if "sameSite" in c:
            val = str(c["sameSite"]).lower()

            if val in ["no_restriction", "none", "unspecified", "null"]:
                c["sameSite"] = "None"
            elif val == "lax":
                c["sameSite"] = "Lax"
            elif val == "strict":
                c["sameSite"] = "Strict"
            else:
                c["sameSite"] = "Lax"

    print("[OK] Cookies loaded", flush=True)
    return cookies


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started with Curl_Cffi TLS Engine", flush=True)

    # File init/clear at the beginning
    article_file = Path("article.json")
    with article_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'article.json' cleared/initialized", flush=True)

    # ========================================================
    # LOAD PINTEREST IDEAS & PICK DYNAMIC TOPIC
    # ========================================================
    ideas_file = Path("pinterest_ideas.json")
    if not ideas_file.exists():
        print("[ERROR] pinterest_ideas.json nahi mila. Exiting...", flush=True)
        sys.exit(1)

    try:
        with ideas_file.open("r", encoding="utf-8") as f:
            ideas_list = json.load(f)
    except Exception as e:
        print(f"[ERROR] pinterest_ideas.json parse nahi ho paya: {e}", flush=True)
        sys.exit(1)

    # PIPELINE INTEGRITY LOCK VERIFICATION
    for item in ideas_list:
        if isinstance(item, dict):
            content_gen = item.get("content_generated", False)
            image_gen = item.get("image_generated", False)
            posted_state = item.get("posted", False)
            
            if not content_gen or not image_gen or not posted_state:
                print("[INFO] Last Pipeline Is Not Yet Finished. Exiting safely.", flush=True)
                sys.exit(0)

    subject_matter = None
    target_index = -1

    # Linear top-down scanning extraction matching clean unassigned entries
    for index, item in enumerate(ideas_list):
        if isinstance(item, str):
            subject_matter = item
            target_index = index
            break
        elif isinstance(item, dict):
            if "content_generated" not in item and "posted" not in item and "generated" not in item:
                subject_matter = item.get("title") or item.get("subject") or list(item.values())[0]
                target_index = index
                break

    if subject_matter is None or target_index == -1:
        print("[INFO] No ungenerated or unposted subject matter found inside array. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Dynamic Target Extracted: '{subject_matter}' at array index [{target_index}]", flush=True)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # ========================================================
    # CURL_CFFI TLS-SPOOFING INTERNAL API REQUEST (FIXED)
    # ========================================================
    # कुकीज़ को डिक्शनरी के बजाय सीधे एक सिंगल स्ट्रिंग (Header Format) में कनवर्ट करें
    cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "Cookie": cookie_string  # कुकीज़ को सीधे हेडर्स में इन्जेक्ट कर दिया
    }
    
    prompt = (
        f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
        f"Do not print any conversational commentary or markdown outside of that code block.\n\n"
        
        f"You are an elite expert Pinterest Marketer and conversion optimization copywriter. "
        f"Generate optimized metadata assets mapped explicitly to the design vectors provided below.\n\n"
        
        f"DATA INPUT PARAMETERS:\n"
        f"1. Target Topic: {subject_matter}\n\n"
        
        f"STRICT OUTPUT FIELD REQUIREMENT VALIDATION METRICS:\n"
        f"1. title: Must contain exactly the text data string: \"{subject_matter}\"\n"
        f"2. description: Craft a high-performing conversion optimized description tailored around consumer pain points related to the title. Include 3-5 trending relevant hashtags at the trailing edge of the value. Each hashtag must begin with the '#' symbol. Total length constraint must be strictly between 150 and 250 characters long.\n"
        f"3. alt_text: Must contain exactly the text data string: \"{subject_matter}\"\n"
        f"4. selected_board: Analyze the cognitive intent behind the topic and select the single most relevant category string match from this strict choice list array ONLY:\n"
        f"   - Anxiety & Mental Peace\n"
        f"   - Calm Mind Habits\n"
        f"   - Focus & Mental Discipline\n"
        f"   - Mental Clarity\n"
        f"   - Overthinking Help\n"
        f"   - Self-Improvement Psychology\n\n"
        
        f"OUTPUT FORMATTING TEMPLATE:\n"
        f"{{\n"
        f'  "title": "{subject_matter}",\n'
        f'  "description": "[Optimized description string content matching length boundaries]",\n'
        f'  "alt_text": "{subject_matter}",\n'
        f'  "selected_board": "[Exact matched string text chosen from given list]"\n'
        f"}}\n"
    )

    payload = {
        "action": "next",
        "messages": [{
            "id": "11111111-1111-1111-1111-111111111111",
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": [prompt]},
            "metadata": {}
        }],
        "model": "text-davinci-002-render-sha",
        "parent_message_id": "22222222-2222-2222-2222-222222222222",
        "timezone_offset_min": -330
    }

    print("[STEP] Sending spoofed TLS request via curl_cffi to bypass Cloudflare...", flush=True)
    json_content = None

    try:
        # cookies= आर्गुमेंट हटा दिया गया है क्योंकि सब कुछ अब headers में है
        response = curl_requests.post(
            "[https://chatgpt.com/backend-api/conversation](https://chatgpt.com/backend-api/conversation)",
            json=payload,
            headers=headers,
            impersonate="chrome120",
            timeout=60
        )
        
        if response.status_code == 200:
            print("[OK] Connection established and stream response fetched successfully.", flush=True)
            json_content = response.text
        else:
            print(f"[ERROR] Request failed with HTTP Status Code: {response.status_code}", flush=True)
            print("Raw Error Body:", response.text, flush=True)
            sys.exit(1)
            
    except Exception as req_err:
        print(f"[CRITICAL ERROR] Network submission failed: {req_err}", flush=True)
        sys.exit(1)

    # ========================================================
    # STREAM PARSING & JSON VALIDATION ENGINE
    # ========================================================
    if json_content:
        try:
            print("[STEP] Parsing content text stream...", flush=True)
            
            # चैटजीपीटी के एपीआई रिस्पॉन्स में से JSON कोडब्लॉक को एक्सट्रेक्ट करना
            if "```json" in json_content:
                json_content = json_content.split("```json", 1)[1].split("```", 1)[0]
            elif "{" in json_content and "}" in json_content:
                json_content = json_content[json_content.find("{"):json_content.rfind("}")+1]
            
            parsed_json = json.loads(json_content.strip())
            
            # Strict structural data synchronization constraints enforcement
            parsed_json["title"] = subject_matter
            parsed_json["alt_text"] = subject_matter
            
            # Fallback safeguard validation structure against broken board names
            if parsed_json.get("selected_board") not in ALLOWED_BOARDS:
                print(f"[WARNING] Invalid board string parsed: '{parsed_json.get('selected_board')}'. Appending arbitrary safe index variant.", flush=True)
                parsed_json["selected_board"] = random.choice(ALLOWED_BOARDS)

            print("[STEP] Saving formatted data inside article.json...", flush=True)
            with article_file.open("w", encoding="utf-8") as f:
                json.dump(parsed_json, f, indent=4, ensure_ascii=False)
            print("[OK] Data structural dump serialization write-out successful.", flush=True)
            
            # UPDATE STATE LOCK TRACKER WITH 3 KEY VALUES
            print("[STEP] Saving updated item state back to pinterest_ideas.json...", flush=True)
            ideas_list[target_index] = {
                "title": subject_matter,
                "content_generated": True,
                "image_generated": False,
                "posted": False
            }

            with ideas_file.open("w", encoding="utf-8") as f:
                json.dump(ideas_list, f, indent=2, ensure_ascii=False)
            print(f"✅ Success: '{subject_matter}' registered with content_generated=True.", flush=True)
            
        except json.JSONDecodeError as je:
            print(f"[ERROR] Content JSON parse karne me fail hua: {je}. Exiting script...", flush=True)
            sys.exit(1)
    else:
        print("[ERROR] Save skip kiya gaya kyunki koi data fetch nahi hua. Exiting script...", flush=True)
        sys.exit(1)

    print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()