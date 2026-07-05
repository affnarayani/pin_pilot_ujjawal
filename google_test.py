import os
import glob
import json
import requests
import markdown
import frontmatter
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
TOKEN_JSON_STR = os.getenv("BLOGGER_TOKEN_JSON")
IMGBB_API_KEY = os.getenv("IMGBBB_API_KEY") # Corrected key as requested

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
        return None

def fetch_and_parse_md_file():
    """posts/ folder se pehli md file utha kar metadata aur HTML content return karta hai"""
    # glob se posts folder ki sabhi md files dhoondhna
    md_files = glob.glob("posts/*.md")
    
    if not md_files:
        print("❌ Error: 'posts/' folder me koi .md file nahi mili!")
        return None, None, None
        
    # Pehli file select karna
    target_file = md_files[0]
    print(f"📄 Found markdown file: {target_file}")
    
    # Python-frontmatter se parse karna
    with open(target_file, 'r', encoding='utf-8') as f:
        post = frontmatter.load(f)
        
    # Front matter metadata nikalna
    title = post.get('title', 'Untitled Post')
    tags = post.get('tags', [])
    
    # Markdown body ko HTML table aur formatting ke saath safe HTML me convert karna
    # 'tables' extension markdown ke tables ko <table> tag me convert karega
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
    
    # 1. Md file fetch aur parse karein
    post_title, markdown_html, post_tags = fetch_and_parse_md_file()
    
    if post_title and markdown_html:
        # 2. Image path set karein aur upload karein
        local_image_path = "image/pin.png"
        uploaded_image_url = upload_image_to_imgbb(local_image_path)
        
        # 3. Agar image mil jaye to use content ke sabse upar HTML banner jaisa jodein
        if uploaded_image_url:
            image_html_tag = f'<p align="center" style="margin-bottom: 20px;"><img src="{uploaded_image_url}" alt="{post_title}" style="max-width:100%; height:auto; border-radius: 8px;" /></p>'
            # Markdown se bani HTML ke aage image tag merge karna
            final_html_content = image_html_tag + markdown_html
        else:
            final_html_content = markdown_html

        # 4. Blogger par upload karein
        create_blogger_post(post_title, final_html_content, post_tags)
    else:
        print("❌ Execution band kar diya gaya kyunki md file process nahi ho saki.")