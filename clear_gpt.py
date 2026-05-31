import os
import sys
import json
import time
import base64
import random
import shutil
import requests
import re
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

print(f"[OK] Found {len(encrypted_files)} encrypted cookie file(s) to process.", flush=True)

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


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
    print(f"[STEP] Loading cookies from: {file_path.name}...", flush=True)

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
# PROCESS SINGLE COOKIE
# =========================
def process_cookie_file(cookie_file_path: Path):
    print(f"\n==================================================", flush=True)
    print(f"[STARTING] Processing file: {cookie_file_path.name}", flush=True)
    print(f"==================================================", flush=True)

    cookies = load_cookies(cookie_file_path)
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        # Target Settings URL
        settings_url = "https://chatgpt.com/#settings/DataControls"

        # 1. Navigate to Data Controls Settings
        print("[STEP] Navigating to ChatGPT Data Controls Settings...", flush=True)
        page.goto(settings_url, wait_until="domcontentloaded")
        print("[OK] Settings URL loaded", flush=True)

        # 2. Wait random 15, 30 seconds
        print("[STEP] Performing random wait after navigation...", flush=True)
        custom_random_wait(15, 30)

        # 3. Locate 'Delete all chats' button
        print("[STEP] Checking for 'Delete all' chats button...", flush=True)
        delete_all_btn = page.get_by_role('button', name='Delete all Delete all chats')
        
        # IMPROVEMENT LOGIC: Agar delete button directly visible nahi hai toh alternative "Open" check karein
        if not delete_all_btn.is_visible():
            print("[INFO] 'Delete all' button not visible. Checking for Workspace 'Open' button fallbacks...", flush=True)
            
            # Sub-element matching via TestID hierarchy
            open_btn = page.get_by_test_id('existing-workspace-row').get_by_role('button', name='Open')
            
            # Fallback for plain button with name/aria "Open"
            if not open_btn.is_visible():
                print("[INFO] Test ID 'Open' button not visible, trying fallback via role/name...", flush=True)
                open_btn = page.get_by_role('button', name='Open')
            
            # Agar dono me se koi bhi "Open" button detect hota hai
            if open_btn.is_visible():
                print("[STEP] 'Open' button detected! Clicking it now...", flush=True)
                open_btn.click()
                
                # Wait for random 15, 30 seconds
                print("[STEP] Waiting after clicking 'Open' button...", flush=True)
                custom_random_wait(15, 30)
                
                # Re-navigate back to data controls settings page
                print("[STEP] Re-navigating to ChatGPT Data Controls Settings...", flush=True)
                page.goto(settings_url, wait_until="domcontentloaded")
                custom_random_wait(10, 15)  # Let it stabilize
                
            else:
                print("[WARNING] Neither 'Delete all' nor 'Open' buttons were immediately visible.", flush=True)

        # Explicit safety wait execution for deletion popup
        delete_all_btn.wait_for(state="visible", timeout=15000)
        print("[STEP] Clicking 'Delete all' button...", flush=True)
        delete_all_btn.click()

        # Brief pause for animation layer rendering
        time.sleep(2)

        # 4. Locate and click Confirmation button
        print("[STEP] Locating confirmation button...", flush=True)
        confirm_btn = page.get_by_test_id('confirm-delete-all-chats-button')
        
        if not confirm_btn.is_visible():
            print("[INFO] Test ID button not visible, trying fallback via role/name...", flush=True)
            confirm_btn = page.get_by_role('button', name='Confirm deletion')

        confirm_btn.wait_for(state="visible", timeout=10000)
        print("[STEP] Clicking Confirmation button...", flush=True)
        confirm_btn.click()
        print("[OK] Deletion command executed successfully!", flush=True)

        # 5. Wait for random 15, 30 seconds before exit
        print("[STEP] Performing post-deletion random wait...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print(f"[ERROR] Exception occurred while processing {cookie_file_path.name}: {e}", flush=True)

    finally:
        print(f"[STEP] Closing browser for file: {cookie_file_path.name}...", flush=True)
        try:
            if browser:
                browser.close()
        except:
            pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print(f"[DONE] Finished processing file: {cookie_file_path.name}", flush=True)


# =========================
# MAIN LOOP
# =========================
def run():
    print("[START] Sequential Cookie Automation Pipeline Started", flush=True)
    
    # Processing each encrypted cookie file sequentially
    for index, cookie_file in enumerate(encrypted_files, start=1):
        print(f"\n[PROGRESS] Processing Cookie {index} of {len(encrypted_files)}", flush=True)
        process_cookie_file(cookie_file)
        
        # Small cooldown break before context hand-off
        if index < len(encrypted_files):
            print("[INFO] Waiting 5 seconds before switching to the next cookie file...", flush=True)
            time.sleep(5)

    print("\n[ALL DONE] All cookie files have been processed sequentially!", flush=True)


if __name__ == "__main__":
    run()