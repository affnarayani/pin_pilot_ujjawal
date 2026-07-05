import os
import sys
import json
import time
import base64
import random
import re
from datetime import datetime
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
    # LOAD PINTEREST IDEAS & SIMPLE STRICT PIPELINE CHECK
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

    # Find the last dictionary in the list to check if its pipeline is complete
    last_dict_item = None
    for item in ideas_list:
        if isinstance(item, dict):
            last_dict_item = item

    # SIMPLE RULE CHECK: Agar koi processed item pehle se maujood hai
    if last_dict_item is not None:
        url_val = str(last_dict_item.get("url") or "").strip()
        
        # Check if ALL 6 flags are strictly True and URL is not empty
        is_pipeline_complete = (
            last_dict_item.get("post_generated") is True and
            last_dict_item.get("post_image_generated") is True and
            last_dict_item.get("post_posted") is True and
            last_dict_item.get("content_generated") is True and
            last_dict_item.get("image_generated") is True and
            last_dict_item.get("posted") is True and
            url_val != ""
        )
        
        # Agar last pipeline complete NAHI hai, toh program aage nahi chalega. Exit safely.
        if not is_pipeline_complete:
            print("[INFO] Last pipeline is not fully complete yet (Flags are false or URL is missing). Exiting safely.", flush=True)
            sys.exit(0)

    # Agar pipeline complete hai (ya list me koi dict hai hi nahi), toh next plain string topic dhundo
    subject_matter = None
    target_index = -1

    for index, item in enumerate(ideas_list):
        if isinstance(item, str):  # Plain string topic
            subject_matter = item
            target_index = index
            break

    if subject_matter is None or target_index == -1:
        print("[INFO] No new plain string topics available to process. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Last pipeline verified complete. Starting next topic: '{subject_matter}' at array index [{target_index}]", flush=True)

    # Folders and file management for Markdown Post output
    post_dir = Path("posts")
    post_dir.mkdir(exist_ok=True)
    post_file = post_dir / "post.md"
    
    with post_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'posts/post.md' cleared/initialized", flush=True)

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

        current_date_str = datetime.now().strftime("%Y-%m-%d")

        # Algorithmic Prompt Design for full markdown post generation
        prompt = (
            f"IMPORTANT:\n"
            f"Return ONLY ONE complete comprehensive article wrapped inside a single ```markdown code block```.\n"
            f"Do NOT output explanations, conversational introduction, greetings, or notes outside the markdown block.\n\n"

            f"ROLE\n"
            f"You are a professional Content Creator, Mental Health Blogger, and SEO Copywriter specializing in psychology and mindfulness.\n\n"

            f"PRIMARY OBJECTIVE\n"
            f"Generate a deep, engaging, and comprehensive blog post based on the target title below. "
            f"The body length must be exactly between 1000 to 1500 words to ensure thorough exploration.\n\n"

            f"INPUT TOPIC TITLE\n"
            f"'{subject_matter}'\n\n"

            f"METADATA FRONTMATTER REQUIREMENTS\n"
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
            f"- Word Count: Strictly 1000 to 1500 words.\n"
            f"- Structure: Do not follow a rigid hardcoded pattern. Create a natural flowing progression with diverse section headers (H2, H3), deeply explanatory paragraphs, logical sub-points, and clear structural lists to make it highly informative and shareable.\n"
            f"- Tone: Compassionate, empathetic, evidence-based, clear, and highly engaging for readers seeking self-improvement.\n"
            f"- Negative Constraints: Avoid generic AI-sounding opening hooks, robotic transitions, or repeating the same terms across consecutive sections. Do NOT include any comparison tables.\n"
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
        print("[STEP] Waiting for generated markdown code block to complete writing (15s checks)...", flush=True)
        code_block_locator = page.locator('#code-block-viewer pre')
        
        markdown_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)
            
            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing live text size variations...", flush=True)
                
                last_length = 0
                max_check_cycles = 25  
                
                for cycle in range(max_check_cycles):
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    if current_length > 0 and current_length == last_length:
                        markdown_content = current_text
                        print("[OK] Markdown post generation is fully finished and finalized.", flush=True)
                        break
                        
                    last_length = current_length
                
                if markdown_content:
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

        # Markdown cleaning and output file stream modification
        if markdown_content:
            print("[STEP] Formatting content data...", flush=True)
            if markdown_content.startswith("```markdown"):
                markdown_content = markdown_content.split("```markdown", 1)[1]
            elif markdown_content.startswith("```"):
                markdown_content = markdown_content.split("```", 1)[1]
                
            if markdown_content.endswith("```"):
                markdown_content = markdown_content.rsplit("```", 1)[0]
            
            clean_output = markdown_content.strip()

            print("[STEP] Saving formatted data inside posts/post.md...", flush=True)
            with post_file.open("w", encoding="utf-8") as f:
                f.write(clean_output)
            print("[OK] Markdown file synchronization write-out successful.", flush=True)
            
            # ========================================================
            # SAVE AND CONVERT TO INITIALIZED PIPELINE STRUCTURE
            # ========================================================
            print("[STEP] Saving changes back to pinterest_ideas.json...", flush=True)
            
            ideas_list[target_index] = {
                "title": subject_matter,
                "post_generated": True,
                "post_image_generated": False,
                "post_posted": False,
                "url": "",
                "content_generated": False,
                "image_generated": False,
                "posted": False
            }

            with ideas_file.open("w", encoding="utf-8") as f:
                json.dump(ideas_list, f, indent=2, ensure_ascii=False)
            print(f"✅ Success: New topic converted to initialized dict successfully.", flush=True)
                
        else:
            print("[ERROR] Save skip kiya gaya kyunki koi content data fetch nahi hua. Exiting script...", flush=True)
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