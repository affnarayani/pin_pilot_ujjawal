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

# Target directory changed to post_image
IMAGE_DIR = Path("post_image")
IMAGE_DIR.mkdir(exist_ok=True)

PBKDF2_ITERATIONS = 200_000
MAX_RETRIES = 5  

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


# =========================
# MAIN
# =========================
def run():
    ideas_file = Path("pinterest_ideas.json")
    
    if not ideas_file.exists():
        raise RuntimeError("❌ 'pinterest_ideas.json' file not found!")

    print("[STEP] Loading Pinterest ideas JSON...", flush=True)
    with ideas_file.open("r", encoding="utf-8") as f:
        ideas_list = json.load(f)

    subject_matter = None
    target_index = -1

    for index, item in enumerate(ideas_list):
        if isinstance(item, dict):
            if (item.get("post_generated") is True and 
                item.get("post_image_generated") is False):
                
                subject_matter = item.get("title") or item.get("subject") or list(item.values())[0]
                target_index = index
                break

    if subject_matter is None or target_index == -1:
        print("[INFO] Blog Post Content Not Generated or Image Already Created! Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Selected Target Subject Matter: '{subject_matter}' at index [{target_index}]", flush=True)

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

        base_prompt = """
        You are an elite Digital Editorial Creative Director, Consumer Psychologist, and AI Image Prompt Engineer specializing in high-conversion web typography and premium blogging aesthetics.

        Your task is to create ONE highly detailed image-generation prompt for a blog post horizontal hero image.

        ==================================================
        TOPIC / SUBJECT MATTER
        ==================================================

        {subject_matter}

        ==================================================
        PRIMARY OBJECTIVE
        ==================================================

        Design an authoritative, cinematic, and premium horizontal blog hero header that instantly establishes credibility, sets the emotional tone of the article, and captures attention on desktop and mobile screens.

        The final image must look like a custom high-end digital publication asset or professional editorial photography, completely free of generic AI-art cliches or awkward composition.

        ==================================================
        IMPORTANT
        ==================================================

        Generate ONLY the final image-generation prompt.

        Do NOT explain your reasoning.

        Do NOT output design notes.

        Do NOT output markdown.

        ==================================================
        IMAGE FORMAT & LAYOUT
        ==================================================

        Blog Post Hero Image / Website Header

        Aspect Ratio:
        16:9

        High-resolution (Landscape)

        Balanced composition with strong visual focal points that fit perfectly across standard modern article layouts without awkward text cropping.

        ==================================================
        VISUAL STYLE & COMPOSITION
        ==================================================

        Premium Digital Editorial. Think Medium publication headers, New York Times corporate essays, or high-end self-improvement platforms.

        • Use clean metaphorical concept art or modern architectural/lifestyle vector minimalism blended with subtle cinematic realism.
        • Soft, sophisticated lighting with intentional depth of field (blurred background elements where necessary to keep the focus clear).
        • Rich textures and highly polished details that feel human-designed.
        • Incorporate ample negative space strategically on one side (left or right) to balance the frame beautifully.

        ==================================================
        COLOR PALETTE
        ==================================================

        Modern self-improvement and intellectual aesthetic.

        • Soft neutral base tones (e.g., warm cream, deep charcoal, muted olive, or sophisticated beige).
        • A single, calculated accent color to drive psychological engagement based on the topic.
        • Elegant, balanced contrast. Avoid oversaturated, neon, neon-gradients, or cheap stock-photo color schemes.

        ==================================================
        NEGATIVE REQUIREMENTS
        ==================================================

        Do NOT include:
        • Cluttered collage layouts or crowded elements.
        • Cheap vector icon kits or generic stock-photo poses.
        • Messy, unreadable AI-generated gibberish text strings inside the illustration itself.
        • Dark, depressing colors unless explicitly needed for contrast.
        • Corny, exaggerated facial expressions or cartoonish proportions.

        Generate ONLY the final image-generation prompt.
        """

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto("https://chatgpt.com/", wait_until="load")
        print("[OK] URL opened", flush=True)

        print("[STEP] Performing initial random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0])
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        if page.get_by_role('button', name='Create an image').is_visible():
            page.get_by_role('button', name='Create an image').click()
            print("[STEP] Create an image button clicked!...", flush=True)
            custom_random_wait(6, 12)
            
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = page.get_by_role('textbox', name='Chat with ChatGPT')
        if chat_box.count() == 0:
            chat_box = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Describe or edit an image')).first
        if chat_box.count() == 0:
            chat_box = page.locator('#prompt-textarea')

        if chat_box.count() > 0:
            chat_box.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
        
        formatted_base = base_prompt.format(subject_matter=subject_matter)
        clean_base_prompt = " ".join(formatted_base.split())
        
        prompt_text = f"Generate a high-quality ultra-detailed image with an aspect ratio of strictly 16:9, depicting the following scene: {clean_base_prompt}"
        print("[STEP] Filling hardcoded template wrapped prompt assembly...", flush=True)
        chat_box.first.type(prompt_text, timeout=0)
        
        page.keyboard.press("Enter")
        print("[OK] Hardcoded structural prompt execution complete.", flush=True)

        share_button = None
        found_share = False
        image_downloaded_successfully = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(30, 60)

            # STRATEGY A: Naya 'Skip' Button Aur Text-Filter Logic
            try:
                skip_button = page.get_by_role('button', name='Skip')
                if skip_button.first.is_visible():
                    print("[INFO] 'Skip' button detected! Clicking 'Skip' button...", flush=True)
                    skip_button.first.click()
                    
                    print("[STEP] Skip clicked. Performing random wait (30-60 seconds)...", flush=True)
                    custom_random_wait(30, 60)
                    
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
            
            # STRATEGY B: Purana Test-ID Based Feedback Logic (FALLBACK)
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

            # Main Download Workflow (Share button detection)
            try:
                locator = page.get_by_role('button', name='Share this image').first
                if locator.is_visible():
                    share_button = locator
                    found_share = True
                    print("✅ 'Share this image' button located successfully!", flush=True)
                    break
            except Exception as loc_err:
                print(f"[INFO] Share locator exception: {loc_err}", flush=True)
            
            print(f"[WARNING] Share button not visible on attempt {attempt}. Retrying...", flush=True)
            loading_locator = page.get_by_test_id('image-gen-loading-state').locator('div').first
            if loading_locator.is_visible():
                print("[INFO] Image still loading!!!", flush=True)

        if not found_share or not share_button:
            print("❌ Error: 'Share this image' button not found after 5 retries. Exiting program.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    imgbb_key = os.getenv("IMGBBB_API_KEY")
                    if imgbb_key:
                        print("[OK] Uploading screenshot to ImgBB...", flush=True)
                        url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                        
                        with open(screenshot_path, "rb") as file:
                            response = requests.post(url, files={"image": file})
                        
                        if response.status_code == 200:
                            res_data = response.json()
                            direct_url = res_data["data"]["display_url"]
                            print("\n" + "="*50, flush=True)
                            print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                            print("="*50 + "\n", flush=True)
                        else:
                            print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                    else:
                        print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
                except Exception as screenshot_err:
                    print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
            sys.exit(1)

        # STRATEGY 1: DIRECT DOWNLOAD
        print("[STEP] Checking if direct 'Download' button is available on main page...", flush=True)
        direct_download_btn = page.get_by_role('button', name='Download').first
        
        if direct_download_btn.is_visible():
            print("✅ Direct 'Download' button found! Initiating direct download...", flush=True)
            try:
                with page.expect_download(timeout=60000) as download_info:
                    direct_download_btn.click()
                
                download = download_info.value
                local_filename = IMAGE_DIR / "post.png"
                download.save_as(local_filename)
                print(f"✅ Original resolution image downloaded directly: {local_filename}", flush=True)
                image_downloaded_successfully = True
                
            except Exception as direct_dl_err:
                print(f"[WARNING] Direct download triggered error, falling back: {direct_dl_err}", flush=True)

        # STRATEGY 2: FALLBACK 1 CONTAINER EXTRACTION
        if not image_downloaded_successfully:
            print("[STEP] Executing Fallback 1: Searching for Generated image container...", flush=True)
            try:
                generated_image_btn = page.get_by_role('button', name=re.compile(r'Generated image:.*', re.IGNORECASE)).first
                if generated_image_btn.is_visible():
                    print("✅ Generated image area located via regex. Extracting inner image element...", flush=True)
                    img_element = generated_image_btn.locator('img').first
                    img_src = img_element.get_attribute('src')
                    
                    if img_src:
                        local_filename = IMAGE_DIR / "post.png"
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

        # STRATEGY 3: FALLBACK 2 NEW TAB/SHARE LINK
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
                    local_filename = IMAGE_DIR / "post.png"
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
                        local_filename = IMAGE_DIR / "post.png"
                        download.save_as(local_filename)
                        print(f"✅ High quality image downloaded successfully via share link tab: {local_filename}", flush=True)
                        image_downloaded_successfully = True
                    except Exception as download_err:
                        print(f"❌ Error during 'Save' button download processing: {download_err}", flush=True)
                        if 'page' in locals() and page:
                            try:
                                screenshot_path = "error_screenshot.png"
                                page.screenshot(path=screenshot_path, full_page=True)
                                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                                
                                imgbb_key = os.getenv("IMGBBB_API_KEY")
                                if imgbb_key:
                                    print("[OK] Uploading screenshot to ImgBB...", flush=True)
                                    url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                                    
                                    with open(screenshot_path, "rb") as file:
                                        response = requests.post(url, files={"image": file})
                                    
                                    if response.status_code == 200:
                                        res_data = response.json()
                                        direct_url = res_data["data"]["display_url"]
                                        print("\n" + "="*50, flush=True)
                                        print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                                        print("="*50 + "\n", flush=True)
                                    else:
                                        print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                                else:
                                    print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
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
            
            # Use .update() to preserve all other fields (url, content_generated, posted etc.)
            ideas_list[target_index].update({
                "post_image_generated": True
            })

            with ideas_file.open("w", encoding="utf-8") as f:
                json.dump(ideas_list, f, indent=2, ensure_ascii=False)
            
            print(f"✅ Success log saved into JSON for index {target_index}: '{subject_matter}' marked as post_image_generated=True.", flush=True)
        else:
            print("❌ Image pipeline terminated without confirming output save down.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    imgbb_key = os.getenv("IMGBBB_API_KEY")
                    if imgbb_key:
                        print("[OK] Uploading screenshot to ImgBB...", flush=True)
                        url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                        
                        with open(screenshot_path, "rb") as file:
                            response = requests.post(url, files={"image": file})
                        
                        if response.status_code == 200:
                            res_data = response.json()
                            direct_url = res_data["data"]["display_url"]
                            print("\n" + "="*50, flush=True)
                            print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                            print("="*50 + "\n", flush=True)
                        else:
                            print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                    else:
                        print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
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
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    imgbb_key = os.getenv("IMGBBB_API_KEY")
                    if imgbb_key:
                        print("[OK] Uploading screenshot to ImgBB...", flush=True)
                        url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                        
                        with open(screenshot_path, "rb") as file:
                            response = requests.post(url, files={"image": file})
                        
                        if response.status_code == 200:
                            res_data = response.json()
                            direct_url = res_data["data"]["display_url"]
                            print("\n" + "="*50, flush=True)
                            print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                            print("="*50 + "\n", flush=True)
                        else:
                            print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                    else:
                        print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
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