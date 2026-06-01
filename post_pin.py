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

PINTEREST_COOKIES_FILE = "pinterest_cookies.json.encrypted"
IMAGE_PATH = Path("image/pin.png")

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Valid Pinterest Board options for comparison
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
HF_TOKEN = os.getenv("HF_TOKEN")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN missing")


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

    # Check if Pin image exists before doing anything else
    if not IMAGE_PATH.exists():
        print(f"❌ Error: Image file not found at {IMAGE_PATH}. Please make sure it exists.", flush=True)
        sys.exit(1)

    cookies = load_cookies(Path(PINTEREST_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # ========================================================
    # HUGGING FACE ONE-SHOT GENERATION FOR ALL DATA (JSON)
    # ========================================================
    print("[STEP] Initializing Hugging Face InferenceClient...", flush=True)
    client = InferenceClient(model="meta-llama/Meta-Llama-3-8B-Instruct", token=HF_TOKEN)

    hf_prompt = (
        "You are an expert Pinterest Marketer and copywriter. Generate optimized data for a Pinterest Pin "
        "promoting a new self-help eBook. To enable effective A/B testing, the generated title should NOT match "
        "the actual book name, but must target consumer pain points.\n\n"
        "eBook Background Details:\n"
        "Book Name: Escape The Mental Noise\n"
        "Author Name: Mind To Better\n"
        "Core Promise: Teaches how to reduce overthinking, calm the mind, improve mental clarity, control distraction, and regain focus.\n"
        "Transformation: From mentally exhausted + distracted to clear-minded + calm + focused.\n\n"
        "STRICT REQUIREMENTS FOR OUTPUT:\n"
        "1. title: Catchy, highly engaging title for A/B testing. MUST be strictly between 40 and 60 characters long.\n"
        "2. description: Compelling copy. MUST be strictly between 150 and 250 characters long.\n"
        "3. alt_text: A concise visual description of what someone would see on an aesthetic self-improvement cover image (for screen readers). MUST be strictly between 15 and 30 characters long.\n"
        "4. selected_board: Select the single most relevant category string from this list ONLY:\n"
        "   - Anxiety & Mental Peace\n"
        "   - Calm Mind Habits\n"
        "   - Focus & Mental Discipline\n"
        "   - Mental Clarity\n"
        "   - Overthinking Help\n"
        "   - Self-Improvement Psychology\n\n"
        "You must respond ONLY with a valid, clean JSON object. Do not include markdown ticks, no backticks, "
        "no introduction, no explanation. Just raw JSON text. Format template:\n"
        "{\n"
        '  "title": "...",\n'
        '  "description": "...",\n'
        '  "alt_text": "...",\n'
        '  "selected_board": "..."\n'
        "}"
    )

    print("[STEP] Requesting combined marketing fields from Llama-3 model...", flush=True)
    generated_data = {}
    try:
        res = client.chat.completions.create(
            messages=[{"role": "user", "content": hf_prompt}],
            max_tokens=500,
            temperature=0.7,
        )
        raw_content = res.choices[0].message.content.strip()
        
        # FIXED: Removed inline line-breaks inside string literal parameters
        if raw_content.startswith("```"):
            raw_content = re.sub(r'^```json\s*|```$', '', raw_content, flags=re.MULTILINE).strip()
            
        generated_data = json.loads(raw_content)
        print("[OK] Successfully generated data from Hugging Face model.", flush=True)
        print(f"[DATA PREVIEW] JSON content parsed completely: {generated_data}", flush=True)

    except Exception as hf_err:
        print(f"❌ Error: Hugging Face extraction or parsing failed: {hf_err}. Exiting.", flush=True)
        sys.exit(1)

    # Validate individual parameters and assign fallbacks if values mismatch criteria
    pin_title = generated_data.get("title", "Stop Overthinking & Reclaim Peace Right Now").strip()
    pin_description = generated_data.get("description", "Feeling mentally exhausted and constantly distracted by social media loops? Discover practical mindset shifts and daily habits to quiet your mind, reduce anxiety, and master razor-sharp mental focus starting today.").strip()
    pin_alt_text = generated_data.get("alt_text", "A minimalist aesthetic setting showcasing a calm person enjoying clarity under soft ambient lighting.").strip()
    chosen_board = generated_data.get("selected_board", "").strip()

    # Enforce strict length limits manually if LLM underperformed
    if len(pin_title) > 60:
        pin_title = pin_title[:57] + "..."
    elif len(pin_title) < 40:
        pin_title = pin_title + " - Calm Mental Clutter Strategy"

    if len(pin_description) > 250:
        pin_description = pin_description[:247] + "..."
    elif len(pin_description) < 150:
        pin_description = pin_description + " Explore effective actionable psychology mental routines to naturally reduce cognitive fatigue and structural overthinking loop habits today."

    # Validate board fallback selection logic
    if chosen_board not in ALLOWED_BOARDS:
        print(f"[WARNING] HF generated an invalid board target '{chosen_board}'. Selecting a random fallback...", flush=True)
        chosen_board = random.choice(ALLOWED_BOARDS)

    print(f"[FINAL PLAN] Board: '{chosen_board}' | Title: '{pin_title}' ({len(pin_title)} chars)", flush=True)

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

        print("[STEP] Opening Pinterest Pin Builder URL...", flush=True)
        page.goto(
            "https://www.pinterest.com/pin-builder/",
            wait_until="domcontentloaded"
        )
        print("[OK] Pinterest Pin Builder URL opened completely", flush=True)
        custom_random_wait(30, 60)

        # 1. Fill Generated Title
        print("[STEP] Locating and filling Title text field...", flush=True)
        title_box = page.get_by_role('textbox', name='Add your title')
        title_box.fill(pin_title)
        print("[OK] Title input completed.", flush=True)
        custom_random_wait(15, 30)

        # 2. Upload Pin Media natively without dialog interference
        print("[STEP] Uploading pin image via file system injection path...", flush=True)
        # Using a regex matcher to locate the dynamic data-test-id input selector element
        upload_input = page.locator('input[data-test-id^="media-upload-input-"]')
        upload_input.set_input_files(str(IMAGE_PATH))
        print("[OK] Media payload transferred successfully.", flush=True)
        custom_random_wait(30, 60)

        # 3. Fill Generated Description
        print("[STEP] Locating and filling Pin Description text field...", flush=True)
        desc_box = page.get_by_role('combobox', name='Tell everyone what your Pin').locator('div').nth(2)
        desc_box.fill(pin_description)
        print("[OK] Description payload added successfully.", flush=True)
        custom_random_wait(15, 30)

        # 4. Open Alt Text section
        print("[STEP] Clicking 'Add alt text' reference button...", flush=True)
        page.get_by_role('button', name='Add alt text').click()
        print("[OK] Alt Text interface drawer exposed.", flush=True)
        custom_random_wait(15, 30)

        # 5. Fill Screen Reader Alt Text
        print("[STEP] Typing generated Alt Text content...", flush=True)
        alt_box = page.get_by_role('textbox', name='Explain what people can see')
        alt_box.click()
        alt_box.fill(pin_alt_text)
        print("[OK] Alt Text loaded successfully.", flush=True)
        custom_random_wait(15, 30)

        # 6. Fill Destination Link
        print("[STEP] Accessing and filling Destination URL redirection field...", flush=True)
        dest_box = page.get_by_role('textbox', name='Add a destination link')
        dest_box.fill("https://mindtobetter.github.io/")
        print("[OK] Target destination URL added successfully.", flush=True)
        custom_random_wait(15, 30)

        # 7. Select immediate publishing protocol
        print("[STEP] Forcing immediate launch scheduling selection parameters...", flush=True)
        page.get_by_role('radio', name='Publish immediately').click()
        print("[OK] Radio button confirmed.", flush=True)
        custom_random_wait(15, 30)

        # 8. Open Board Selection Dropdown
        print("[STEP] Triggering Pinterest Board selector drop list click...", flush=True)
        page.locator('[data-test-id="board-dropdown-select-button"]').click()
        print("[OK] Dropdown component overlay active.", flush=True)
        custom_random_wait(15, 30)

        # Map correct execution string names based on selected context target configuration
        if chosen_board == "Anxiety & Mental Peace":
            board_button = page.get_by_role('button', name='Anxiety & Mental Peace Publish')
        elif chosen_board == "Calm Mind Habits":
            board_button = page.get_by_role('button', name='Calm Mind Habits Publish')
        elif chosen_board == "Focus & Mental Discipline":
            board_button = page.get_by_role('button', name='Focus & Mental Discipline')
        elif chosen_board == "Mental Clarity":
            board_button = page.get_by_role('button', name='Mental Clarity Publish')
        elif chosen_board == "Overthinking Help":
            board_button = page.get_by_role('button', name='Overthinking Help Publish')
        elif chosen_board == "Self-Improvement Psychology":
            board_button = page.get_by_role('button', name='Self-Improvement Psychology')
        else:
            board_button = page.get_by_role('button', name='Anxiety & Mental Peace Publish')

        print(f"[STEP] Selecting chosen board element matching action: '{chosen_board}'", flush=True)
        board_button.click()
        print("[OK] Specific Board category assignment selection registered.", flush=True)
        custom_random_wait(15, 30)

        # 9. Commit Save Parameters Action
        print("[STEP] Instantiating final board data save button deployment...", flush=True)
        page.locator('[data-test-id="board-dropdown-save-button"]').click()
        print("[OK] Post submission workflow finalized successfully.", flush=True)
        
        print("[STEP] Holding browser session buffer before structural closing loop routine...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
        sys.exit(1)

    finally:
        try:
            browser.close()
        except:
            pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)

def clear_image_folder():
    # Folder ka path set karein
    folder_path = Path("image")
    
    # Check karein ki folder exist karta hai ya nahi
    if not folder_path.exists():
        print(f"[INFO] '{folder_path}' nam ka koi folder nahi mila.", flush=True)
        return

    print(f"[START] '{folder_path}' folder ko khali kiya ja raha hai...", flush=True)
    
    # Folder ke andar ke saare contents par loop chalayein
    for item in folder_path.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()  # File ya link ko delete karne ke liye
                print(f"[DEL] File delete ho gayi: {item.name}", flush=True)
            elif item.is_dir():
                shutil.rmtree(item)  # Pura sub-folder delete karne ke liye
                print(f"[DEL] Sub-folder delete ho gaya: {item.name}", flush=True)
        except Exception as e:
            print(f"❌ Error aaya {item.name} ko delete karte waqt: {e}", flush=True)

    print("[SUCCESS] 'image' folder ke andar ke saare contents saaf ho gaye hain!", flush=True)

if __name__ == "__main__":
    run()
    clear_image_folder()
    custom_random_wait(15, 30)