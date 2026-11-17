import os
import sys
import json
import time
import random
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

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
POSTS_DIR = get_path("blogspot_post")
POST_FILE = POSTS_DIR / "post.md"
PLATFORM = "chatgpt"
DEFAULT_SAVE_PIN_PERCENTAGE = get("save_pin_percentage_default", 30)

INVISIBLE_CHARS_PATTERN = re.compile(r'[\ufeff\u200b\u200c\u200d\u2060\u00ad]')


def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def clean_invisible_chars(text: str) -> str:
    return INVISIBLE_CHARS_PATTERN.sub("", text)


def strip_canvas_wrapper(text: str) -> str:
    text = text.strip()
    while True:
        lines = text.split("\n")
        changed = False
        if lines and re.match(r'^:::[\w-]+\{.*\}\s*$', lines[0].strip()):
            lines = lines[1:]
            changed = True
        while lines and lines[0].strip() == "":
            lines.pop(0)
            changed = True
        while lines and lines[-1].strip() == "":
            lines.pop()
            changed = True
        if lines and re.match(r'^:{3,}\s*$', lines[-1].strip()):
            lines.pop()
            changed = True
        text = "\n".join(lines).strip()
        if not changed:
            break
    return text


def load_or_init_status():
    if STATUS_FILE.exists():
        try:
            with STATUS_FILE.open("r", encoding="utf-8") as f:
                status = json.load(f)
            if not isinstance(status, dict):
                raise ValueError("status.json root is not an object")
        except Exception as e:
            print(f"[WARNING] status.json corrupt/unreadable ({e}). Reinitializing with defaults.", flush=True)
            status = {}
    else:
        print("[INFO] status.json not found. Creating with defaults.", flush=True)
        status = {}

    raw_save_pct = status.get("save_pin_percentage")
    if raw_save_pct is None or not isinstance(raw_save_pct, (int, float)) or isinstance(raw_save_pct, bool):
        save_pct = DEFAULT_SAVE_PIN_PERCENTAGE
    else:
        save_pct = raw_save_pct
    if save_pct < 0 or save_pct > 100:
        print(f"[WARNING] save_pin_percentage={save_pct} out of range. Resetting to default {DEFAULT_SAVE_PIN_PERCENTAGE}.", flush=True)
        save_pct = DEFAULT_SAVE_PIN_PERCENTAGE
    save_pct = int(save_pct)
    status["save_pin_percentage"] = save_pct
    status["click_pin_percentage"] = 100 - save_pct

    counts = status.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    status["counts"] = {
        "total_pins": int(counts.get("total_pins", 0) or 0),
        "save_pins": int(counts.get("save_pins", 0) or 0),
        "click_pins": int(counts.get("click_pins", 0) or 0),
    }
    status.setdefault("current_pin_type", None)
    return status


