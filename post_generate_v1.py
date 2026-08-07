import os
import sys
import json
import time
import base64
import random
import re
import requests
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

def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None

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
            f"CRITICALLY AND STRICTLY IMPORTANT:\n"
            f"Return ONLY ONE complete comprehensive article wrapped inside a single ```markdown code block```.\n"
            f"Do NOT output explanations, conversational introduction, greetings, or notes outside the markdown block.\n\n"

            f"ROLE\n"
            f"You are a professional Content Creator, Mental Health Blogger, SEO Copywriter, and Psychology Educator specializing in evidence-based mental wellness, mindfulness, emotional wellbeing, and self-improvement.\n\n"

            f"PRIMARY OBJECTIVE\n"
            f"Generate a deep, engaging, original, and comprehensive evergreen blog post based on the target title below. "
            f"The body length must be exactly between 1000 and 1500 words to ensure thorough exploration while maintaining excellent readability. "
            f"The article should be genuinely useful, highly informative, naturally engaging, and capable of ranking well on Google for its primary search intent.\n\n"

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
                if 'page' in locals() and page:
                    try:
                        screenshot_path = "error_screenshot.png"
                        page.screenshot(path=screenshot_path, full_page=True)
                        print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                        
                        upload_to_tmpfiles(screenshot_path)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    upload_to_tmpfiles(screenshot_path)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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