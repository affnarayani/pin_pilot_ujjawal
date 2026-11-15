import os
from pathlib import Path

from common.config_loader import get_path

try:
    import requests
except Exception:
    requests = None

ERROR_DIR = get_path("error")
ERROR_SCREENSHOT = ERROR_DIR / "error_screenshot.png"


def ensure_error_dir():
    ERROR_DIR.mkdir(parents=True, exist_ok=True)


def capture_error_screenshot(page, filename: str = "error_screenshot.png") -> Path:
    ensure_error_dir()
    path = ERROR_DIR / filename
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        try:
            page.screenshot(path=str(path))
        except Exception:
            pass
    return path


def upload_to_onlyfiles(file_path: Path, expire: int = 172800):
    if requests is None:
        print("[WARNING] requests package not available for OnlyFiles upload.", flush=True)
        return None
    if not file_path or not Path(file_path).exists():
        return None
    url = "https://api.onlyfiles.com/v1/upload"
    try:
        with open(file_path, "rb") as f:
            response = requests.post(url, files={"file": f}, data={"expire": expire}, timeout=60)
        if response.status_code == 200:
            data = response.json()
            direct_url = data.get("data", {}).get("file", {}).get("url", {}).get("full")
            print(f"[OK] Screenshot uploaded to OnlyFiles: {direct_url}", flush=True)
            return direct_url
        print(f"[WARNING] OnlyFiles upload failed: {response.status_code} {response.text}", flush=True)
    except Exception as e:
        print(f"[WARNING] OnlyFiles upload error: {e}", flush=True)
    return None


def upload_to_imgbb(file_path: Path):
    if requests is None:
        print("[WARNING] requests package not available for ImgBB upload.", flush=True)
        return None
    api_key = os.getenv("IMGBBB_API_KEY")
    if not api_key:
        print("[ERROR] IMGBBB_API_KEY missing. ImgBB upload failed.", flush=True)
        return None
    if not file_path or not Path(file_path).exists():
        return None
    url = "https://api.imgbb.com/1/upload"
    try:
        with open(file_path, "rb") as image_file:
            response = requests.post(url, data={"key": api_key}, files={"image": image_file}, timeout=60)
        if response.status_code == 200:
            data = response.json()
            image_url = data.get("data", {}).get("url")
            print(f"[OK] Image uploaded to ImgBB: {image_url}", flush=True)
            return image_url
        print(f"[ERROR] ImgBB upload failed: {response.status_code} {response.text}", flush=True)
    except Exception as e:
        print(f"[ERROR] ImgBB upload error: {e}", flush=True)
    return None


def handle_error_screenshot(page, upload: bool = True):
    path = capture_error_screenshot(page)
    if upload and path and path.exists():
        upload_to_onlyfiles(path)
    return path
