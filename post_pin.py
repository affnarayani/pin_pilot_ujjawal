import os
import sys
import json
import time
import base64
import random
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

    # ========================================================
    # LOAD PINTEREST IDEAS & VERIFY PIPELINE CONDITION
    # ========================================================
    ideas_file = Path("pinterest_ideas.json")
    article_file = Path("article.json")

    if not ideas_file.exists():
        print("[ERROR] pinterest_ideas.json nahi mila. Exiting...", flush=True)
        sys.exit(1)

    try:
        with ideas_file.open("r", encoding="utf-8") as f:
            ideas_list = json.load(f)
    except Exception as e:
        print(f"[ERROR] pinterest_ideas.json parse nahi ho paya: {e}", flush=True)
        sys.exit(1)

    target_item = None
    target_index = -1

    # Find the current item in the pipeline state
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict):
            # Checking for target pending item condition
            if item.get("content_generated") is True and item.get("image_generated") is True and item.get("posted") is False:
                target_item = item
                target_index = index
                break

    # If no structured item satisfies the strict launch parameters criteria
    if target_item is None:
        print("[INFO] Either content generation or image generation is pending.", flush=True)
        sys.exit(0)

    print(f"[OK] Pipeline verified for: '{target_item['title']}' at index [{target_index}]", flush=True)

    # Fetch textual content data elements from local storage file
    if not article_file.exists():
        print("[ERROR] article.json file metadata missing. Pipeline corrupted.", flush=True)
        sys.exit(1)

    try:
        with article_file.open("r", encoding="utf-8") as f:
            meta_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] article.json parse failed: {e}", flush=True)
        sys.exit(1)

    pin_title = target_item["title"]
    pin_description = meta_data.get("description", "").strip()
    pin_alt_text = meta_data.get("alt_text", pin_title).strip()
    chosen_board = meta_data.get("selected_board", "").strip()

    if not pin_description:
        print("[ERROR] Description field inside article.json is empty.", flush=True)
        sys.exit(1)

    # Validate board fallback selection logic matching state schema boundary rules
    if chosen_board not in ALLOWED_BOARDS:
        print(f"[WARNING] Local metadata board target '{chosen_board}' invalid. Falling back safely.", flush=True)
        chosen_board = random.choice(ALLOWED_BOARDS)

    cookies = load_cookies(Path(PINTEREST_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & EXECUTION
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    page = None
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

        # Primary Locator (Aapka original role-based locator)
        alt_box = page.get_by_role('textbox', name='Explain what people can see')

        # Agar primary locator nahi milta, toh fallbacks check karenge
        if not alt_box.is_visible():
            print("[INFO] Role-based locator nahi mila, CSS Fallback try kar rahe hain...", flush=True)
            # Fallback 1: CSS Selector with Regex (ID starts with 'pin-draft-alttext-')
            alt_box = page.locator("textarea[id^='pin-draft-alttext-']")

        if not alt_box.is_visible():
            print("[INFO] CSS locator bhi nahi mila, XPath Fallback try kar rahe hain...", flush=True)
            # Fallback 2: XPath with starts-with() function
            alt_box = page.locator("//textarea[starts-with(@id, 'pin-draft-alttext-')]")

        # Action perform karne se pehle ensure karein ki element mil gaya hai
        alt_box.wait_for(state="visible", timeout=5000)

        alt_box.click()
        alt_box.fill(pin_alt_text)
        print("[OK] Alt Text loaded successfully.", flush=True)
        custom_random_wait(15, 30)

        # 6. Fill Destination Link
        print("[STEP] Accessing and filling Destination URL redirection field...", flush=True)
        dest_box = page.get_by_role('textbox', name='Add a destination link')
        dest_box.fill("https://mindtobetter.gumroad.com/")
        print("[OK] Target destination URL added successfully.", flush=True)
        custom_random_wait(15, 30)

        # 7. Select immediate publishing protocol
        print("[STEP] Forcing immediate launch scheduling selection parameters...", flush=True)
        page.get_by_role('radio', name='Publish immediately').click()
        print("[OK] Radio button confirmed.", flush=True)
        custom_random_wait(15, 30)

        # 7.5 Mark as AI-Modified Content Checkbox Action
        print("[STEP] Locating and checking 'Mark as AI-Modified Content' option...", flush=True)
        ai_checkbox = page.get_by_role('checkbox', name='Mark as AI-Modified Content')
        ai_checkbox.check()
        print("[OK] AI-Modified Content checkbox checked on successfully.", flush=True)
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
        custom_random_wait(30, 60)

        # ========================================================
        # UPDATE ARRAY METADATA LOCK TRACKER STATE
        # ========================================================
        print("[STEP] Committing posted=True back to state tracker...", flush=True)
        ideas_list[target_index]["posted"] = True

        with ideas_file.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print(f"✅ State Update Complete: '{pin_title}' flag marked as posted=True.", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
        if page:
            try:
                screenshot_path = "error_screenshot_post.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except:
                pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)


if __name__ == "__main__":
    run()
    custom_random_wait(15, 30)