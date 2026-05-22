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
from huggingface_hub import InferenceClient

from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

PINTEREST_COOKIES_FILE = "cookies.json.encrypted"
POSTED_CONTENT_FILE = "posted_content.json"

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

PBKDF2_ITERATIONS = 200_000
MAX_RETRIES = 5  

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN missing")


# =========================
# RANDOM WAIT
# =========================
def random_wait():
    seconds = random.uniform(6, 12)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


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
    print("[START] Script started", flush=True)

    cookies = load_cookies(Path(PINTEREST_COOKIES_FILE))

    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP
    # =========================
    stealth = Stealth()

    pw_cm = stealth.use_sync(sync_playwright())

    pw = pw_cm.__enter__()

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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)

        page.goto(
            "https://chatgpt.com/",
            wait_until="domcontentloaded"
        )

        print("[OK] URL opened", flush=True)

        # 1. Initial random wait (30-60 seconds)
        print("[STEP] Performing initial random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

        # 2. Locate chat box and type prompt
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        prompt_text = "create a 4k image of an ebook cover page with text 30 money making ideas and a relevant image in the background. image size must be 1024x1536"
        print(f"[STEP] Filling prompt: '{prompt_text}'", flush=True)
        chat_box.fill(prompt_text)
        
        page.keyboard.press("Enter")
        print("[OK] Prompt sent successfully", flush=True)

        # 3. 'Share this image' retry loop (Max 5 times)
        share_button = None
        found_share = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(30, 60)
            
            try:
                locator = page.get_by_role('button', name='Share this image').first
                if locator.is_visible():
                    share_button = locator
                    found_share = True
                    print("✅ 'Share this image' button located successfully!", flush=True)
                    break
            except Exception as loc_err:
                print(f"[INFO] Share locator exception: {loc_err}", flush=True)
            
            print(f"[WARNING] Share button not visible on attempt {attempt}. Retrying...", flush=True)

        if not found_share or not share_button:
            print("❌ Error: 'Share this image' button not found after 5 retries. Exiting program.", flush=True)
            sys.exit(1)

        # Clear clipboard
        page.evaluate("() => navigator.clipboard.writeText('')")

        # Click share button
        print("[STEP] Clicking 'Share this image' button...", flush=True)
        share_button.click()
        
        # Pop-up load hone ke liye chhota sa wait
        custom_random_wait(15, 30)

        # HACK: Agar 'Copy link' button wala pop-up aata hai toh uspar click karega
        try:
            copy_link_btn = page.get_by_role('button', name='Copy link').first
            if copy_link_btn.is_visible():
                print("[INFO] 'Copy link' pop-up detected. Clicking it explicitly...", flush=True)
                copy_link_btn.click()
                time.sleep(2)  # URL properly copy hone ke liye thoda wait
        except Exception as pop_err:
            print("[INFO] No pop-up button found, continuing with direct copy...", flush=True)

        # 4. Extract and print copied shared URL
        public_shared_url = page.evaluate("() => navigator.clipboard.readText()")
        print(f"\n[COPIED URL] Shared Link Extracted: {public_shared_url}\n", flush=True)

        if public_shared_url and "chatgpt.com/s/" in public_shared_url:
            # Open new page tab for shared link
            print("[STEP] Opening new tab for public shared link...", flush=True)
            shared_page = context.new_page()
            shared_page.goto(public_shared_url, wait_until="domcontentloaded")
            
            # REQUIREMENT: New tab par jaane ke baad 30, 60 seconds ka random wait
            print("[STEP] Performing mandatory random wait on new tab (30-60 seconds)...", flush=True)
            custom_random_wait(30, 60)
            
            print("[STEP] Locating 'Save' button to trigger high-res download...", flush=True)
            
            # Intercept download event loop
            try:
                save_btn = shared_page.get_by_role('button', name='Save').first
                
                # Setup download watcher to capture file stream natively
                with shared_page.expect_download(timeout=60000) as download_info:
                    print("[STEP] Clicking 'Save' button...", flush=True)
                    save_btn.click()
                
                download = download_info.value
                
                # File save in program root directory
                root_dir = Path(".")
                local_filename = root_dir / f"highres_cat_{int(time.time())}.png"
                
                download.save_as(local_filename)
                print(f"✅ Original resolution high quality image downloaded successfully (Saved to root): {local_filename}", flush=True)
                
            except Exception as download_err:
                print(f"❌ Error during 'Save' button download processing: {download_err}", flush=True)
                
            # Close shared page tab
            shared_page.close()
        else:
            print("[ERROR] Extracted clipboard content is not a valid ChatGPT shared page link URL.", flush=True)

        # 6. Final random wait (30-60 seconds) before browser close
        print("[STEP] Performing final random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)

    finally:
        try:
            browser.close()
        except:
            pass

        try:
            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir(exist_ok=True)
            print("[CLEANUP] Temp cleared", flush=True)
        except Exception as e:
            print("[CLEANUP ERROR]", e, flush=True)

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()