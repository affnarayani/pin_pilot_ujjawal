import os
import sys
import json
import random
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config_loader import get, get_path
from common.crypto import load_cookies, get_env_decrypt_key
from common.locator_loader import resolve_locator, apply_auto_correct
from common.screenshot import handle_error_screenshot

# Environment
load_dotenv()
get_env_decrypt_key()

# Config
HEADLESS = get("headless", True)
USER_AGENT = get(
    "user_agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)
MIN_RANDOM_WAIT_SHORT = get("min_random_wait_short", 3)
MAX_RANDOM_WAIT_SHORT = get("max_random_wait_short", 5)
MIN_RANDOM_WAIT_LONG = get("min_random_wait_long", 30)
MAX_RANDOM_WAIT_LONG = get("max_random_wait_long", 60)

PINTEREST_COOKIES_DIR = get_path("pinterest_cookies")
IDEAS_FILE = get_path("json", "pinterest_ideas.json")
ARTICLE_FILE = get_path("json", "article.json")
IMAGE_PATH = get_path("pinterest_image", "pin.png")
PLATFORM = "pinterest"

ALLOWED_BOARDS = [
    "Anxiety & Mental Peace",
    "Calm Mind Habits",
    "Focus & Mental Discipline",
    "Mental Clarity",
    "Overthinking Help",
    "Self-Improvement Psychology",
]

BOARD_LOCATOR_MAP = {
    "Anxiety & Mental Peace": "pinterest_board_anxiety_mental_peace_button",
    "Calm Mind Habits": "pinterest_board_calm_mind_habits_button",
    "Focus & Mental Discipline": "pinterest_board_focus_mental_discipline_button",
    "Mental Clarity": "pinterest_board_mental_clarity_button",
    "Overthinking Help": "pinterest_board_overthinking_help_button",
    "Self-Improvement Psychology": "pinterest_board_self_improvement_psychology_button",
}


def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    import time
    time.sleep(seconds)


def pick_pinterest_cookie_file():
    PINTEREST_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    candidates = list(PINTEREST_COOKIES_DIR.glob("*.encrypted"))
    root_fallback = get_path("pinterest_cookies.json.encrypted")
    if not candidates and root_fallback.exists():
        candidates = [root_fallback]
    if not candidates:
        raise RuntimeError(f"No Pinterest cookie .encrypted file found in '{PINTEREST_COOKIES_DIR}'")
    return random.choice(candidates)


def run():
    print("[START] Script started", flush=True)
    apply_auto_correct()

    if not IMAGE_PATH.exists():
        print(f"[ERROR] Image file not found at {IMAGE_PATH}", flush=True)
        sys.exit(1)

    if not IDEAS_FILE.exists():
        print(f"[ERROR] Ideas file not found: {IDEAS_FILE}", flush=True)
        sys.exit(1)

    try:
        with IDEAS_FILE.open("r", encoding="utf-8") as f:
            ideas_list = json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not parse ideas file: {e}", flush=True)
        sys.exit(1)

    target_item = None
    target_index = -1
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict) and item.get("content_generated") is True and item.get("image_generated") is True and item.get("posted") is False:
            target_item = item
            target_index = index
            break

    if target_item is None or target_index == -1:
        print("[INFO] Conditions not met (content_generated=True, image_generated=True, posted=False). Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Pipeline verified for '{target_item.get('title')}' at index [{target_index}]", flush=True)

    if not ARTICLE_FILE.exists():
        print(f"[ERROR] Article metadata file missing: {ARTICLE_FILE}", flush=True)
        sys.exit(1)

    try:
        with ARTICLE_FILE.open("r", encoding="utf-8") as f:
            meta_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not parse article metadata: {e}", flush=True)
        sys.exit(1)

    extracted_blog_url = str(target_item.get("url", "")).strip()
    if not extracted_blog_url:
        print("[WARNING] Target item has no URL. Falling back to default store URL.", flush=True)
        extracted_blog_url = "https://mindtobetter.gumroad.com/"

    meta_data["url"] = extracted_blog_url
    with ARTICLE_FILE.open("w", encoding="utf-8") as f:
        json.dump(meta_data, f, indent=4, ensure_ascii=False)
    print(f"[OK] Updated article metadata with URL: {extracted_blog_url}", flush=True)

    pin_title = target_item.get("title") or meta_data.get("title", "Untitled Pin")
    pin_description = str(meta_data.get("description", "")).strip()
    pin_alt_text = str(meta_data.get("alt_text", pin_title)).strip()
    chosen_board = str(meta_data.get("selected_board", "")).strip()
    destination_link = str(meta_data.get("url", "https://mindtobetter.gumroad.com/")).strip()

    if not pin_description:
        print("[ERROR] Description field in article metadata is empty.", flush=True)
        sys.exit(1)

    if chosen_board not in ALLOWED_BOARDS:
        print(f"[WARNING] Invalid board '{chosen_board}'. Using random allowed board.", flush=True)
        chosen_board = random.choice(ALLOWED_BOARDS)

    cookie_file = pick_pinterest_cookie_file()
    print(f"[OK] Selected Pinterest cookie file: {cookie_file}", flush=True)
    cookies = load_cookies(cookie_file)
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()
    browser = None
    page = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(no_viewport=True, user_agent=USER_AGENT)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        context.add_cookies(cookies)
        page = context.new_page()
        print("[OK] Browser context ready", flush=True)

        print("[STEP] Opening Pinterest Pin Builder...", flush=True)
        page.goto("https://www.pinterest.com/pin-builder/", wait_until="domcontentloaded")
        print("[OK] Pinterest Pin Builder opened", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        title_box = resolve_locator(page, PLATFORM, "pinterest_title_textbox", "Locate Pinterest pin title textbox")
        title_box.fill(pin_title)
        print("[OK] Title filled", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        upload_input = resolve_locator(page, PLATFORM, "pinterest_media_upload_input", "Locate Pinterest media upload input")
        upload_input.set_input_files(str(IMAGE_PATH))
        print("[OK] Media uploaded", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        desc_box = resolve_locator(page, PLATFORM, "pinterest_description_combobox", "Locate Pinterest pin description editor")
        desc_box.fill(pin_description)
        print("[OK] Description filled", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        try:
            add_alt_btn = resolve_locator(page, PLATFORM, "pinterest_add_alt_text_button", "Locate Add alt text button")
            add_alt_btn.click()
            custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
            alt_box = resolve_locator(page, PLATFORM, "pinterest_alt_text_textbox", "Locate alt text textbox")
            alt_box.fill(pin_alt_text)
            print("[OK] Alt text filled", flush=True)
            custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        except Exception as alt_err:
            print(f"[WARNING] Alt text step skipped: {alt_err}", flush=True)

        dest_box = resolve_locator(page, PLATFORM, "pinterest_destination_link_textbox", "Locate destination link textbox")
        dest_box.click()
        dest_box.type(destination_link, delay=50)
        print(f"[OK] Destination link entered: {destination_link}", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        publish_radio = resolve_locator(page, PLATFORM, "pinterest_publish_immediately_radio", "Locate Publish immediately radio")
        publish_radio.click()
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        ai_checkbox = resolve_locator(page, PLATFORM, "pinterest_mark_ai_modified_checkbox", "Locate Mark as AI-Modified Content checkbox")
        try:
            ai_checkbox.check()
        except Exception:
            ai_checkbox.click()
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        board_dropdown = resolve_locator(page, PLATFORM, "pinterest_board_dropdown_button", "Locate board dropdown button")
        board_dropdown.click()
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        board_locator_name = BOARD_LOCATOR_MAP.get(chosen_board, "pinterest_board_anxiety_mental_peace_button")
        board_button = resolve_locator(page, PLATFORM, board_locator_name, f"Locate Pinterest board button for '{chosen_board}'")
        board_button.click()
        print(f"[OK] Board selected: {chosen_board}", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        board_save = resolve_locator(page, PLATFORM, "pinterest_board_save_button", "Locate board dropdown save/publish button")
        board_save.click()
        print("[OK] Pin submission triggered", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        ideas_list[target_index].update({"posted": True})
        with IDEAS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print(f"[OK] Updated posted=True for '{pin_title}'", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation failed:", e, flush=True)
        try:
            if page:
                handle_error_screenshot(page, upload=True)
        except Exception as screenshot_err:
            print(f"[WARNING] Could not capture/upload error screenshot: {screenshot_err}", flush=True)
        sys.exit(1)
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass
        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()
