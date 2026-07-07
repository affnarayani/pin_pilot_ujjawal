import os
import sys
import json
import requests
import markdown
import frontmatter
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from dotenv import load_dotenv

# .env file se variables load karna
load_dotenv()

# Environment Variables / GitHub Secrets
BLOG_ID = os.getenv("BLOG_ID")
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
TOKEN_JSON_STR = os.getenv("TOKEN_JSON_STR")
IMGBB_API_KEY = os.getenv("IMGBBB_API_KEY")

SCOPES = ['https://www.googleapis.com/auth/blogger']

def get_blogger_service():
    """Blogger API Authentication handle karta hai"""
    if TOKEN_JSON_STR:
        token_data = json.loads(TOKEN_JSON_STR)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    elif os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    else:
        raise ValueError("❌ No token.json found or BLOGGER_TOKEN_JSON variable is missing!")
    
    creds._client_id = CLIENT_ID
    creds._client_secret = CLIENT_SECRET

    if creds and creds.expired and creds.refresh_token:
        print("🔄 Access token expire ho chuka hai, refresh kiya ja raha hai...")
        creds.refresh(Request())

    return build('blogger', 'v3', credentials=creds)

def upload_image_to_imgbb(image_path):
    """Local Image ko ImgBB par upload karke uska direct URL return karta hai"""
    if not os.path.exists(image_path):
        print(f"⚠️ Warning: Image file '{image_path}' nahi mili!")
        return None
        
    if not IMGBB_API_KEY:
        print("⚠️ Warning: IMGBBB_API_KEY missing hai! Image upload nahi ho payegi.")
        return None

    print("📤 Image ko ImgBB par upload kiya ja raha hai...")
    url = "https://api.imgbb.com/1/upload"
    payload = {"key": IMGBB_API_KEY}
    
    with open(image_path, "rb") as image_file:
        files = {"image": image_file}
        response = requests.post(url, data=payload, files=files)
        
    if response.status_code == 200:
        data = response.json()
        image_url = data["data"]["url"]
        print(f"✅ Image successfully upload ho gayi! URL: {image_url}")
        return image_url
    else:
        print(f"❌ ImgBB Upload Failed: {response.text}")
        sys.exit(1)

def fetch_and_parse_md_file():
    """posts/post.md file ko direct utha kar metadata aur HTML content return karta hai"""
    target_file = "posts/post.md"
    
    if not os.path.exists(target_file):
        print(f"❌ Error: Required markdown file '{target_file}' nahi mili!")
        return None, None, None
        
    print(f"📄 Found markdown file: {target_file}")
    
    with open(target_file, 'r', encoding='utf-8') as f:
        post = frontmatter.load(f)
        
    title = post.get('title', 'Untitled Post')
    tags = post.get('tags', [])
    
    md_content = post.content
    html_content = markdown.markdown(md_content, extensions=['tables'])
    
    return title, html_content, tags

def create_blogger_post(title, html_content, tags=[]):
    """Blogger Par Post Create Karta Hai"""
    try:
        service = get_blogger_service()
        
        post_body = {
            "kind": "blogger#post",
            "blog": {"id": BLOG_ID},
            "title": title,
            "content": html_content,
            "labels": tags
        }
        
        request = service.posts().insert(blogId=BLOG_ID, body=post_body)
        response = request.execute()
        
        print(f"🎉 Success! Post URL: {response['url']}")
        return response
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

# --- Main Program Execution ---
if __name__ == "__main__":
    print("🚀 Blogger Dynamic Markdown Script Started...")
    
    ideas_file = Path("pinterest_ideas.json")
    if not ideas_file.exists():
        print("❌ Error: 'pinterest_ideas.json' file nahi mili!")
        sys.exit(1)
        
    # 1. JSON file load karke validation logic check karna
    with ideas_file.open("r", encoding="utf-8") as f:
        ideas_list = json.load(f)
        
    target_index = -1
    
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict):
            # Condition: post_image_generated == True aur post_posted == False
            if (item.get("post_image_generated") is True and 
                item.get("post_posted") is False):
                target_index = index
                break
                
    if target_index == -1:
        print("[INFO] No topics matching 'post_image_generated': true and 'post_posted': false. Exiting safely.")
        sys.exit(0)
        
    print(f"[OK] Found valid tracking row at index [{target_index}] in JSON.")

    # 2. Fixed md file (posts/post.md) fetch aur parse karein
    post_title, markdown_html, post_tags = fetch_and_parse_md_file()
    
    if post_title and markdown_html:
        # 3. Image path post_image/post.png se upload karein
        local_image_path = "post_image/post.png"
        uploaded_image_url = upload_image_to_imgbb(local_image_path)
        
        # 4. Agar image mil jaye to use content ke sabse upar HTML banner jaisa jodein
        if uploaded_image_url:
            image_html_tag = f'<p align="center" style="margin-bottom: 20px;"><img src="{uploaded_image_url}" alt="{post_title}" style="max-width:100%; height:auto; border-radius: 8px;" /></p>'
            final_html_content = image_html_tag + markdown_html
        else:
            final_html_content = markdown_html

        # 5. Blogger par upload karein
        post_response = create_blogger_post(post_title, final_html_content, post_tags)
        
        # 6. Agar post successfully upload ho jaye toh JSON update karein
        if post_response and "url" in post_response:
            print("[STEP] Updating 'post_posted' status and 'url' inside JSON...", flush=True)
            
            # Extract Blogger live post URL
            blog_post_url = post_response["url"]
            
            # Use .update() taaki baki koi key-value pair delete ya change na ho
            ideas_list[target_index].update({
                "post_posted": True,
                "url": blog_post_url
            })
            
            with ideas_file.open("w", encoding="utf-8") as f:
                json.dump(ideas_list, f, indent=2, ensure_ascii=False)
                
            print(f"✅ Success! JSON updated seamlessly with post_posted=True and url='{blog_post_url}'.")
        else:
            print("❌ Blogger post upload failed or URL not returned, JSON state was not modified.")
            sys.exit(1)
    else:
        print("❌ Execution band kar diya gaya kyunki md file process nahi ho saki.")
        sys.exit(1)