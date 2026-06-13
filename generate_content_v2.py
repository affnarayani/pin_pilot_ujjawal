import os
import sys
import json
import asyncio
import base64
import random
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

# nodriver को इम्पोर्ट कर रहे हैं
import nodriver as uc

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
# CRYPTO & COOKIE LOAD
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
    print("[OK] Cookies loaded", flush=True)
    return cookies

# =========================
# MAIN AUTOMATION LOGIC
# =========================
async def run():
    print("[START] Script started with Nodriver Engine", flush=True)

    article_file = Path("article.json")
    with article_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'article.json' cleared/initialized", flush=True)

    # LOAD PINTEREST IDEAS
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
            if not item.get("content_generated", False) or not item.get("image_generated", False) or not item.get("posted", False):
                print("[INFO] Last Pipeline Is Not Yet Finished. Exiting safely.", flush=True)
                sys.exit(0)

    subject_matter = None
    target_index = -1

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

    raw_cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))

    # NODRIVER BROWSER LAUNCH
    # browser.start() खुद ब खुद बिना झंझट के अनडिटेक्टेड क्रोमियम लोड करता है
    config = uc.Config()
    config.no_sandbox = True
    config.headless = True
    config.add_argument("--disable-gpu")
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--disable-software-rasterizer")
    print("[STEP] Launching Undetected Chrome via Nodriver...", flush=True)
    browser = await uc.start(config=config)
    
    try:
        page = await browser.get("https://chatgpt.com/")
        print("[OK] URL opened. Injecting cookies...", flush=True)
        await asyncio.sleep(5)

        # Nodriver में कुकीज़ सेट करने का फॉर्मेट
        for c in raw_cookies:
            cookie_dict = {
                "name": c["name"],
                "value": c["value"],
                "domain": ".chatgpt.com" if "chatgpt" in c.get("domain", "") else c.get("domain", "chatgpt.com"),
                "path": c.get("path", "/")
            }
            try:
                await browser.cookies.set(**cookie_dict)
            except Exception:
                pass

        # कुकीज़ सेट करने के बाद पेज रीलोड करें ताकि लॉगिन एक्टिव हो जाए
        page = await browser.get("https://chatgpt.com/")
        print("[OK] Page reloaded with active cookies session.", flush=True)
        await asyncio.sleep(random.uniform(15, 25))

        # FIND TEXTAREA (NODRIVER SELECTOR)
        print("[STEP] Locating chat textbox...", flush=True)
        textbox = None
        
        # Multiple fallbacks using nodriver find methods
        try:
            textbox = await page.find('textarea[id="prompt-textarea"]')
        except Exception:
            try:
                textbox = await page.find('div[contenteditable="true"]')
            except Exception:
                raise RuntimeError("❌ Textbox locator load nahi ho paya.")

        prompt = (
            f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
            f"Do not print any conversational commentary or markdown outside of that code block.\n\n"
            f"Target Topic: {subject_matter}\n\n"
            f"OUTPUT FORMATTING TEMPLATE:\n"
            f"{{\n"
            f'  "title": "{subject_matter}",\n'
            f'  "description": "[Optimized description string between 150-250 chars with 3-5 hashtags]",\n'
            f'  "alt_text": "{subject_matter}",\n'
            f'  "selected_board": "[Exact matched string text chosen from standard mental peace list]"\n'
            f"}}\n"
        )

        # TEXTBOX FILL & SUBMIT (DIRECT ELEMENT METHOD)
        print("[STEP] Typing prompt into ChatGPT...", flush=True)
        await textbox.click()
        await textbox.send_keys(prompt)
        await asyncio.sleep(5)

        # FIND AND CLICK SEND BUTTON
        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = None
        try:
            send_button = await page.find('button[data-testid="send-button"]')
        except Exception:
            try:
                send_button = await page.find('#composer-submit-button')
            except Exception:
                raise RuntimeError("❌ Send button not found.")

        await send_button.click()
        print("[OK] Prompt sent successfully.", flush=True)
        
        await asyncio.sleep(30)

        # LIVE STREAM JSON READING VIA PRE TAGS
        print("[STEP] Waiting for generated JSON code block...", flush=True)
        json_content = None
        
        for attempt in range(1, 8):
            print(f"[STEP] Checking code block (Attempt {attempt}/7)...", flush=True)
            await asyncio.sleep(10)
            
            try:
                # nodriver में पूरे पेज पर 'pre' टैग को खोज रहे हैं
                code_block = await page.find('pre')
                if code_block:
                    current_text = code_block.text.strip()
                    if "}" in current_text:
                        json_content = current_text
                        print("[OK] Complete JSON output captured.", flush=True)
                        break
            except Exception:
                pass

        if json_content:
            try:
                if "```json" in json_content:
                    json_content = json_content.split("```json", 1)[1]
                if "```" in json_content:
                    json_content = json_content.split("```", 1)[0]

                parsed_json = json.loads(json_content.strip())
                parsed_json["title"] = subject_matter
                parsed_json["alt_text"] = subject_matter

                if parsed_json.get("selected_board") not in ALLOWED_BOARDS:
                    parsed_json["selected_board"] = random.choice(ALLOWED_BOARDS)

                # SAVE OUTPUT
                with article_file.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print("[OK] 'article.json' saved successfully.", flush=True)

                # UPDATE ARRAYS STATE
                ideas_list[target_index] = {
                    "title": subject_matter,
                    "content_generated": True,
                    "image_generated": False,
                    "posted": False
                }
                with ideas_file.open("w", encoding="utf-8") as f:
                    json.dump(ideas_list, f, indent=2, ensure_ascii=False)
                print(f"✅ State updated for topic: {subject_matter}", flush=True)

            except Exception as json_err:
                print(f"[ERROR] JSON processing failed: {json_err}", flush=True)
                sys.exit(1)
        else:
            print("[ERROR] No content generated or timeout reached.", flush=True)
            sys.exit(1)

    except Exception as main_err:
        print(f"[CRITICAL ERROR] {main_err}", flush=True)
        try:
            await page.save_screenshot("error_screenshot.png")
            print("[OK] Error screenshot captured via nodriver.", flush=True)
        except Exception:
            pass
        sys.exit(1)
    finally:
        browser.stop()
        print("[DONE] Script finished", flush=True)

if __name__ == "__main__":
    asyncio.run(run())