def save_status(status):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATUS_FILE.open("w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)


def decide_pin_type(status):
    save_pct = status["save_pin_percentage"]
    counts = status["counts"]
    if save_pct <= 0:
        pin_type = "click"
    elif save_pct >= 100:
        pin_type = "save"
    else:
        total = counts["total_pins"]
        current_save_ratio = (counts["save_pins"] / total * 100) if total > 0 else 0.0
        pin_type = "save" if current_save_ratio < save_pct else "click"

    counts["total_pins"] += 1
    counts[f"{pin_type}_pins"] += 1
    status["current_pin_type"] = pin_type

    actual_pct = (counts["save_pins"] / counts["total_pins"] * 100) if counts["total_pins"] > 0 else 0.0
    print(
        f"[STATUS] target save%={save_pct} | actual so far: save={counts['save_pins']}/{counts['total_pins']} ({actual_pct:.1f}%) | Decided pin_type='{pin_type}'",
        flush=True,
    )
    return pin_type


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

    last_dict_item = None
    for item in ideas_list:
        if isinstance(item, dict):
            last_dict_item = item

    if last_dict_item is not None:
        url_val = str(last_dict_item.get("url") or "").strip()
        is_pipeline_complete = (
            last_dict_item.get("post_generated") is True
            and last_dict_item.get("post_image_generated") is True
            and last_dict_item.get("post_posted") is True
            and last_dict_item.get("content_generated") is True
            and last_dict_item.get("image_generated") is True
            and last_dict_item.get("posted") is True
            and url_val != ""
        )
        if not is_pipeline_complete:
            print("[INFO] Last pipeline is not fully complete yet. Exiting safely.", flush=True)
            sys.exit(0)

    subject_matter = None
    target_index = -1
    for index, item in enumerate(ideas_list):
        if isinstance(item, str):
            subject_matter = item
            target_index = index
            break

    if subject_matter is None or target_index == -1:
        print("[INFO] No new plain string topics available. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Last pipeline complete. Starting next topic: '{subject_matter}' at index [{target_index}]", flush=True)

    status = load_or_init_status()
    decide_pin_type(status)
    save_status(status)

    POSTS_DIR.mkdir(exist_ok=True)
    with POST_FILE.open("w", encoding="utf-8") as f:
        f.write("")
    print(f"[OK] Cleared/initialized {POST_FILE}", flush=True)

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

        textbox = resolve_locator(page, PLATFORM, "chatgpt_textbox", "Locate ChatGPT chat textbox for blog post generation")
        textbox.click()
        print("[OK] Textbox located and clicked", flush=True)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)

        current_date_str = datetime.now().strftime("%Y-%m-%d")
        prompt = (
            f"CRITICALLY AND STRICTLY IMPORTANT:\n"
            f"Return ONLY ONE complete comprehensive article wrapped inside a single \`\`\`markdown code block\`\`\`.\n"
            f"Do NOT output explanations, conversational introduction, greetings, or notes outside the markdown block.\n\n"

            f"ROLE\n"
            f"You are a professional Content Creator, Mental Health Blogger, SEO Copywriter, and Psychology Educator specializing in evidence-based mental wellness, mindfulness, emotional wellbeing, and self-improvement.\n\n"

            f"PRIMARY OBJECTIVE\n"
            f"Generate a deep, engaging, original, and comprehensive evergreen blog post based on the target title below. "
            f"The body length must be exactly between 1000 and 1500 words to ensure thorough exploration while maintaining excellent readability. "
            f"The article should be genuinely useful, highly informative, naturally engaging, and capable of ranking well on Google for its primary search intent.\n\n"

            f"INPUT TOPIC TITLE\n"
            f"'{subject_matter}'\n\n"

            f"CRITICALLY AND STRICTLY SUPER IMPORTANT METADATA FRONTMATTER REQUIREMENTS\n"
            f"The article must begin exactly with a YAML frontmatter block structured like this. "
            f"You must dynamically analyze the content to determine the most fitting category, read time, and relevant tags (all tags must strictly be formatted in Title Case):\n"
            f"---\n"
            f"title: \"[Catchy relevant version or exact topic title]\"\n"
            f"description: \"[Engaging summary of the article between 120-150 characters]\"\n"
            f"pubDate: \"{current_date_str}\"\n"
            f"category: \"[Dynamically determine appropriate category]\"\n"
            f"author: \"Mind To Better\"\n"
            f"readTime: \"[Dynamically estimate read time, e.g., '6 min read']\"\n"
            f"tags: [\"[Dynamic Tag 1]\", \"[Dynamic Tag 2]\", \"[Dynamic Tag 3]\"]\n"
            f"---\n\n"

            f"CONTENT STRUCTURE RULES\n"
            f"- Word Count: Strictly between 1000 and 1500 words.\n"
            f"- Structure: Do NOT follow a rigid or repetitive template. Create a natural progression with diverse H2 and H3 headings, explanatory paragraphs, logical sub-points, and clear structural lists wherever they genuinely improve readability. Do NOT include comparison tables.\n"
            f"- Tone: Compassionate, trustworthy, evidence-based, insightful, conversational, and highly engaging.\n"
            f"- Keep the article suitable for both beginners and readers already familiar with self-improvement concepts.\n"
            f"- Avoid generic AI-sounding introductions, robotic transitions, filler paragraphs, unnecessary repetition, or keyword stuffing.\n\n"

            f"E-E-A-T REQUIREMENTS\n"
            f"- Demonstrate expertise through nuanced explanations instead of generic advice.\n"
            f"- Explain WHY concepts work before explaining HOW to apply them.\n"
            f"- Where appropriate, naturally reference established psychological principles or reputable organizations such as APA, NIH, WHO, Mayo Clinic, Harvard Health, or similar trusted sources without making the article sound academic.\n"
            f"- Never fabricate research, statistics, quotes, or scientific findings.\n"
            f"- If mentioning research, describe it naturally instead of inserting formal citations.\n\n"

            f"SEARCH INTENT OPTIMIZATION\n"
            f"- Fully satisfy the likely Google search intent behind the topic.\n"
            f"- Anticipate the reader's next questions and answer them naturally inside the article.\n"
            f"- Ensure readers can finish the article without feeling the need to immediately search another page for missing information.\n"
            f"- Naturally incorporate semantic variations of the primary keyword without overusing exact-match phrases.\n"
            f"- Whenever appropriate, include concise explanatory paragraphs that have Featured Snippet potential.\n\n"

            f"ORIGINALITY REQUIREMENTS\n"
            f"- Every article must provide original explanations rather than repeating common internet advice.\n"
            f"- Prioritize insight over motivation.\n"
            f"- Avoid clichés and overused self-help statements.\n"
            f"- Introduce fresh perspectives whenever possible.\n"
            f"- Every major section should contribute at least one genuinely new insight.\n\n"

            f"WRITING NATURALNESS\n"
            f"- Write like an experienced human author, not an AI.\n"
            f"- Vary sentence lengths naturally.\n"
            f"- Vary paragraph lengths naturally.\n"
            f"- Occasionally use rhetorical questions when appropriate.\n"
            f"- Occasionally include relatable everyday situations or examples that readers can immediately recognize.\n"
            f"- Avoid making every paragraph follow the same rhythm.\n"
            f"- Avoid ending every section with a mini-summary.\n"
            f"- Avoid repetitive sentence openings.\n"
            f"- Avoid repeatedly starting paragraphs with phrases such as 'Another...', 'One important...', 'It is also...', or 'Over time...' unless absolutely necessary.\n\n"

            f"READABILITY\n"
            f"- Optimize for comfortable online reading.\n"
            f"- Prefer clear, simple English over unnecessarily academic vocabulary.\n"
            f"- Break long explanations into digestible paragraphs.\n"
            f"- Use bullet lists only when they genuinely improve clarity.\n\n"

            f"TRANSITIONS\n"
            f"- Ensure each section flows naturally into the next.\n"
            f"- Build ideas progressively rather than making sections feel independently generated.\n\n"

            f"PRACTICAL VALUE\n"
            f"- Every major section should accomplish at least one of the following:\n"
            f"  * Explain an important concept.\n"
            f"  * Explain why it matters.\n"
            f"  * Show how readers can apply it.\n"
            f"  * Correct a common misconception.\n"
            f"- Avoid paragraphs that merely restate previous information.\n\n"

            f"NEWSLETTER INTEGRATION\n"
            f"- Exactly once during the middle 40%–60% portion of the article, insert a short standalone subsection encouraging readers to subscribe for future evidence-based mental wellness and self-improvement content.\n"
            f"- Keep this subsection under 70 words.\n"
            f"- Immediately after that text, insert EXACTLY the following HTML snippet on its own line without modifying anything:\n\n"
            f"<script async data-uid=\"eaed1acc11\" src=\"https://mindtobetter.kit.com/eaed1acc11/index.js\"></script>\n\n"

            f"EBOOK RECOMMENDATION\n"
            f"- Exactly once during the article (not at the very beginning or very end), naturally recommend a related in-depth ebook for readers who want to explore the topic further.\n"
            f"- This recommendation must blend seamlessly into the surrounding content and should feel genuinely helpful rather than promotional.\n"
            f"- Never paste the raw URL.\n"
            f"- Instead, use an HTML hyperlink with given anchor text such as:\n"
            f"<a href=\"https://mindtobetter.blogspot.com/p/store.html\">CLICK HERE</a>\n"
            f"- The anchor text inside the <a href> tag must be exactly \"CLICK HERE\". Do not use any other anchor text.\n\n"

            f"CONCLUSION\n"
            f"- End with a thoughtful and memorable takeaway.\n"
            f"- Reinforce the central message without repeating earlier paragraphs.\n"
            f"- Avoid generic motivational endings or clichés.\n\n"

            f"FAQ SECTION\n"
            f"- End the article with an H2 heading titled 'Frequently Asked Questions'.\n"
            f"- Include exactly 3 to 5 concise, high-value FAQs based on the most likely questions readers would search after reading the article.\n"
            f"- Questions should target long-tail search intent and complement the main article rather than repeat it.\n"
            f"- Each answer should be between 40 and 80 words.\n"
            f"- Write answers naturally and conversationally while remaining evidence-based.\n"
            f"- Avoid obvious or redundant questions already answered in the main body.\n"
            f"- Do not use FAQ schema or JSON-LD; output only standard Markdown headings and paragraphs.\n\n"

            f"OUTPUT QUALITY\n"
            f"- The finished article should feel publication-ready for a high-quality mental wellness blog.\n"
            f"- Aim for content quality that is worthy of long-term evergreen rankings.\n"
            f"- Optimize for user satisfaction, readability, topical authority, and search engine visibility.\n"
            f"- Return valid Markdown only inside the requested markdown code block while preserving any required HTML exactly as instructed.\n"
        )

        textbox.fill(prompt)
        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        send_button = resolve_locator(page, PLATFORM, "chatgpt_send_button", "Locate ChatGPT send button")
        send_button.click()
        custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)

        print("[STEP] Waiting for generated markdown code block...", flush=True)
        markdown_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block (Attempt {attempt}/5)...", flush=True)
            try:
                code_block = resolve_locator(page, PLATFORM, "chatgpt_code_block", "Locate generated markdown code block/content")
                last_length = 0
                for cycle in range(25):
                    time.sleep(30)
                    current_text = code_block.inner_text().strip()
                    current_length = len(current_text)
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous={last_length}, Current={current_length}", flush=True)
                    if current_length > 0 and current_length == last_length:
                        markdown_content = clean_invisible_chars(current_text)
                        print("[OK] Markdown generation finalized.", flush=True)
                        break
                    last_length = current_length
            except Exception as e:
                print(f"[INFO] Code block not ready on attempt {attempt}: {e}", flush=True)
            if markdown_content:
                break
            if attempt < 5:
                custom_random_wait(MIN_RANDOM_WAIT_LONG, MAX_RANDOM_WAIT_LONG)
            else:
                raise RuntimeError("Markdown code block generation did not complete after retries")

        if markdown_content.startswith("```markdown"):
            markdown_content = markdown_content.split("```markdown", 1)[1]
        elif markdown_content.startswith("```"):
            markdown_content = markdown_content.split("```", 1)[1]
        if markdown_content.endswith("```"):
            markdown_content = markdown_content.rsplit("```", 1)[0]

        clean_output = strip_canvas_wrapper(markdown_content.strip())
        starts_with_frontmatter = clean_output.startswith("---")
        has_faq_section = bool(re.search(r'^#{1,3}\s*Frequently Asked Questions', clean_output, re.IGNORECASE | re.MULTILINE))
        if not starts_with_frontmatter or not has_faq_section:
            raise RuntimeError(f"Generated content failed sanity check (frontmatter={starts_with_frontmatter}, faq={has_faq_section})")

        with POST_FILE.open("w", encoding="utf-8") as f:
            f.write(clean_output)
        print(f"[OK] Saved markdown to {POST_FILE}", flush=True)

        ideas_list[target_index] = {
            "title": subject_matter,
            "post_generated": True,
            "post_image_generated": False,
            "post_posted": False,
            "url": "",
            "content_generated": False,
            "image_generated": False,
            "posted": False,
        }
        with IDEAS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print("[OK] Updated pinterest_ideas.json with initialized pipeline state", flush=True)

        custom_random_wait(MIN_RANDOM_WAIT_SHORT, MAX_RANDOM_WAIT_SHORT)
        print("[DONE] Post generation completed", flush=True)

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
