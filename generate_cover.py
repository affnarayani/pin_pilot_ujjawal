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

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

IMAGE_DIR = Path("image")
IMAGE_DIR.mkdir(exist_ok=True)

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

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))

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

        # ========================================================
        # HUGGING FACE OPTIMIZED PROMPT GENERATION
        # ========================================================
        print("[STEP] Initializing Hugging Face InferenceClient...", flush=True)
        client = InferenceClient(model="meta-llama/Meta-Llama-3-8B-Instruct", token=HF_TOKEN)

        hf_prompt = (
            "You are an expert AI image prompt engineer. Based on the following eBook details, "
            "write a highly descriptive, professional, and visually compelling image generation prompt "
            "for its cover page. The prompt should perfectly capture the essence, mood, and transformation theme. "
            "Respond ONLY with the final optimized image prompt text. Do not include any introduction, "
            "explanation, or markdown quotes.\n\n"
            "eBook Details:\n"
            "Book Name: Escape The Mental Noise\n"
            "Author Name: Mind To Better\n"
            "Core Promise:\n"
            "Teaches the reader to:\n"
            "- Reduce overthinking\n"
            "- Calm the mind\n"
            "- Improve mental clarity\n"
            "- Control distraction\n"
            "- Regain focus\n"
            "What the eBook Explains:\n"
            "- Why the brain is always noisy\n"
            "- The psychology of overthinking\n"
            "- The impact of phones and social media\n"
            "- How mental clutter builds up\n"
            "- How to develop calm thinking\n"
            "- Practical exercises for clarity\n"
            "Transformation Path:\n"
            "- From: Mentally exhausted and distracted\n"
            "- To: Clear-minded, calm, and focused."
        )

        print("[STEP] Requesting optimized prompt from Llama-3 model using chat completions...", flush=True)
        hf_generated_prompt = ""
        
        try:
            res = client.chat.completions.create(
                messages=[{"role": "user", "content": hf_prompt}],
                max_tokens=300,
                temperature=0.7,
            )
            
            raw_content = res.choices[0].message.content
            if raw_content:
                hf_generated_prompt = raw_content.replace("**", "").replace("*", "").strip()
                # Remove leading/trailing quotes if any
                hf_generated_prompt = re.sub(r'^["\']|["\']$', '', hf_generated_prompt).strip()
                
        except Exception as hf_err:
            print(f"[ERROR] Hugging Face prompt generation failed: {hf_err}", flush=True)

        # STRICT REQUIREMENT: If empty or generation failed, exit with error code 1
        if not hf_generated_prompt:
            print("❌ Error: HF prompt generation failed or returned empty content. Exiting program.", flush=True)
            sys.exit(1)

        print("[OK] Successfully received prompt from Hugging Face", flush=True)
        # ========================================================

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
        
        prompt_text = f"Generate a 4k image with a size strictly of 1024x1536 px, depicting the following scene: {hf_generated_prompt}"
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
                
                # File save in image folder with static name pin.png
                local_filename = IMAGE_DIR / "pin.png"
                
                download.save_as(local_filename)
                print(f"✅ Original resolution high quality image downloaded successfully (Saved to image directory): {local_filename}", flush=True)
                
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
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()