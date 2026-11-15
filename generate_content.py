import os
import sys
import json
import time
import random
import re
from pathlib import Path
from typing import Any, Dict, List

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

COOKIES_DIR = get_path("chatgpt_cookies")
IDEAS_FILE = get_path("json", "pinterest_ideas.json")
STATUS_FILE = get_path("json", "status.json")
ARTICLE_FILE = get_path("json", "article.json")
BLOG_POST_FILE = get_path("blogspot_post", "post.md")
PLATFORM = "chatgpt"

ALLOWED_BOARDS = [
    "Anxiety & Mental Peace",
    "Calm Mind Habits",
    "Focus & Mental Discipline",
    "Mental Clarity",
    "Overthinking Help",
    "Self-Improvement Psychology",
]


def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
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
            print(f"[WARNING] Invalid/missing current_pin_type ('{pin_type}'). Defaulting to '{default}'.", flush=True)
            return default
        return pin_type
    except Exception as e:
        print(f"[WARNING] Could not read status.json ({e}). Defaulting pin_type to '{default}'.", flush=True)
        return default


def read_blog_post():
    if not BLOG_POST_FILE.exists():
        print(f"[WARNING] {BLOG_POST_FILE} not found. Generating without article grounding.", flush=True)
        return None
    try:
        text = BLOG_POST_FILE.read_text(encoding="utf-8").strip()
        if not text:
            print(f"[WARNING] {BLOG_POST_FILE} is empty. Generating without article grounding.", flush=True)
            return None
        return text
    except Exception as e:
        print(f"[WARNING] Could not read {BLOG_POST_FILE} ({e}). Generating without article grounding.", flush=True)
        return None


