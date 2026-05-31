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
    print("❌ No .encrypted cookie files found in 'cookies/' folder", flush=True)
    sys.exit(1)

print(f"[OK] Found {len(encrypted_files)} cookie file(s) to process.", flush=True)

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    print("❌ DECRYPT_KEY missing in environment variables.", flush=True)
    sys.exit(1)


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
        print("❌ Decryption failed (InvalidTag)", flush=True)
        sys.exit(1)


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print(f"[STEP] Loading cookies from {file_path.name}...", flush=True)

    try:
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
    except Exception as crypto_err:
        print(f"❌ [CRITICAL CRYPTO ERROR]: File {file_path.name} parse/decrypt failed: {crypto_err}", flush=True)
        sys.exit(1)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # LOOP: Iterating through each encrypted cookie file in the directory
    for index, cookie_file in enumerate(encrypted_files, start=1):
        print("\n" + "="*50, flush=True)
        print(f"[PROCESS] Processing file {index}/{len(encrypted_files)}: {cookie_file.name}", flush=True)
        print("="*50, flush=True)

        browser = None
        pw_cm = None

        try:
            cookies = load_cookies(cookie_file)
            print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

            # =========================
            # STEALTH SETUP
            # =========================
            stealth = Stealth()
            pw_cm = stealth.use_sync(sync_playwright())
            pw = pw_cm.__enter__()

            # GITHUB RUNNER COMPATIBILITY FIX 1: Window size arguments pass kiye
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--start-maximized",
                    "--window-size=1920,1080",
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            # GITHUB RUNNER COMPATIBILITY FIX 2: no_viewport hata kar fixed large desktop size set kiya
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT
            )

            context.grant_permissions(["clipboard-read", "clipboard-write"])

            print("[STEP] Adding cookies to browser context...", flush=True)
            context.add_cookies(cookies)

            page = context.new_page()
            print("[OK] Cookies added successfully", flush=True)

            print("[STEP] Opening ChatGPT Main URL (Logging in via cookies)...", flush=True)
            page.goto(
                "https://chatgpt.com/",
                wait_until="domcontentloaded"
            )
            print("[OK] URL opened and Login completed via session cookies", flush=True)

            # ==================================================
            # AUTOMATION STEPS WITH FIXED 15-30 SEC DELAYS
            # ==================================================
            
            # Step 1: Wait then locate profile menu button
            print("[STEP] Waiting 15-30s before interacting with profile menu...", flush=True)
            custom_random_wait(15, 30)

            sidebar_btn = page.get_by_role("button", name="Open sidebar")
            
            # Check karega ki kya layout me "Open sidebar" button generated hai
            if sidebar_btn.is_visible():
                # Agar visible hai aur closed state (aria-expanded="false") me hai toh collapse expand karenge
                if sidebar_btn.get_attribute("aria-expanded") == "false":
                    print("[STEP] Sidebar is closed. Opening side bar...", flush=True)
                    sidebar_btn.click(force=True)
                    print("[STEP] Side bar opened successfully.", flush=True)
                    custom_random_wait(15, 30)
                else:
                    print("[STEP] Side bar button is visible but already expanded. Proceeding...", flush=True)
            else:
                print("[STEP] Side bar toggle button not present in current view layout. Proceeding...", flush=True)
            
            print("[STEP] Locating profile menu button...", flush=True)
            profile_button = page.get_by_test_id("accounts-profile-button").last
            profile_button.wait_for(state="visible", timeout=30000)
            profile_button.click()
            print("[OK] Profile menu clicked", flush=True)
            
            # Step 2: Wait then click Settings option
            print("[STEP] Waiting 15-30s before clicking Settings...", flush=True)
            custom_random_wait(15, 30)

            print("[STEP] Clicking Settings option...", flush=True)
            settings_item = page.get_by_test_id("settings-menu-item")
            if not settings_item.is_visible():
                settings_item = page.get_by_role("menuitem", name="Settings")
            
            settings_item.wait_for(state="visible", timeout=30000)
            settings_item.click()
            print("[OK] Settings opened", flush=True)

            # Step 3: Wait then click Data Controls tab
            print("[STEP] Waiting 15-30s before switching to Data controls tab...", flush=True)
            custom_random_wait(15, 30)

            print("[STEP] Navigating to Data controls tab...", flush=True)
            data_controls_tab = page.get_by_test_id("data-controls-tab")
            if not data_controls_tab.is_visible():
                data_controls_tab = page.get_by_role("tab", name="Data controls")
                
            data_controls_tab.wait_for(state="visible", timeout=30000)
            data_controls_tab.click()
            print("[OK] Data controls tab loaded", flush=True)

            # Step 4: Wait then trigger Delete All Chats confirmation
            print("[STEP] Waiting 15-30s before clicking 'Delete all' chats...", flush=True)
            custom_random_wait(15, 30)

            print("[STEP] Clicking 'Delete all' chats button...", flush=True)
            delete_all_btn = page.get_by_role("button", name="Delete all Delete all chats")
            delete_all_btn.wait_for(state="visible", timeout=30000)
            delete_all_btn.click()
            print("[OK] Delete all confirmation prompt triggered", flush=True)

            # Step 5: Wait then click final Deletion Confirmation button
            print("[STEP] Waiting 15-30s before clicking 'Confirm deletion'...", flush=True)
            custom_random_wait(15, 30)

            print("[STEP] Clicking 'Confirm deletion' button...", flush=True)
            confirm_btn = page.get_by_test_id("confirm-delete-all-chats-button")
            if not confirm_btn.is_visible():
                confirm_btn = page.get_by_role("button", name="Confirm deletion")
                
            confirm_btn.wait_for(state="visible", timeout=30000)
            confirm_btn.click()
            print("[OK] Deletion confirmed successfully", flush=True)

            # Step 6: Post-action finalized wait interval before session destruction
            print("[STEP] Action complete for this profile. Finalizing safety wait...", flush=True)
            custom_random_wait(15, 30)

        except SystemExit:
            raise
        except Exception as e:
            print(f"\n❌ [CRITICAL ERROR] Operation failed for {cookie_file.name}: {e}", flush=True)
            print("[STEP] Executing emergency browser teardown and exiting program via sys.exit(1)...", flush=True)
            
            if browser:
                try:
                    browser.close()
                except:
                    pass
            if pw_cm:
                try:
                    pw_cm.__exit__(None, None, None)
                except:
                    pass
                    
            sys.exit(1)

        finally:
            if browser or pw_cm:
                print(f"[STEP] Exiting browser and cleaning context for {cookie_file.name}...", flush=True)
                if browser:
                    try:
                        browser.close()
                    except:
                        pass
                if pw_cm:
                    try:
                        pw_cm.__exit__(None, None, None)
                    except:
                        pass

    print("\n[DONE] All cookie files processed successfully.", flush=True)


if __name__ == "__main__":
    run()