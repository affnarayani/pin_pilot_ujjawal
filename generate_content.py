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

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Valid Pinterest Board options
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

    # ========================================================
    # LOAD PINTEREST IDEAS & PICK DYNAMIC TOPIC
    # ========================================================
    ideas_file = Path("pinterest_ideas.json")
    if not ideas_file.exists():
        print("[ERROR] pinterest_ideas.json nahi mila. Exiting...", flush=True)
        sys.exit(1)

    try:
        with ideas_file.open("r", encoding="utf-8") as f:
            ideas_list = json.load(f)
    except Exception as e:
        print(f"[ERROR] pinterest_ideas.json parse nahi ho paya: {e}", flush=True)
        sys.exit(1)

    # ========================================================
    # PIPELINE INTEGRITY LOCK VERIFICATION
    # ========================================================
    # Agar kisi bhi pichle processed item ki teeno keys me se koi ek bhi False hai,
    # toh iska matlab uska image gen ya posting pending hai. Naya task run nahi hoga.
    for item in ideas_list:
        if isinstance(item, dict):
            content_gen = item.get("content_generated", False)
            image_gen = item.get("image_generated", False)
            posted_state = item.get("posted", False)
            
            # Checking if any of them is False (Pipeline unfinished status trigger)
            if not content_gen or not image_gen or not posted_state:
                print("[INFO] Last Pipeline Is Not Yet Finished. Exiting safely.", flush=True)
                sys.exit(0)

    # File init/clear at the beginning
    article_file = Path("article.json")
    with article_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'article.json' cleared/initialized", flush=True)

    subject_matter = None
    target_index = -1

    # Linear top-down scanning extraction matching clean unassigned entries
    for index, item in enumerate(ideas_list):
        if isinstance(item, str):
            subject_matter = item
            target_index = index
            break
        elif isinstance(item, dict):
            # Enforce checking that neither tracking structure exists
            if "content_generated" not in item and "posted" not in item and "generated" not in item:
                subject_matter = item.get("title") or item.get("subject") or list(item.values())[0]
                target_index = index
                break

    if subject_matter is None or target_index == -1:
        print("[INFO] No ungenerated or unposted subject matter found inside array. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Dynamic Target Extracted: '{subject_matter}' at array index [{target_index}]", flush=True)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & LOGIN
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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto("https://chatgpt.com/?temporary-chat=true", wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        # 30 to 60 seconds random wait after page load
        custom_random_wait(30, 60)

        # Verification of active authenticated status
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0])
        
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        continue_button = page.get_by_role("button", name="Continue")
        if continue_button.is_visible():
            continue_button.click()
            custom_random_wait(6, 12)
        # =========================
        # AUTOMATION FLOW
        # =========================
        print("[STEP] Locating chat textbox...", flush=True)
        
        # Fallback Strategy for Textbox Locators
        textbox = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        if textbox.count() == 0:
            print("[INFO] Fallback 1: Searching for 'Ask anything' paragraph inside textbox context...", flush=True)
            textbox = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Ask anything')).first
            
        if textbox.count() == 0:
            print("[INFO] Fallback 2: Searching via CSS Selector '#prompt-textarea'...", flush=True)
            textbox = page.locator('#prompt-textarea')

        # Trigger action if found
        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
            
        custom_random_wait(15, 30)

        # Construction of algorithmic contextual optimization prompt blueprint
        prompt = (
            f"IMPORTANT:\n"
            f"Return ONLY ONE valid JSON object wrapped inside a single ```json code block```.\n"
            f"Do NOT output explanations, markdown, comments, notes or conversational text outside the JSON block.\n\n"

            f"ROLE\n"
            f"You are an elite Pinterest SEO Strategist, Pinterest Trend Analyst, Consumer Psychologist and Direct Response Copywriter.\n\n"

            f"PRIMARY OBJECTIVE\n"
            f"Generate Pinterest metadata that maximizes:\n"
            f"- Pinterest Search Visibility\n"
            f"- Search Ranking\n"
            f"- Click Through Rate (CTR)\n"
            f"- Saves\n"
            f"- Outbound Clicks\n"
            f"- User Engagement\n"
            f"- Human Readability\n\n"

            f"INPUT TOPIC\n"
            f"{subject_matter}\n\n"

            f"========================================\n"
            f"INTERNAL ANALYSIS (DO NOT OUTPUT)\n"
            f"========================================\n"

            f"Before writing anything, silently determine:\n"
            f"• Primary Pinterest Search Intent\n"
            f"• Target Audience Intent\n"
            f"• Primary Keyword\n"
            f"• 3-6 Secondary Pinterest Keywords\n"
            f"• Semantic Keyword Variations\n"
            f"• User Pain Point\n"
            f"• Desired Transformation\n"
            f"• Emotional Trigger\n"
            f"• Curiosity Trigger\n"
            f"• Best Content Angle\n"
            f"• Best Hook Style\n"
            f"• Best CTA Style\n"
            f"• Reader Awareness Stage\n"
            f"• Best Matching Board\n\n"

            f"Never output this reasoning.\n\n"

            f"========================================\n"
            f"TITLE\n"
            f"========================================\n"

            f"Requirements:\n"
            f"- Target length: 60-85 characters.\n"
            f"- Never exceed 95 characters.\n"
            f"- Naturally include the complete input topic exactly once.\n"
            f"- You MAY add words before and/or after the topic.\n"
            f"- Front-load the strongest searchable keyword whenever natural.\n"
            f"- Optimize for Pinterest Search first.\n"
            f"- Optimize for CTR second.\n"
            f"- Make users curious.\n"
            f"- Promise a clear benefit.\n"
            f"- Human sounding only.\n"
            f"- Avoid clickbait.\n"
            f"- Avoid generic AI phrases.\n"
            f"- No emojis.\n"
            f"- No hashtags.\n"
            f"- No quotation marks.\n\n"

            f"========================================\n"
            f"DESCRIPTION\n"
            f"========================================\n"

            f"Requirements:\n"
            f"- Target length: 350-600 characters.\n"
            f"- Never exceed 700 characters.\n"
            f"- First sentence must immediately communicate value.\n"
            f"- Use a compelling hook.\n"
            f"- Naturally reinforce the primary keyword.\n"
            f"- Naturally include several semantic Pinterest search keywords.\n"
            f"- Avoid keyword stuffing.\n"
            f"- Avoid repeating identical phrases.\n"
            f"- Use synonyms naturally.\n"
            f"- Address a real user pain point.\n"
            f"- Explain the practical benefit.\n"
            f"- Build curiosity.\n"
            f"- Make the content feel save-worthy.\n"
            f"- End with ONE natural CTA.\n"
            f"- CTA examples:\n"
            f"  • Save this pin for later.\n"
            f"  • Explore the complete guide.\n"
            f"  • Learn the full framework.\n"
            f"  • Read the complete method.\n"
            f"  • Discover the complete system.\n"
            f"- Educational tone preferred over promotional tone.\n"
            f"- Never sound spammy.\n"
            f"- No emojis.\n"
            f"- Hashtags are optional. Use at most 3 only if they genuinely improve discoverability.\n\n"

            f"========================================\n"
            f"ALT TEXT\n"
            f"========================================\n"

            f"Requirements:\n"
            f"- Target length: 120-250 characters.\n"
            f"- Never exceed 350 characters.\n"
            f"- Describe the infographic for accessibility.\n"
            f"- Mention the main topic naturally.\n"
            f"- Mention important visual information.\n"
            f"- Naturally include the primary keyword once.\n"
            f"- Do NOT copy the title.\n"
            f"- Do NOT paraphrase the description.\n"
            f"- Human language only.\n"
            f"- No hashtags.\n"
            f"- No emojis.\n\n"

            f"========================================\n"
            f"HOOK STRATEGY\n"
            f"========================================\n"

            f"Internally choose ONLY ONE hook style:\n"
            f"- Problem\n"
            f"- Curiosity\n"
            f"- Mistake\n"
            f"- Checklist\n"
            f"- Framework\n"
            f"- Step-by-Step\n"
            f"- Secret\n"
            f"- Myth vs Fact\n"
            f"- Transformation\n"
            f"- Warning\n"
            f"- Question\n"
            f"- Habit\n"
            f"- Beginner Guide\n\n"

            f"========================================\n"
            f"BOARD\n"
            f"========================================\n"

            f"Choose EXACTLY ONE:\n"
            f"- Anxiety & Mental Peace\n"
            f"- Calm Mind Habits\n"
            f"- Focus & Mental Discipline\n"
            f"- Mental Clarity\n"
            f"- Overthinking Help\n"
            f"- Self-Improvement Psychology\n\n"

            f"========================================\n"
            f"DIVERSITY RULES\n"
            f"========================================\n"

            f"- Never use repetitive sentence structures.\n"
            f"- Vary sentence openings naturally.\n"
            f"- Avoid AI sounding transitions.\n"
            f"- Avoid repetitive adjectives.\n"
            f"- Produce unique wording even for related topics.\n\n"

            f"========================================\n"
            f"NEGATIVE RULES\n"
            f"========================================\n"

            f"Never use phrases such as:\n"
            f"unlock your potential\n"
            f"game changer\n"
            f"transform your life\n"
            f"revolutionary\n"
            f"next level\n"
            f"dive into\n"
            f"unleash\n"
            f"boost your life instantly\n"
            f"life-changing secret\n\n"

            f"Never:\n"
            f"- Keyword stuff.\n"
            f"- Repeat identical phrases.\n"
            f"- Sound robotic.\n"
            f"- Write generic marketing copy.\n"
            f"- Invent facts.\n"
            f"- Invent statistics.\n"
            f"- Make medical claims.\n\n"

            f"========================================\n"
            f"FINAL SELF REVIEW (DO NOT OUTPUT)\n"
            f"========================================\n"

            f"Before returning JSON, internally score your output from 1-10 for:\n"
            f"- Pinterest SEO\n"
            f"- CTR\n"
            f"- Human Readability\n"
            f"- Search Intent Match\n"
            f"- Semantic Diversity\n"
            f"- Accessibility\n\n"

            f"If any category scores below 9/10, improve the output before returning it.\n\n"

            f"========================================\n"
            f"OUTPUT FORMAT\n"
            f"========================================\n"

            f'{{\n'
            f'  "title": "...",\n'
            f'  "description": "...",\n'
            f'  "alt_text": "...",\n'
            f'  "selected_board": "..."\n'
            f'}}'
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        # Initial processing wait allocation
        custom_random_wait(30, 60)

        # ============================================
        # STABLE 15-SECOND POLLING LIVE STREAM CHECK
        # ============================================
        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        code_block_locator = page.locator('#code-block-viewer pre')
        
        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)
            
            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing live text size variations...", flush=True)
                
                last_length = 0
                max_check_cycles = 15  # 15 cycles * 15 seconds = ~3.7 minutes maximum
                
                for cycle in range(max_check_cycles):
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    if current_length > 0 and current_length == last_length:
                        if current_text.endswith("}"):
                            json_content = current_text
                            print("[OK] Content generation is fully finished and finalized.", flush=True)
                            break
                        else:
                            print("[WARNING] Text generation paused but JSON closing bracket '}' is missing. Waiting...", flush=True)
                        
                    last_length = current_length
                
                if json_content:
                    break
            
            if attempt < 5:
                print(f"[WARNING] Code block completely write nahi hua. Retrying window...", flush=True)
                custom_random_wait(30, 60)
            else:
                print("❌ Max retries reached. Streaming complete nahi ho payi. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        # JSON parsing, validation and State Array Modification 
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                
                # Strict structural data synchronization constraints enforcement
                # parsed_json["title"] = subject_matter
                # parsed_json["alt_text"] = subject_matter
                
                # Fallback safeguard validation structure against broken board names
                if parsed_json.get("selected_board") not in ALLOWED_BOARDS:
                    print(f"[WARNING] Invalid board string parsed: '{parsed_json.get('selected_board')}'. Appending arbitrary safe index variant.", flush=True)
                    parsed_json["selected_board"] = random.choice(ALLOWED_BOARDS)

                print("[STEP] Saving formatted data inside article.json...", flush=True)
                with article_file.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print("[OK] Data structural dump serialization write-out successful.", flush=True)
                
                # ========================================================
                # UPDATE STATE LOCK TRACKER WITH 3 KEY VALUES
                # ========================================================
                print("[STEP] Saving updated item state back to pinterest_ideas.json...", flush=True)
                ideas_list[target_index] = {
                    "title": subject_matter,
                    "content_generated": True,
                    "image_generated": False,
                    "posted": False
                }

                with ideas_file.open("w", encoding="utf-8") as f:
                    json.dump(ideas_list, f, indent=2, ensure_ascii=False)
                print(f"✅ Success: '{subject_matter}' registered with content_generated=True, image_generated=False, posted=False.", flush=True)
                
            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse karne me fail hua: {je}. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skip kiya gaya kyunki koi data fetch nahi hua. Exiting script...", flush=True)
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
        if browser:
            try:
                browser.close()
            except:
                pass
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