def run():
    print("[START] Script started", flush=True)
    apply_auto_correct()

    if not IDEAS_FILE.exists():
        print(f"[ERROR] Ideas file not found: {IDEAS_FILE}", flush=True)
        sys.exit(1)

    try:
        with IDEAS_FILE.open("r", encoding="utf-8") as f:
            ideas_list = json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not parse ideas file: {e}", flush=True)
        sys.exit(1)

    subject_matter = None
    target_index = -1
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict) and item.get("url") and item.get("content_generated") is False:
            subject_matter = item.get("title")
            target_index = index
            break

    if subject_matter is None or target_index == -1:
        print("[INFO] No matching item with valid URL and content_generated=False. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Target topic: '{subject_matter}' at index [{target_index}]", flush=True)

    ARTICLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ARTICLE_FILE.open("w", encoding="utf-8") as f:
        f.write("")
    print(f"[OK] Initialized {ARTICLE_FILE}", flush=True)

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

        print("[STEP] Opening ChatGPT...", flush=True)
        page.goto(CHATGPT_URL, wait_until="load")
        print("[OK] ChatGPT opened", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        try:
            continue_button = resolve_locator(page, PLATFORM, "chatgpt_continue_button", "Locate Continue button if shown after login")
            if continue_button.is_visible():
                continue_button.click()
                custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        except Exception:
            pass

        textbox = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Locate ChatGPT chat textbox for metadata generation")
        textbox.click()
        print("[OK] Textbox located and clicked", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        pin_type = read_pin_type()
        print(f"[OK] Pin type from status: '{pin_type}'", flush=True)
        blog_post_content = read_blog_post()
        if blog_post_content:
            print(f"[OK] Loaded blog post ({len(blog_post_content)} chars) for grounding", flush=True)

        if pin_type == "save":
            title_rules = (
                "- Target length: 60-85 characters. Never exceed 95.\n"
                "- Naturally include the complete input topic exactly once.\n"
                "- Front-load strongest searchable keyword when natural.\n"
                "- Optimize for Pinterest Search first, CTR second.\n"
                "- Promise a clear, complete benefit (save-worthy, self-contained).\n"
                "- Human sounding only. No clickbait, no emojis, no hashtags, no quotes."
            )
            description_rules = (
                "- Target length: 350-600 characters. Never exceed 700.\n"
                "- First sentence communicates value immediately with a compelling hook.\n"
                "- Reinforce primary keyword and include semantic Pinterest search keywords naturally.\n"
                "- Address real pain point, explain practical benefit, build curiosity.\n"
                "- Make content feel complete and save-worthy on its own.\n"
                "- End with ONE natural save-oriented CTA (e.g. Save this pin for later, Explore the complete guide).\n"
                "- Educational tone, never spammy. No emojis. Hashtags optional (max 3)."
            )
            sync_rules = (
                "A separate image-generation step will run AFTER this one using ONLY this JSON. "
                "This is a SAVE-type pin (no curiosity gap). Output:\n"
                "- 'image_teaser_points': array of 3-5 short strings covering ALL main points (full reveal).\n"
                "- 'hidden_hook': empty string \"\".\n"
                "GROUNDING: image_teaser_points must come from the actual blog article content below when available."
            )
        else:
            title_rules = (
                "- Target length: 60-85 characters. Never exceed 95.\n"
                "- Naturally include the complete input topic exactly once.\n"
                "- Front-load strongest searchable keyword when natural.\n"
                "- Optimize for Pinterest Search first, CTR second.\n"
                "- Promise a clear benefit WITHOUT revealing full answer/method/steps.\n"
                "- OPEN LOOP: title must create a question only the linked article answers.\n"
                "- Human sounding only. No clickbait, no emojis, no hashtags, no quotes."
            )
            description_rules = (
                "- Target length: 350-600 characters. Never exceed 700.\n"
                "- First sentence communicates value immediately with a compelling hook.\n"
                "- Reinforce primary keyword and include semantic Pinterest search keywords naturally.\n"
                "- Address real pain point, explain practical benefit, build curiosity.\n"
                "- CLICK HOOK RULE: include ONE specific detail that exists ONLY on the blog and not on the pin image.\n"
                "- End with ONE natural CTA that pushes toward click (e.g. Tap to read the full guide, Read the complete method).\n"
                "- Educational tone, never spammy. No emojis. Hashtags optional (max 3)."
            )
            sync_rules = (
                "A separate image-generation step will run AFTER this one using ONLY this JSON. "
                "Keep image and description in sync. Output:\n"
                "- 'image_teaser_points': array of 2-4 short strings (headings/labels only, no full how/why). Only these may appear on image.\n"
                "- 'hidden_hook': ONE sentence describing the specific detail that must NEVER appear on image and only on blog. Must match description's click hook.\n"
                "GROUNDING: both must be REAL content from the blog article below; never invent specifics not covered."
            )

        prompt = (
            "IMPORTANT:\n"
            "Return ONLY ONE valid JSON object wrapped inside a single ```json code block```.\n"
            "Do NOT output explanations, markdown, comments, notes or conversational text outside the JSON block.\n\n"
            "ROLE\nYou are an elite Pinterest SEO Strategist, Pinterest Trend Analyst, Consumer Psychologist and Direct Response Copywriter.\n\n"
            "PRIMARY OBJECTIVE\nGenerate Pinterest metadata that maximizes Search Visibility, Ranking, CTR, Saves, Outbound Clicks, Engagement, and Human Readability.\n\n"
            f"INPUT TOPIC\n{subject_matter}\n\n"
            "========================================\nBLOG ARTICLE CONTENT (actual article the pin links to)\n========================================\n"
            f"{blog_post_content if blog_post_content else '[Not available - keep content general and do not invent specifics.]'}\n\n"
            "Everything you write must accurately reflect what is actually written in this article.\n\n"
            "INTERNAL ANALYSIS (DO NOT OUTPUT): silently determine primary intent, audience, primary/secondary/semantic keywords, pain point, desired transformation, emotional/curiosity trigger, angle, hook style, CTA style, awareness stage, best matching board.\n\n"
            "TITLE REQUIREMENTS:\n" + title_rules + "\n\n"
            "DESCRIPTION REQUIREMENTS:\n" + description_rules + "\n\n"
            "ALT TEXT REQUIREMENTS:\n"
            "- Target 120-250 chars (max 350). Describe infographic for accessibility.\n"
            "- Mention main topic naturally and include primary keyword once.\n"
            "- Do not copy title or paraphrase description. Human language only. No hashtags/emojis.\n\n"
            "BOARD: Choose EXACTLY ONE from:\n"
            "- Anxiety & Mental Peace\n- Calm Mind Habits\n- Focus & Mental Discipline\n- Mental Clarity\n- Overthinking Help\n- Self-Improvement Psychology\n\n"
            "DIVERSITY/NEGATIVE RULES: avoid repetitive structures/openings/adjectives/AI transitions and banned phrases; no keyword stuffing, robotic copy, invented facts/stats/medical claims.\n\n"
            "IMAGE-CONTENT SYNC CONTRACT (CRITICAL):\n" + sync_rules + "\n\n"
            "OUTPUT FORMAT:\n"
            "{\n"
            '  "title": "...",\n'
            '  "description": "...",\n'
            '  "alt_text": "...",\n'
            '  "selected_board": "...",\n'
            '  "image_teaser_points": ["...", "..."],\n'
            '  "hidden_hook": "..."\n'
            "}"
        )

        textbox.fill(prompt)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        send_button = resolve_locator(page, PLATFORM, "chatgpt_send_button", "Locate ChatGPT send button")
        send_button.click()
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        print("[STEP] Waiting for generated JSON code block...", flush=True)
        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block (Attempt {attempt}/5)...", flush=True)
            try:
                code_block = resolve_locator(page, PLATFORM, "chatgpt_code_block", "Locate generated JSON code block/content")
                last_length = 0
                for cycle in range(15):
                    time.sleep(15)
                    current_text = code_block.inner_text().strip()
                    current_length = len(current_text)
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous={last_length}, Current={current_length}", flush=True)
                    if current_length > 0 and current_length == last_length and current_text.endswith("}"):
                        json_content = current_text
                        print("[OK] JSON generation finalized.", flush=True)
                        break
                    last_length = current_length
            except Exception as e:
                print(f"[INFO] Code block not ready on attempt {attempt}: {e}", flush=True)
            if json_content:
                break
            if attempt < 5:
                custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
            else:
                raise RuntimeError("JSON code block generation did not complete after retries")

        try:
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', json_content)
            if json_match:
                clean_json_str = json_match.group(1)
            else:
                fallback_match = re.search(r'\{[\s\S]*\}', json_content)
                clean_json_str = fallback_match.group(0) if fallback_match else json_content.strip()
            parsed_json = json.loads(clean_json_str)
        except Exception as je:
            raise RuntimeError(f"Failed to parse generated JSON: {je}")

        if parsed_json.get("selected_board") not in ALLOWED_BOARDS:
            print(f"[WARNING] Invalid board '{parsed_json.get('selected_board')}'. Using safe fallback.", flush=True)
            parsed_json["selected_board"] = random.choice(ALLOWED_BOARDS)
        parsed_json["pin_type"] = pin_type

        with ARTICLE_FILE.open("w", encoding="utf-8") as f:
            json.dump(parsed_json, f, indent=4, ensure_ascii=False)
        print(f"[OK] Saved metadata to {ARTICLE_FILE}", flush=True)

        ideas_list[target_index]["content_generated"] = True
        with IDEAS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print(f"[OK] Updated content_generated=True for '{subject_matter}'", flush=True)

        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        print("[DONE] Content generation completed", flush=True)

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
