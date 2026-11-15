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
CHATGPT_URL = get("chatgpt_url", "https://chatgpt.com/")
MAX_RETRIES = get("max_retries", 10)

COOKIES_DIR = get_path("chatgpt_cookies")
IMAGE_DIR = get_path("pinterest_image")
IMAGE_DIR.mkdir(exist_ok=True)
IDEAS_FILE = get_path("json", "pinterest_ideas.json")
ARTICLE_FILE = get_path("json", "article.json")
STATUS_FILE = get_path("json", "status.json")
PLATFORM = "chatgpt"


def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    import time
    time.sleep(seconds)


def read_pin_type(default="click"):
    if not STATUS_FILE.exists():
        print(f"[WARNING] status.json not found. Defaulting pin_type to '{default}'.", flush=True)
        return default
    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            status = json.load(f)
        pin_type = status.get("current_pin_type")
        if pin_type not in ("save", "click"):
            print(f"[WARNING] status.json has invalid/missing current_pin_type ('{pin_type}'). Defaulting to '{default}'.", flush=True)
            return default
        return pin_type
    except Exception as e:
        print(f"[WARNING] Could not read status.json ({e}). Defaulting pin_type to '{default}'.", flush=True)
        return default


def run():
    print("[START] Script started", flush=True)
    apply_auto_correct()

    if not IDEAS_FILE.exists():
        raise RuntimeError(f"Missing ideas file: {IDEAS_FILE}")

    with IDEAS_FILE.open("r", encoding="utf-8") as f:
        ideas_list = json.load(f)

    subject_matter = None
    target_index = -1
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict) and item.get("content_generated") is True and item.get("image_generated") is False:
            subject_matter = item.get("title") or item.get("subject") or list(item.values())[0]
            target_index = index
            break

    if subject_matter is None or target_index == -1:
        print("[INFO] Target conditions not met. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Selected target subject: '{subject_matter}' at index [{target_index}]", flush=True)

    generated_title = None
    image_teaser_points = []
    hidden_hook = None
    article_pin_type = None
    if ARTICLE_FILE.exists():
        try:
            with ARTICLE_FILE.open("r", encoding="utf-8") as f:
                article_data = json.load(f)
            generated_title = article_data.get("title")
            image_teaser_points = article_data.get("image_teaser_points") or []
            hidden_hook = article_data.get("hidden_hook")
            article_pin_type = article_data.get("pin_type")
            print(f"[OK] Loaded article contract: teaser_points={image_teaser_points}, hidden_hook='{hidden_hook}', pin_type='{article_pin_type}'", flush=True)
        except Exception as article_err:
            print(f"[WARNING] Could not parse article.json: {article_err}", flush=True)
    else:
        print("[WARNING] article.json not found. Continuing without article contract.", flush=True)

    pin_type = read_pin_type()
    print(f"[OK] Pin type from status: '{pin_type}'", flush=True)
    if article_pin_type and article_pin_type != pin_type:
        print(f"[WARNING] pin_type mismatch (status='{pin_type}', article='{article_pin_type}'). Using article value.", flush=True)
        pin_type = article_pin_type

    if generated_title:
        subject_matter = generated_title

    encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError(f"No .encrypted cookie files found in '{COOKIES_DIR}'")
    cookie_file = random.choice(encrypted_files)
    print(f"[OK] Selected cookie file: {cookie_file.name}", flush=True)
    cookies = load_cookies(cookie_file)
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()
    browser = None
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

        layout_options = [
            "Vertical step-by-step timeline (1, 2, 3) connected by a subtle dotted line",
            "Side-by-side comparison matrix (This vs That split layout)",
            "4-quadrant clean grid structure dividing the concepts into boxes",
            "Central core concept with 4 radiating minimalist branches or arrows",
        ]
        color_options = [
            "Deep dark obsidian background with crisp white and soft sage green accents.",
            "Warm minimalist cream background with rich terracotta and charcoal grey accents.",
            "Clean stark white background with sophisticated navy blue and soft gold accent highlights.",
            "Soft muted beige background with deep chocolate brown text and burnt orange accents.",
        ]
        font_options = [
            "A bold, elegant Serif headline paired with a clean, high-readability Sans-Serif body font",
            "A heavy, geometric technical Sans-Serif headline paired with a minimalist light Sans-Serif body font",
            "An editorial-style Slab-Serif headline with clean Monospace supporting lines",
        ]
        selected_layout = random.choice(layout_options)
        selected_color = random.choice(color_options)
        selected_font = random.choice(font_options)

        base_prompt = """
        You are an elite Pinterest Visual Strategist, Editorial Information Designer, UX Infographic Designer, Consumer Psychologist, and AI Image Engineer.

        Your task is to create ONE highly detailed image for a Pinterest-optimized vertical infographic.

        ==================================================
        TOPIC
        ==================================================
        {subject_matter}

        ==================================================
        LOCKED CONTENT PLAN (DO NOT DEVIATE)
        ==================================================
        {content_plan}

        ==================================================
        PRIMARY OBJECTIVE
        ==================================================
        Design a Pinterest pin image that immediately stops scrolling, communicates value within seconds, encourages users to save it, and motivates them to click for more information.
        The final image must look professionally designed, premium, clean, educational, trustworthy, and highly engaging.

        IMPORTANT: Generate ONLY the final image. Do NOT explain reasoning, output notes, or markdown.

        IMAGE FORMAT: Pinterest Vertical Pin, Aspect Ratio 2:3, High-resolution, Professional editorial quality.

        LAYOUT STYLE: Professional editorial grid with a {layout_structure} format. Consistent margins and spacing, large white space, balanced composition, perfect alignment.

        VISUAL HIERARCHY: 1. Headline 2. Hero visual 3. Main insight 4. Supporting insights 5. Takeaway 6. CTA.

        HEADLINE: Occupy ~25-35% upper canvas. Large, bold, extremely readable, benefit-driven, curiosity-driven, mobile-first. {headline_rule}

        HERO VISUAL: Use ONE dominant hero illustration communicating the emotional state related to the topic. Avoid stock-photo poses, decorative people, or unrelated illustrations.

        CONTENT STRUCTURE:
        {content_structure_block}

        CTA:
        {cta_block}

        TYPOGRAPHY: Modern editorial using {font_style} pairing. Bold headline, clear hierarchy, few font sizes, high contrast, generous spacing, clean alignment.

        COLOR PALETTE: {color_theme} Limited strategic accent colors, excellent contrast, calming and premium.

        MOBILE READABILITY: Every important element must remain readable without zooming.

        FINAL QUALITY CHECK: Topic immediately clear, headline dominates, Pinterest-optimized, premium, mobile readable, balanced white space. {quality_check_extra}
        Generate ONLY the final image.
        """

        if pin_type == "save":
            if image_teaser_points:
                plan_lines = ["Show ALL of these points on the image (full reveal - this is a SAVE-type pin):"]
                for pt in image_teaser_points:
                    plan_lines.append(f"- {pt}")
                content_plan = "\n        ".join(plan_lines)
            else:
                content_plan = "No locked plan provided - choose 3-5 strong points and explain each one fully. This is a SAVE-type pin."

            headline_rule = "This is a SAVE-type pin: the headline may fully state the benefit/promise. Clarity and completeness are important."
            content_structure_block = (
                "Create an infographic containing: one strong headline, one short subtitle, and three to five content blocks covering ALL main points. "
                "Each block should contain a short heading and one or two concise supporting lines that fully explain the point."
            )
            cta_block = "Place one subtle save-oriented CTA at the bottom (e.g. Save This Pin, Save & Come Back Anytime, Pin This for Later)."
            quality_check_extra = "The image fully delivers the promised value on its own and is worth saving."
        else:
            if image_teaser_points or hidden_hook:
                plan_lines = []
                if image_teaser_points:
                    plan_lines.append("Show ONLY these points on the image (short headings, without full explanation):")
                    for pt in image_teaser_points:
                        plan_lines.append(f"- {pt}")
                if hidden_hook:
                    plan_lines.append(f"NEVER reveal this on the image (exclusive to the blog): {hidden_hook}")
                content_plan = "\n        ".join(plan_lines)
            else:
                content_plan = "No locked plan provided - choose 2-3 strong points and withhold one clear specific detail for the blog."

            headline_rule = "OPEN LOOP RULE: headline must create curiosity, not resolve it. Name the promise but do not summarize the full answer."
            content_structure_block = (
                "Create an infographic containing: one strong headline, one short subtitle, and two to four content blocks (not full list). "
                "Each block should contain a short heading and ONE brief supporting line naming WHAT to do without fully explaining HOW or WHY. "
                "Use ONLY points from LOCKED CONTENT PLAN. Never reveal the hidden hook anywhere."
            )
            cta_block = "Place one subtle CTA at the bottom that drives tap-through (e.g. Read the Full Guide ->, See All Tips on the Blog, Get the Full Breakdown)."
            quality_check_extra = "The image does NOT fully answer the topic on its own and leaves a specific reason to click through."

        prompt = base_prompt.format(
            subject_matter=subject_matter,
            content_plan=content_plan,
            headline_rule=headline_rule,
            content_structure_block=content_structure_block,
            cta_block=cta_block,
            quality_check_extra=quality_check_extra,
            layout_structure=selected_layout,
            color_theme=selected_color,
            font_style=selected_font,
        )
        prompt_text = "Generate an image with a size strictly of 1024x1536 px, depicting the following scene: " + " ".join(prompt.split())

        print("[STEP] Opening ChatGPT...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        chat_box = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Locate ChatGPT chat textbox")
        chat_box.click()
        print("[OK] Textbox located and clicked", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        chat_box.fill(prompt_text)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        page.keyboard.press("Enter")
        print("[OK] Prompt sent", flush=True)

        # Simplified flow: wait for image/share button, click share, click download, save
        share_button = None
        found_share = False
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for generated image/share button... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
            try:
                share_button = resolve_locator(page, PLATFORM, "chatgpt_share_image_button", "Locate Share this image button after image generation")
                if share_button.is_visible():
                    found_share = True
                    print("[OK] Share image button found", flush=True)
                    break
            except Exception as e:
                print(f"[INFO] Share button not ready yet: {e}", flush=True)

        if not found_share or not share_button:
            raise RuntimeError("Share this image button not found after retries")

        print("[STEP] Clicking Share this image button...", flush=True)
        share_button.click()
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        print("[STEP] Clicking Download button...", flush=True)
        download_btn = resolve_locator(page, PLATFORM, "chatgpt_download_button", "Locate Download button in share/download flow")
        with page.expect_download(timeout=60000) as download_info:
            download_btn.click()
        download = download_info.value
        local_filename = IMAGE_DIR / "pin.png"
        download.save_as(str(local_filename))
        print(f"[OK] Image downloaded: {local_filename}", flush=True)

        ideas_list[target_index].update({"image_generated": True})
        with IDEAS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print(f"[OK] Updated image_generated=True for index {target_index}", flush=True)

        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
        print("[DONE] Image generation completed", flush=True)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        try:
            if "page" in locals() and page:
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
