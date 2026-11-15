import json
import os
import sys
from pathlib import Path

import frontmatter
import markdown
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from common.config_loader import get_path
from common.screenshot import upload_to_imgbb

# Load environment variables
load_dotenv()

BLOG_ID = os.getenv("BLOG_ID")
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
TOKEN_JSON_STR = os.getenv("TOKEN_JSON_STR")

SCOPES = ["https://www.googleapis.com/auth/blogger"]

IDEAS_FILE = get_path("json", "pinterest_ideas.json")
POST_FILE = get_path("blogspot_post", "post.md")
POST_IMAGE_FILE = get_path("blogspot_image", "post.png")
TOKEN_FILE = get_path("json", "token.json")


def get_blogger_service():
    """Build and return authenticated Blogger API service."""
    if TOKEN_JSON_STR:
        token_data = json.loads(TOKEN_JSON_STR)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    elif TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    else:
        raise ValueError("No Blogger token found in TOKEN_JSON_STR or json/token.json")

    creds._client_id = CLIENT_ID
    creds._client_secret = CLIENT_SECRET

    if creds and creds.expired and creds.refresh_token:
        print("[STEP] Refreshing expired access token...", flush=True)
        creds.refresh(Request())

    return build("blogger", "v3", credentials=creds)


def upload_post_image_to_imgbb(image_path: Path):
    """Upload Blogspot post image to ImgBB for permanent hosting."""
    if not image_path.exists():
        print(f"[WARNING] Post image not found: {image_path}", flush=True)
        return None
    print("[STEP] Uploading post image to ImgBB...", flush=True)
    image_url = upload_to_imgbb(image_path)
    if not image_url:
        print("[ERROR] ImgBB upload failed for post image.", flush=True)
    return image_url


def fetch_and_parse_post_file():
    """Read and parse markdown post file into title, HTML content, and tags."""
    if not POST_FILE.exists():
        print(f"[ERROR] Required markdown file not found: {POST_FILE}", flush=True)
        return None, None, None

    print(f"[STEP] Reading markdown post file: {POST_FILE}", flush=True)
    with POST_FILE.open("r", encoding="utf-8") as f:
        post = frontmatter.load(f)

    title = post.get("title", "Untitled Post")
    tags = post.get("tags", [])
    html_content = markdown.markdown(post.content, extensions=["tables"])
    return title, html_content, tags


def create_blogger_post(title, html_content, tags=None):
    """Create a Blogger post and return API response."""
    if tags is None:
        tags = []
    try:
        service = get_blogger_service()
        post_body = {
            "kind": "blogger#post",
            "blog": {"id": BLOG_ID},
            "title": title,
            "content": html_content,
            "labels": tags,
        }
        request = service.posts().insert(blogId=BLOG_ID, body=post_body)
        response = request.execute()
        print(f"[OK] Blogger post published: {response.get('url')}", flush=True)
        return response
    except Exception as e:
        print(f"[ERROR] Failed to create Blogger post: {e}", flush=True)
        return None


if __name__ == "__main__":
    print("[START] Blogger post publish script started", flush=True)

    if not IDEAS_FILE.exists():
        print(f"[ERROR] Ideas file not found: {IDEAS_FILE}", flush=True)
        sys.exit(1)

    with IDEAS_FILE.open("r", encoding="utf-8") as f:
        ideas_list = json.load(f)

    target_index = -1
    for index, item in enumerate(ideas_list):
        if isinstance(item, dict) and item.get("post_image_generated") is True and item.get("post_posted") is False:
            target_index = index
            break

    if target_index == -1:
        print("[INFO] No topic ready for Blogspot posting. Exiting safely.", flush=True)
        sys.exit(0)

    print(f"[OK] Found publish target at index [{target_index}]", flush=True)

    post_title, markdown_html, post_tags = fetch_and_parse_post_file()
    if not post_title or not markdown_html:
        print("[ERROR] Could not process markdown post content.", flush=True)
        sys.exit(1)

    uploaded_image_url = upload_post_image_to_imgbb(POST_IMAGE_FILE)
    if uploaded_image_url:
        image_html_tag = (
            f'<p align="center" style="margin-bottom: 20px;">'
            f'<img src="{uploaded_image_url}" alt="{post_title}" style="max-width:100%; height:auto; border-radius: 8px;" />'
            f"</p>"
        )
        final_html_content = image_html_tag + markdown_html
    else:
        final_html_content = markdown_html

    post_response = create_blogger_post(post_title, final_html_content, post_tags)
    if post_response and "url" in post_response:
        blog_post_url = post_response["url"]
        ideas_list[target_index].update({"post_posted": True, "url": blog_post_url})
        with IDEAS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ideas_list, f, indent=2, ensure_ascii=False)
        print(f"[OK] Updated ideas state: post_posted=True, url='{blog_post_url}'", flush=True)
    else:
        print("[ERROR] Blogspot publish failed or URL missing. State not updated.", flush=True)
        sys.exit(1)

    print("[DONE] Blogger post publish script finished", flush=True)
