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
IMAGE_DIR = get_path("blogspot_image")
IMAGE_DIR.mkdir(exist_ok=True)
IDEAS_FILE = get_path("json", "pinterest_ideas.json")
PLATFORM = "chatgpt"


def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    import time
    time.sleep(seconds)


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
        if isinstance(item, dict) and item.get("post_generated") is True and item.get("post_image_generated") is False:
            subject_matter = item.get("title") or item.get("subject") or list(item.values())[0]
            target_index = index
            break

    if subject_matter is None or target_index == -1:
        print("[INFO] Target conditions not met. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Selected target subject: '{subject_matter}' at index [{target_index}]", flush=True)

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

        
        base_prompt = (
            "You are a world-class Digital Editorial Art Director, "
            "Visual Storyteller, and Award-Winning Conceptual Illustrator. "

            "Your mission is to transform the given blog topic - {subject_matter} "
            "into a visually striking, emotionally engaging, and intellectually "
            "intriguing HERO IMAGE that immediately captures attention. "

            "IMPORTANT CREATIVE DIRECTION: "
            "Do not create a simple, literal, predictable, or generic representation "
            "of the topic. Think like a creative director designing the cover of "
            "a premium international magazine. Every image must have a strong "
            "visual hook that makes viewers stop scrolling and become curious. "

            "1. VISUAL HOOK & CONCEPT: "
            "Identify the most interesting, surprising, dramatic, or thought-provoking "
            "aspect of the subject. Translate it into a powerful visual metaphor, "
            "unexpected visual contrast, intriguing scenario, or imaginative concept. "
            "Create one dominant focal point with a clear visual story. "
            "The image should communicate an idea, not merely depict an object. "
            "Avoid obvious stock-photo compositions and overused visual cliches. "

            "2. VISUAL STYLE: "
            "Use premium cinematic editorial illustration with a sophisticated "
            "semi-realistic 3D aesthetic, artistic lighting, rich visual textures, "
            "dramatic shadows, atmospheric depth, and carefully composed colors. "
            "Blend photorealistic environments with stylized conceptual elements "
            "where appropriate. The result should feel like premium magazine cover art, "
            "not a basic AI-generated illustration. "

            "3. HUMAN FIGURES: "
            "Human characters are allowed and encouraged when they strengthen "
            "the visual storytelling. Use tasteful, lightly cartoonized, "
            "semi-realistic illustrated human figures with natural proportions, "
            "expressive body language, and subtle facial expressions. "
            "Avoid fully realistic human portraits, uncanny faces, exaggerated "
            "cartoon characters, childish illustrations, or corporate stock-photo people. "
            "Human figures should support the central concept, never dominate "
            "the composition unnecessarily. "

            "4. COMPOSITION & DEPTH: "
            "Create a visually rich, layered composition with foreground, "
            "midground, and background elements. Use dramatic perspective, "
            "intentional asymmetry, dynamic visual flow, selective depth of field, "
            "and strong subject-background separation. "
            "Avoid empty, flat, overly minimal, or visually uninteresting layouts. "
            "Maintain a clear focal hierarchy and balanced negative space. "

            "5. COLOR & LIGHTING: "
            "Use a sophisticated, topic-appropriate color palette. "
            "Combine rich complementary colors, cinematic highlights, "
            "subtle gradients, realistic reflections, and directional lighting. "
            "Avoid dull, washed-out, excessively pale, or monotonous colors. "

            "6. FORMAT & QUALITY: "
            "Landscape 16:9 aspect ratio, high resolution, premium digital editorial "
            "artwork, polished details, professional composition, visually engaging "
            "even when viewed as a small blog thumbnail. "

            "7. STRICT RESTRICTIONS: "
            "Absolutely NO text, letters, typography, numbers, captions, "
            "logos, watermarks, signatures, website interfaces, or UI elements. "
            "Do not generate a poster containing written content. "

            "8. CREATIVE FREEDOM: "
            "Do not follow a fixed visual template for every topic. "
            "Vary the visual concept, camera angle, environment, character placement, "
            "metaphor, lighting, and composition according to the subject matter. "
            "Prioritize originality, curiosity, emotional impact, and visual storytelling. "

            "FINAL OUTPUT: Generate and return ONLY the final image. "
            "Do not output any text, explanation, prompt, description, markdown, or code."
        )

        prompt_text = "Generate a high-quality ultra-detailed image with an aspect ratio of strictly 16:9, depicting the following scene: " + " ".join(
            base_prompt.format(subject_matter=subject_matter).split()
        )

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
        local_filename = IMAGE_DIR / "post.png"
        download.save_as(str(local_filename))
        print(f"[OK] Image downloaded: {local_filename}", flush=True)

        ideas_list[target_index].update({"post_image_generated": True})
        with IDEAS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print(f"[OK] Updated post_image_generated=True for index {target_index}", flush=True)

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
