import base64
import json
import os
import re
from pathlib import Path
import requests

from common.locator_manager import (
    get_locator_expressions,
    normalize_locator_for_wrap,
    remove_changed_locator_entries,
    upsert_changed_locator,
)

_ROOT = Path(__file__).resolve().parent.parent


def _eval_locator(page, expr: str):
    expr = expr.strip()
    # Safe-ish eval: provide page and re only
    return eval(expr, {"__builtins__": {}}, {"page": page, "re": re})


def _is_candidate_visible(locator, timeout_ms: int = 0) -> bool:
    try:
        candidate = locator.first if hasattr(locator, "first") else locator
    except Exception:
        candidate = locator
    # Fast path: immediate checks, no long waits
    try:
        if hasattr(candidate, "is_visible"):
            if candidate.is_visible():
                return True
    except Exception:
        pass
    try:
        if hasattr(candidate, "count") and candidate.count() > 0:
            # if present in DOM, try very short visibility wait
            try:
                candidate.wait_for(state="visible", timeout=max(0, int(timeout_ms)))
                return True
            except Exception:
                # attached but maybe not visible; still acceptable in some cases? prefer visible
                pass
    except Exception:
        pass
    return False


def try_locate_fast(page, platform: str, locator_name: str):
    """Try all configured expressions quickly (no sequential long timeout waits). Returns (locator_or_none, used_expr_or_none)."""
    exprs = get_locator_expressions(platform, locator_name)
    if not exprs:
        return None, None
    # First pass: immediate visibility/count checks
    for expr in exprs:
        try:
            loc = _eval_locator(page, expr)
            candidate = loc.first if hasattr(loc, "first") else loc
            try:
                if candidate.is_visible():
                    return candidate, expr
            except Exception:
                pass
            try:
                if hasattr(loc, "count") and loc.count() > 0:
                    # quick short wait
                    try:
                        candidate.wait_for(state="visible", timeout=300)
                        return candidate, expr
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            continue
    # Second pass: very short bounded waits per candidate to catch elements appearing momentarily (still not long blocking chain)
    for expr in exprs:
        try:
            loc = _eval_locator(page, expr)
            candidate = loc.first if hasattr(loc, "first") else loc
            try:
                candidate.wait_for(state="attached", timeout=500)
                # prefer visible
                try:
                    if candidate.is_visible():
                        return candidate, expr
                except Exception:
                    pass
                try:
                    if loc.count() > 0:
                        return candidate, expr
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            continue
    return None, None


def _save_temp_screenshot(page, purpose: str = "locator") -> Path:
    err_dir = _ROOT / "error"
    err_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", purpose)[:80]
    path = err_dir / f"_temp_{safe}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        try:
            page.screenshot(path=str(path))
        except Exception:
            return None
    return path if path.exists() else None


def _call_gemini_vision(image_path: Path, platform: str, locator_name: str, task_description: str, failed_expressions):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not image_path or not Path(image_path).exists():
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None
    prompt = (
        "You are fixing a Playwright UI locator for browser automation. "
        f"Platform: {platform}. Locator variable name: {locator_name}. "
        f"Task/intent: {task_description}. "
        f"Failed locator expressions tried (in order): {json.dumps(failed_expressions, ensure_ascii=False)}. "
        "Inspect the screenshot and return the best SINGLE working Playwright Python locator expression for the target element. "
        "Rules: return ONLY the expression, no markdown, no explanation. "
        "Expression must start with 'page.'. "
        "Do NOT suggest any selector that starts with '#' (no id selectors like #radix-...). "
        "Prefer role, text, test id, aria-label, or stable attributes. "
        "Wrap raw selectors with page.locator(...) if needed."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": b64}}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300},
    }
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code != 200:
            return None
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).strip()
        text = re.sub(r"\n?```$", "", text).strip()
        if not text:
            return None
        text = text.splitlines()[0].strip()
        if not text.startswith("page."):
            text = normalize_locator_for_wrap(text)
        # reject id-starting css selectors
        if re.search(r"page\.locator\(\s*['\"]#[^'\"]+['\"]\s*\)", text):
            return None
        return text
    except Exception:
        return None


def resolve_locator(page, platform: str, locator_name: str, task_description: str, timeout_ms: int = 1500):
    """Resolve locator: try all txt primary/fallbacks quickly; if none work use Gemini Vision fallback to suggest and save to changed_locators.json for next run (do not execute in current run)."""
    exprs = get_locator_expressions(platform, locator_name)
    loc, used = try_locate_fast(page, platform, locator_name)
    if loc is not None:
        # if it worked, remove any pending changed entry for this locator that is no longer needed (cleanup will also run; safe mid-run)
        remove_changed_locator_entries(platform, locator_name)
        return loc

    # Gemini vision fallback: save suggested locator for next run (apply based on auto_correct_locator), do not implement in current run
    screenshot = _save_temp_screenshot(page, f"{platform}_{locator_name}")
    suggested = _call_gemini_vision(screenshot, platform, locator_name, task_description, exprs)
    broke = exprs[0] if exprs else "(missing)"
    if suggested:
        suggested = normalize_locator_for_wrap(suggested)
        if not re.search(r"page\.locator\(\s*['\"]#[^'\"]+['\"]\s*\)", suggested):
            upsert_changed_locator(platform, locator_name, broke, suggested)
            print(f"[INFO] Gemini suggested locator saved for next run: platform='{platform}', locator='{locator_name}'", flush=True)
        else:
            suggested = None
    raise RuntimeError(f"Unable to resolve locator '{locator_name}' for platform '{platform}' (task: {task_description})")
