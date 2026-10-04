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
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

STATUS_FILE = Path("status.json")

IMAGE_DIR = Path("image")
IMAGE_DIR.mkdir(exist_ok=True)

PBKDF2_ITERATIONS = 200_000
MAX_RETRIES = 10

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


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

def upload_to_onlyfiles(screenshot_path):
    url = "https://api.onlyfiles.com/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file}, data={"expire": 172800})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL from the API response
        direct_url = res_data["data"]["file"]["url"]["full"]
        print(f"👉 DIRECT LINK (Expires in 48 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None


# =========================
# STATUS.JSON (read-only here — post_generate.py owns the decision)
# =========================
def read_pin_type(default="click"):
    """
    Reads the pin_type ('save' or 'click') that post_generate.py decided and
    persisted for the item currently in flight. This script never decides or
    mutates the ratio itself — it only consumes the decision.
    """
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

# =========================
# MAIN
# =========================
def run():
    ideas_file = Path("pinterest_ideas.json")
    
    if not ideas_file.exists():
        raise RuntimeError("❌ 'pinterest_ideas.json' file not found!")

    # ========================================================
    # LOAD & PARSE IDEAS JSON (STRICT FILTER REGISTRATION)
    # ========================================================
    print("[STEP] Loading Pinterest ideas JSON...", flush=True)
    with ideas_file.open("r", encoding="utf-8") as f:
        ideas_list = json.load(f)

    subject_matter = None
    target_index = -1

    # FIXED CONDITION: Find the entry where content_generated is True and image_generated is False
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict):
            if (item.get("content_generated") is True and 
                item.get("image_generated") is False):
                
                subject_matter = item.get("title") or item.get("subject") or list(item.values())[0]
                target_index = index
                break

    # If criteria condition falls out, exit early with tracking logs
    if subject_matter is None or target_index == -1:
        print("[INFO] Target conditions ('content_generated': true and 'image_generated': false) not met! Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Selected Target Subject Matter: '{subject_matter}' at index [{target_index}]", flush=True)

    # ========================================================
    # LOAD article.json — SHARED CONTRACT FROM generate_content.py
    # ========================================================
    # generate_content.py runs first for this same topic and writes article.json
    # with the exact title/teaser points/hidden hook it used. We MUST reuse these
    # exact values here, otherwise the image and the description will each guess
    # a different "withheld detail" and the curiosity gap won't line up.
    article_file = Path("article.json")
    generated_title = None
    image_teaser_points = []
    hidden_hook = None
    article_pin_type = None

    if article_file.exists():
        try:
            with article_file.open("r", encoding="utf-8") as f:
                article_data = json.load(f)
            generated_title = article_data.get("title")
            image_teaser_points = article_data.get("image_teaser_points") or []
            hidden_hook = article_data.get("hidden_hook")
            article_pin_type = article_data.get("pin_type")
            print(f"[OK] Loaded article.json contract -> teaser_points={image_teaser_points}, hidden_hook='{hidden_hook}', pin_type='{article_pin_type}'", flush=True)
        except Exception as article_err:
            print(f"[WARNING] Could not parse article.json, falling back to raw topic only: {article_err}", flush=True)
    else:
        print("[WARNING] article.json not found. Image will be generated WITHOUT sync to the description's hidden hook.", flush=True)

    # ========================================================
    # PIN TYPE (save vs click) — decided once by post_generate.py,
    # persisted in status.json. This script only reads it (never mutates it).
    # ========================================================
    pin_type = read_pin_type()
    print(f"[OK] Pin type for this topic (from status.json): '{pin_type}'", flush=True)

    if article_pin_type and article_pin_type != pin_type:
        print(f"[WARNING] pin_type mismatch! status.json says '{pin_type}' but article.json (written by generate_content.py) says '{article_pin_type}'. "
              f"This usually means status.json's save/click ratio was changed mid-pipeline for this topic. "
              f"Trusting article.json's value to stay consistent with the already-generated title/description.", flush=True)
        pin_type = article_pin_type

    # Prefer the polished, AI-generated title over the raw idea string when available
    if generated_title:
        subject_matter = generated_title

    print("[START] Script started", flush=True)
    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
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

        layout_options = [
            "Vertical step-by-step timeline (1, 2, 3) connected by a subtle dotted line",
            "Side-by-side comparison matrix (This vs That split layout)",
            "4-quadrant clean grid structure dividing the concepts into boxes",
            "Central core concept with 4 radiating minimalist branches or arrows"
        ]

        color_options = [
            "Deep dark obsidian background with crisp white and soft sage green accents.",
            "Warm minimalist cream background with rich terracotta and charcoal grey accents.",
            "Clean stark white background with sophisticated navy blue and soft gold accent highlights.",
            "Soft muted beige background with deep chocolate brown text and burnt orange accents."
        ]

        font_options = [
            "A bold, elegant Serif headline paired with a clean, high-readability Sans-Serif body font",
            "A heavy, geometric technical Sans-Serif headline paired with a minimalist light Sans-Serif body font",
            "An editorial-style Slab-Serif headline with clean Monospace supporting lines"
        ]

        # 2. Runtime par random element select karna
        selected_layout = random.choice(layout_options)
        selected_color = random.choice(color_options)
        selected_font = random.choice(font_options)

        # Base strategic blueprint for prompt creation
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

        The final image must look like a professionally designed Pinterest infographic created by an experienced editorial designer—not by AI.

        The design should feel premium, clean, educational, trustworthy, and highly engaging.

        ==================================================
        IMPORTANT
        ==================================================

        Generate ONLY the final image.

        Do NOT explain your reasoning.

        Do NOT output design notes.

        Do NOT output markdown.

        ==================================================
        DO NOT CREATE
        ==================================================

        • Book covers
        • eBook covers
        • Magazine covers
        • Posters
        • Advertisements
        • Product mockups
        • Generic social media posts
        • Quote graphics
        • Presentation slides
        • AI concept art
        • Decorative artwork
        • Title-only graphics
        • Busy collage layouts

        ==================================================
        IMAGE FORMAT
        ==================================================

        Pinterest Vertical Pin

        Aspect Ratio:
        2:3

        High-resolution

        Professional editorial quality

        ==================================================
        LAYOUT STYLE
        ==================================================

        Use a professional editorial grid with a {layout_structure} format.

        Consistent margins.

        Consistent spacing.

        Large white space.

        Balanced composition.

        Perfect alignment.

        Premium visual rhythm.

        ==================================================
        DESIGN GOAL
        ==================================================

        Imagine this image competing against dozens of Pinterest pins.

        The design should maximize:

        • Scroll stopping
        • Click-through rate
        • Saves
        • Shares
        • Readability
        • Visual clarity
        • Trust
        • Educational value

        ==================================================
        VISUAL HIERARCHY
        ==================================================

        The eye should naturally move through the design in this order:

        1. Headline
        2. Hero visual
        3. Main insight
        4. Supporting insights
        5. Takeaway
        6. CTA

        Nothing should interrupt this reading flow.

        ==================================================
        HEADLINE
        ==================================================

        The headline is the single most important visual element.

        Requirements:

        • Occupy approximately 25–35 percent of the upper canvas.
        • Large.
        • Bold.
        • Extremely readable.
        • Benefit-driven.
        • Curiosity-driven.
        • Mobile-first.
        • Easy to understand within two seconds.
        • Never feel like a blog title.
        • Never feel like a textbook heading.

        The headline should stop scrolling before explaining.

        {headline_rule}

        ==================================================
        HERO VISUAL
        ==================================================

        Use ONE dominant hero illustration.

        The illustration should communicate the emotional state of the reader.

        Examples:

        • overwhelmed mind
        • calm mind
        • person reflecting
        • decision making
        • mental clutter becoming clarity
        • stress transforming into peace
        • focus replacing distraction

        Avoid stock-photo style poses.

        Avoid decorative people.

        Avoid multiple unrelated illustrations.

        The hero visual should immediately communicate the topic even before reading.

        ==================================================
        CONTENT STRUCTURE
        ==================================================

        {content_structure_block}

        ==================================================
        CONTENT DENSITY
        ==================================================

        Prioritize simplicity.

        Remove unnecessary information.

        Less text is better.

        Each section should communicate one idea only.

        Do not overload the design.

        Reduce cognitive load wherever possible.

        ==================================================
        VISUAL STORYTELLING
        ==================================================

        Every illustration, icon and visual element must reinforce the educational message.

        Visuals should never exist only for decoration.

        Use:

        • psychology illustrations
        • simple diagrams
        • arrows
        • progress indicators
        • minimal icons
        • subtle dividers
        • meaningful symbols

        Avoid visual clutter.

        ==================================================
        TRANSFORMATION SECTION
        ==================================================

        Near the bottom, include one concise transformation summary showing the desired outcome after applying the advice.

        This section should feel motivating rather than promotional.

        ==================================================
        CTA
        ==================================================

        {cta_block}

        Only ONE CTA.

        ==================================================
        TYPOGRAPHY
        ==================================================

        Typography should feel modern editorial using {font_style} pairing.

        Use:

        • bold headline
        • clear hierarchy
        • few font sizes
        • high contrast
        • generous spacing
        • clean alignment

        Avoid:

        • decorative fonts
        • script fonts
        • curved text
        • excessive font variation
        • text effects

        ==================================================
        COLOR PALETTE
        ==================================================

        Modern self-improvement aesthetic.

        {color_theme}

        Limited strategic accent colors.

        Excellent contrast.

        Calming, trustworthy and premium.

        Never oversaturate colors.

        ==================================================
        MOBILE READABILITY
        ==================================================

        Assume the image will first be viewed on a phone.

        Every important element must remain readable without zooming.

        Prioritize readability over additional content.

        ==================================================
        VISUAL STYLE
        ==================================================

        Premium Pinterest infographic.

        Editorial information design.

        Modern self-improvement niche.

        Minimal clutter.

        High-end publication quality.

        Clean vector illustration mixed with subtle realism.

        Professional digital product quality.

        ==================================================
        NEGATIVE REQUIREMENTS
        ==================================================

        Do NOT:

        • overload text
        • create long paragraphs
        • create tiny unreadable fonts
        • use unnecessary decorations
        • create multiple competing focal points
        • generate random icons
        • create visual clutter
        • use generic AI layouts
        • produce stock-photo aesthetics
        • overuse gradients
        • overuse shadows
        • use inconsistent illustration styles

        ==================================================
        FINAL QUALITY CHECK
        ==================================================

        Before producing the final image ensure that:

        ✓ The design immediately communicates the topic.

        ✓ The headline dominates attention.

        ✓ The layout is optimized for Pinterest.

        ✓ The design looks premium.

        ✓ Mobile readability is excellent.

        ✓ Information hierarchy is obvious.

        ✓ White space is balanced.

        ✓ Visuals support the educational message.

        ✓ The design feels human-made.

        {quality_check_extra}

        ✓ The overall result resembles a top-performing Pinterest infographic created by an experienced designer.

        Generate ONLY the final image.
        """

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto("https://chatgpt.com/", wait_until="load")
        print("[OK] URL opened", flush=True)

        # Initial random wait (30-60 seconds)
        print("[STEP] Performing initial random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

        # Check login state
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0]).or_(page.get_by_role('button', name='Open profile menu'))
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        create_image_btn = page.get_by_role('button', name='Create an image', exact=True)

        if create_image_btn.is_visible():
            create_image_btn.click()
            print("[STEP] Create an image button clicked!...", flush=True)
            custom_random_wait(6, 12)
            
        # Locate chat box
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = page.get_by_role('textbox', name='Chat with ChatGPT').or_(page.get_by_role('textbox', name='Ask ChatGPT'))
        if chat_box.count() == 0:
            chat_box = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Describe or edit an image')).first
        if chat_box.count() == 0:
            chat_box = page.locator('#prompt-textarea')

        if chat_box.count() > 0:
            chat_box.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
        
        # Step B: Build pin_type-conditional prompt blocks
        if pin_type == "save":
            # SAVE-type pin: fully self-contained, no curiosity gap, save-oriented CTA.
            if image_teaser_points:
                plan_lines = ["Show ALL of these points on the image (full reveal — this is a SAVE-type pin, not a teaser):"]
                for pt in image_teaser_points:
                    plan_lines.append(f"- {pt}")
                content_plan = "\n        ".join(plan_lines)
            else:
                content_plan = "No locked plan was provided — choose 3-5 strong points yourself and explain each one fully. This is a SAVE-type pin, so nothing needs to be withheld."

            headline_rule = (
                "This is a SAVE-type pin: the headline MAY fully state the benefit/promise. "
                "It does not need to leave a question unanswered — clarity and completeness are more important than curiosity here."
            )

            content_structure_block = (
                "Create an infographic containing:\n\n"
                "        • One strong headline\n\n"
                "        • One short subtitle\n\n"
                "        • Three to five content blocks covering ALL the main points of the topic\n\n"
                "        Each block should contain:\n\n"
                "        • short heading\n"
                "        • one or two concise supporting lines that fully explain the point (this pin is meant to be a complete, standalone reference worth saving)\n\n"
                "        Each supporting line should remain short enough to read comfortably on a phone screen.\n\n"
                "        Never create long paragraphs."
            )

            cta_block = (
                "Place one subtle but visible CTA at the bottom that encourages saving and returning to this pin.\n\n"
                "        Prefer examples like:\n\n"
                "        • Save This Pin\n"
                "        • Save & Come Back Anytime\n"
                "        • Pin This for Later\n\n"
                "        This pin is meant to be a complete, standalone reference, so a save-oriented CTA is correct here."
            )

            quality_check_extra = "✓ The image fully delivers the promised value on its own, making it complete and worth saving."

        else:  # pin_type == "click"
            if image_teaser_points or hidden_hook:
                plan_lines = []
                if image_teaser_points:
                    plan_lines.append("Show ONLY these points on the image (as short headings, without full explanation):")
                    for pt in image_teaser_points:
                        plan_lines.append(f"- {pt}")
                if hidden_hook:
                    plan_lines.append(f"NEVER reveal this on the image (it is exclusive to the blog): {hidden_hook}")
                content_plan = "\n        ".join(plan_lines)
            else:
                content_plan = "No locked plan was provided — choose 2-3 strong points yourself and deliberately withhold one clear, specific detail for the blog."

            headline_rule = (
                "OPEN LOOP RULE: The headline must create curiosity, not resolve it. It should name the topic/promise but must NOT "
                "summarize the full answer (e.g. avoid headlines that already state the complete method). A reader should finish the "
                "headline still wanting to know \"how exactly\" — that \"how exactly\" lives on the blog, not on this image."
            )

            content_structure_block = (
                "Create an infographic containing:\n\n"
                "        • One strong headline\n\n"
                "        • One short subtitle\n\n"
                "        • Two to four content blocks (NOT the full list — see CURIOSITY GAP RULE below)\n\n"
                "        Each block should contain:\n\n"
                "        • short heading\n"
                "        • ONE brief supporting line that names WHAT to do, without fully explaining HOW or WHY\n\n"
                "        Each supporting line should remain short enough to read comfortably on a phone screen.\n\n"
                "        Never create long paragraphs.\n\n"
                "        ==================================================\n"
                "        CURIOSITY GAP RULE (CRITICAL FOR CLICK-THROUGH)\n"
                "        ==================================================\n\n"
                "        Use ONLY the points listed under LOCKED CONTENT PLAN above as your content blocks — do not add extra tips of your own, "
                "and do not include more than what is listed there. If LOCKED CONTENT PLAN did not provide specific points, fall back to showing "
                "only 2-3 of the strongest points for the topic and add a small text element such as \"+ more inside\".\n\n"
                "        Do NOT fully explain any single point. Give the heading/label of the tip (the \"what\") as provided, but leave the "
                "mechanism, the number, the exact method, or the reasoning (\"the how/why\") unanswered — that must only be resolved by reading "
                "the blog post.\n\n"
                "        If a \"hidden hook\" was provided in the LOCKED CONTENT PLAN, that specific detail must NEVER appear anywhere in the "
                "image — not in the headline, not in any content block, not in the transformation section. It exists exclusively on the blog.\n\n"
                "        The image should function as a compelling table of contents, not as a standalone answer. A viewer who reads only the "
                "image should still have a clear, specific reason to tap through."
            )

            cta_block = (
                "Place one subtle but visible CTA at the bottom that drives the reader to TAP THROUGH, not just save.\n\n"
                "        Prefer examples like:\n\n"
                "        • Read the Full Guide →\n"
                "        • See All [N] Tips on the Blog\n"
                "        • Get the Full Breakdown\n"
                "        • Tap for the Complete Method\n\n"
                "        Avoid using \"Save this Pin\" as the CTA text on the image itself — saving should happen naturally because the content "
                "is good, but the on-image CTA text should point toward the linked article, since the CURIOSITY GAP RULE above depends on the "
                "reader wanting to click for the rest."
            )

            quality_check_extra = "✓ The image does NOT fully answer the topic on its own — it leaves a specific, nameable reason to click through to the blog."

        print(f"[OK] Using '{pin_type}'-style image prompt blocks.", flush=True)

        formatted_base = base_prompt.format(
            subject_matter=subject_matter,
            content_plan=content_plan,
            headline_rule=headline_rule,
            content_structure_block=content_structure_block,
            cta_block=cta_block,
            quality_check_extra=quality_check_extra,
            layout_structure=selected_layout,
            color_theme=selected_color,
            font_style=selected_font
        )
        clean_base_prompt = " ".join(formatted_base.split())
        prompt_text = f"Generate a 8k image with a size strictly of 1024x1536 px, depicting the following scene: {clean_base_prompt}"
        print("[STEP] Filling hardcoded template wrapped prompt assembly...", flush=True)
        chat_box.first.type(prompt_text, timeout=0)
        
        page.keyboard.press("Enter")
        print("[OK] Hardcoded structural prompt execution complete.", flush=True)

        # Image generation tracking context
        share_button = None
        found_share = False
        image_downloaded_successfully = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(30, 60)

            # --------------------------------------------------------
            # STRATEGY A: Naya 'Skip' Button Aur Text-Filter Logic
            # --------------------------------------------------------
            try:
                skip_button = page.get_by_role('button', name='Skip')
                if skip_button.first.is_visible():
                    print("[INFO] 'Skip' button detected! Clicking 'Skip' button...", flush=True)
                    skip_button.first.click()
                    
                    # Skip par click karne ke baad 30-60 seconds ka wait
                    print("[STEP] Skip clicked. Performing random wait (30-60 seconds)...", flush=True)
                    custom_random_wait(30, 60)
                    
                    # Ab check karein ki kya dono options available hain
                    option_1 = page.locator('div').filter(has_text=re.compile(r'^1Image 1Image 1 is better$')).get_by_label('')
                    option_2 = page.locator('div').filter(has_text=re.compile(r'^2Image 2Image 2 is better$')).get_by_label('')
                    
                    if option_1.first.is_visible() or option_2.first.is_visible():
                        print("[INFO] Strategy A options are still available. Selecting one randomly...", flush=True)
                        chosen_option = random.choice([option_1, option_2])
                        
                        if chosen_option.first.is_visible():
                            chosen_option.first.click()
                            print("[STEP] Preference selected. Performing another random wait (30-60 seconds)...", flush=True)
                            custom_random_wait(30, 60)
                    else:
                        print("[INFO] Strategy A options are not available after Skip. Proceeding to normal download...", flush=True)
            except Exception as preference_err:
                print(f"[INFO] Strategy A (Skip Button) exception: {preference_err}", flush=True)
            
            # --------------------------------------------------------
            # STRATEGY B: Purana Test-ID Based Feedback Logic (FALLBACK)
            # --------------------------------------------------------
            try:
                feedback_buttons = page.get_by_test_id('paragen-prefer-response-button')
                if feedback_buttons.first.is_visible():
                    count = feedback_buttons.count()
                    print(f"[INFO] Fallback Active! Found {count} test-id preference buttons.", flush=True)
                    chosen_index = random.choice([0, 1]) if count >= 2 else 0
                    print(f"[STEP] Selecting response index via test-id: {chosen_index}", flush=True)
                    feedback_buttons.nth(chosen_index).click()
                    custom_random_wait(15, 30)
            except Exception as old_feedback_err:
                print(f"[INFO] Strategy B (Test-ID Fallback) exception: {old_feedback_err}", flush=True)

            # --------------------------------------------------------
            # Main Download Workflow (Share button detection)
            # --------------------------------------------------------
            try:
                locator = page.get_by_role('button', name='Share this image').first
                if locator.is_visible():
                    share_button = locator
                    found_share = True
                    print("✅ 'Share this image' button located successfully!", flush=True)
                    break
            except Exception as loc_err:
                print(f"[INFO] Share locator exception: {loc_err}", flush=True)

            if not found_share and attempt == 5:
                print("[INFO] 5 retries complete ho gaye. Image nahi mili, chat_box mein 'Continue Generating Prompt' bhej rahe hain...", flush=True)
                try:
                    chat_box = page.get_by_role('textbox', name='Chat with ChatGPT').or_(page.get_by_role('textbox', name='Ask ChatGPT'))
                    if chat_box.count() == 0:
                        chat_box = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Describe or edit an image')).first
                    if chat_box.count() == 0:
                        chat_box = page.locator('#prompt-textarea')

                    if chat_box.count() > 0:
                        chat_box.first.click()
                        chat_box.first.type("Please continue generating the image")
                        custom_random_wait(3, 5)
                        page.keyboard.press("Enter")
                        print("[OK] 'Continue Generating Prompt' bhej diya gaya hai. Agle 5 retries ka wait kar rahe hain...", flush=True)
                except Exception as q_err:
                    print(f"[WARNING] 'Continue Generating Prompt' bhejte waqt error aaya: {q_err}", flush=True)
            
            print(f"[WARNING] Share button not visible on attempt {attempt}. Retrying...", flush=True)
            loading_locator = page.get_by_test_id('image-gen-loading-state').locator('div').first
            if loading_locator.is_visible():
                print("[INFO] Image still loading!!!", flush=True)

        if not found_share or not share_button:
            print("❌ Error: 'Share this image' button not found after 5 retries. Exiting program.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    # Playwright full page screenshot
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    upload_to_onlyfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            sys.exit(1)

        # ========================================================
        # PROCESSING STRATEGY 1: DIRECT DOWNLOAD
        # ========================================================
        print("[STEP] Checking if direct 'Download' button is available on main page...", flush=True)
        direct_download_btn = page.get_by_role('button', name='Download').first
        
        if direct_download_btn.is_visible():
            print("✅ Direct 'Download' button found! Initiating direct download...", flush=True)
            try:
                with page.expect_download(timeout=60000) as download_info:
                    direct_download_btn.click()
                
                download = download_info.value
                local_filename = IMAGE_DIR / "pin.png"
                download.save_as(local_filename)
                print(f"✅ Original resolution image downloaded directly: {local_filename}", flush=True)
                image_downloaded_successfully = True
                
            except Exception as direct_dl_err:
                print(f"[WARNING] Direct download triggered error, falling back: {direct_dl_err}", flush=True)

        # ========================================================
        # PROCESSING STRATEGY 2: FALLBACK 1 CONTAINER EXTRACTION
        # ========================================================
        if not image_downloaded_successfully:
            print("[STEP] Executing Fallback 1: Searching for Generated image container...", flush=True)
            try:
                generated_image_btn = page.get_by_role('button', name=re.compile(r'Generated image:.*', re.IGNORECASE)).first
                if generated_image_btn.is_visible():
                    print("✅ Generated image area located via regex. Extracting inner image element...", flush=True)
                    img_element = generated_image_btn.locator('img').first
                    img_src = img_element.get_attribute('src')
                    
                    if img_src:
                        local_filename = IMAGE_DIR / "pin.png"
                        if img_src.startswith('blob:'):
                            print("[INFO] Blob URL detected. Extracting image data natively...", flush=True)
                            base64_data = page.evaluate("""async (url) => {
                                const response = await fetch(url);
                                const blob = await response.blob();
                                return new Promise((resolve) => {
                                    const reader = new FileReader();
                                    reader.onloadend = () => resolve(reader.result.split(',')[1]);
                                    reader.readAsDataURL(blob);
                                });
                            }""", img_src)
                            with open(local_filename, "wb") as fh:
                                fh.write(base64.b64decode(base64_data))
                        else:
                            print(f"[INFO] Standard image URL detected. Streaming source context...", flush=True)
                            img_response = page.request.get(img_src)
                            with open(local_filename, "wb") as fh:
                                fh.write(img_response.body())
                                
                        print(f"✅ Original dimensions image successfully saved via Fallback 1: {local_filename}", flush=True)
                        image_downloaded_successfully = True
                    else:
                        print("[WARNING] Image element found but 'src' attribute was empty.", flush=True)
            except Exception as fallback_one_err:
                print(f"[WARNING] Fallback 1 extraction method failed: {fallback_one_err}", flush=True)

        # ========================================================
        # PROCESSING STRATEGY 3: FALLBACK 2 NEW TAB/SHARE LINK
        # ========================================================
        if not image_downloaded_successfully:
            print("[INFO] Moving forward with Fallback 2 workflow (New Tab / Share Link Method)...", flush=True)
            page.evaluate("() => navigator.clipboard.writeText('')")
            print("[STEP] Clicking 'Share this image' button...", flush=True)
            share_button.click()
            custom_random_wait(15, 30)

            try:
                popup_download_btn = page.get_by_role('button', name='Download').first
                if popup_download_btn.is_visible():
                    print("✅ 'Download' button found inside the Copy Link pop-up!", flush=True)
                    with page.expect_download(timeout=60000) as download_info:
                        popup_download_btn.click()
                    
                    download = download_info.value
                    local_filename = IMAGE_DIR / "pin.png"
                    download.save_as(local_filename)
                    print(f"✅ Image downloaded from pop-up successfully: {local_filename}", flush=True)
                    image_downloaded_successfully = True
            except Exception as popup_dl_err:
                print(f"[INFO] Pop-up direct download failed or not found: {popup_dl_err}", flush=True)

            if not image_downloaded_successfully:
                try:
                    copy_link_btn = page.get_by_role('button', name='Copy link').first
                    if copy_link_btn.is_visible():
                        print("[INFO] 'Copy link' pop-up detected. Clicking it explicitly...", flush=True)
                        copy_link_btn.click()
                        time.sleep(2)
                except Exception:
                    print("[INFO] No pop-up button found, continuing with direct copy...", flush=True)

                public_shared_url = page.evaluate("() => navigator.clipboard.readText()")
                print(f"\n[COPIED URL] Shared Link Extracted: {public_shared_url}\n", flush=True)

                if public_shared_url and "chatgpt.com/s/" in public_shared_url:
                    print("[STEP] Opening new tab for public shared link...", flush=True)
                    shared_page = context.new_page()
                    shared_page.goto(public_shared_url, wait_until="domcontentloaded")
                    custom_random_wait(30, 60)
                    
                    try:
                        save_btn = shared_page.get_by_role('button', name='Save').first.or_(shared_page.get_by_role('button', name='Save'))
                        with shared_page.expect_download(timeout=60000) as download_info:
                            print("[STEP] Clicking 'Save' button...", flush=True)
                            save_btn.click()
                        
                        download = download_info.value
                        local_filename = IMAGE_DIR / "pin.png"
                        download.save_as(local_filename)
                        print(f"✅ High quality image downloaded successfully via share link tab: {local_filename}", flush=True)
                        image_downloaded_successfully = True
                    except Exception as download_err:
                        print(f"❌ Error during 'Save' button download processing: {download_err}", flush=True)
                        if 'page' in locals() and page:
                            try:
                                screenshot_path = "error_screenshot.png"
                                # Playwright full page screenshot
                                page.screenshot(path=screenshot_path, full_page=True)
                                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                                
                                upload_to_onlyfiles(screenshot_path)
                            except Exception as screenshot_err:
                                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
                        sys.exit(1)
                    finally:
                        shared_page.close()
                else:
                    print("[ERROR] Extracted clipboard content is not a valid ChatGPT shared page link URL.", flush=True)

        # ========================================================
        # FIXED: UPDATE STATE IN JSON (PRESERVING EXISTING KEYS)
        # ========================================================
        if image_downloaded_successfully:
            print("[STEP] Updating execution status inside JSON state schema...", flush=True)
            
            # Using .update() to safely update ONLY the image_generated field
            ideas_list[target_index].update({
                "image_generated": True
            })

            # Modifying updates persistent across calls
            with ideas_file.open("w", encoding="utf-8") as f:
                json.dump(ideas_list, f, indent=2, ensure_ascii=False)
            
            print(f"✅ Success log saved into JSON for index {target_index}: '{subject_matter}' marked as image_generated=True.", flush=True)
        else:
            print("❌ Image pipeline terminated without confirming output save down.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    # Playwright full page screenshot
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    upload_to_onlyfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            sys.exit(1)

        print("[STEP] Performing final random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    # Playwright full page screenshot
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    upload_to_onlyfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()