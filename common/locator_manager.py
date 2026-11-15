import json
import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LOCATORS_DIR = _ROOT / "locators"
CHANGED_LOCATORS_PATH = LOCATORS_DIR / "changed_locators.json"


def _locator_file(platform: str) -> Path:
    return LOCATORS_DIR / f"{platform}_locators.txt"


def parse_locator_file(platform: str):
    path = _locator_file(platform)
    locators = {}
    if not path.exists():
        return locators
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in line:
                continue
            name, expr = line.split("=", 1)
            name = name.strip()
            expr = expr.strip()
            if not name or not expr:
                continue
            locators.setdefault(name, []).append(expr)
    return locators


def get_locator_expressions(platform: str, name: str):
    return parse_locator_file(platform).get(name, [])


def load_changed_locators():
    if not CHANGED_LOCATORS_PATH.exists():
        return []
    try:
        with CHANGED_LOCATORS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            valid = []
            for e in data:
                if isinstance(e, dict) and e.get("locator_name") and e.get("broke_locator") and e.get("suggested_locator"):
                    valid.append(e)
            return valid
    except Exception:
        pass
    return []


def save_changed_locators(entries):
    LOCATORS_DIR.mkdir(parents=True, exist_ok=True)
    with CHANGED_LOCATORS_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _is_forbidden_suggested(expr: str) -> bool:
    if not expr:
        return True
    s = expr.strip()
    # do not suggest locator starting with # (raw/css id selector like #radix-...)
    if s.startswith("#"):
        return True
    # reject wrapped id selectors e.g. page.locator('#radix...') or ("#...")
    if re.search(r"page\.locator\(\s*['\"]#[^'\"]+['\"]\s*\)", s):
        return True
    return False


def upsert_changed_locator(platform: str, locator_name: str, broke_locator: str, suggested_locator: str):
    if _is_forbidden_suggested(suggested_locator):
        return
    suggested_locator = suggested_locator.strip()
    entries = load_changed_locators()
    updated = False
    for entry in entries:
        if (
            entry.get("platform") == platform
            and entry.get("locator_name") == locator_name
            and entry.get("broke_locator") == broke_locator
        ):
            if entry.get("suggested_locator") != suggested_locator:
                entry["suggested_locator"] = suggested_locator
            updated = True
            break
    if not updated:
        entries.append({
            "platform": platform,
            "locator_name": locator_name,
            "broke_locator": broke_locator,
            "suggested_locator": suggested_locator,
        })
    save_changed_locators(entries)


def remove_changed_locator_entries(platform: str, locator_name: str, suggested_locator: str = None):
    entries = load_changed_locators()
    new_entries = []
    for e in entries:
        if e.get("platform") != platform or e.get("locator_name") != locator_name:
            new_entries.append(e)
            continue
        if suggested_locator is not None and e.get("suggested_locator") != suggested_locator:
            new_entries.append(e)
            continue
        # drop
    if len(new_entries) != len(entries):
        save_changed_locators(new_entries)


def cleanup_changed_locators_against_txt():
    entries = load_changed_locators()
    if not entries:
        return
    kept = []
    for e in entries:
        platform = e.get("platform")
        name = e.get("locator_name")
        suggested = e.get("suggested_locator")
        if not platform or not name or not suggested:
            continue
        exprs = get_locator_expressions(platform, name)
        if suggested in exprs:
            continue
        kept.append(e)
    if len(kept) != len(entries):
        save_changed_locators(kept)


def _read_locator_lines(platform: str):
    path = _locator_file(platform)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return f.read().splitlines()


def _write_locator_lines(platform: str, lines):
    path = _locator_file(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def append_locator_after_primary(platform: str, locator_name: str, suggested_locator: str):
    if _is_forbidden_suggested(suggested_locator):
        return
    exprs = get_locator_expressions(platform, locator_name)
    if suggested_locator in exprs:
        return
    lines = _read_locator_lines(platform)
    insert_line = f"{locator_name} = {suggested_locator}"
    if not lines:
        _write_locator_lines(platform, [insert_line])
        return
    primary_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        n, _ = line.split("=", 1)
        if n.strip() == locator_name:
            primary_idx = i
            break
    if primary_idx is None:
        lines.append(insert_line)
    else:
        lines.insert(primary_idx + 1, insert_line)
    _write_locator_lines(platform, lines)


def load_config_flag(flag_name: str, default=False):
    cfg_path = _ROOT / "json" / "config.json"
    if not cfg_path.exists():
        return default
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return bool(cfg.get(flag_name, default))
    except Exception:
        return default


def apply_auto_correct():
    cleanup_changed_locators_against_txt()
    auto = load_config_flag("auto_correct_locator", False)
    entries = load_changed_locators()
    if not entries:
        return
    if auto:
        remaining = []
        for e in entries:
            platform = e.get("platform")
            name = e.get("locator_name")
            suggested = e.get("suggested_locator")
            broke = e.get("broke_locator")
            if platform and name and suggested and broke:
                append_locator_after_primary(platform, name, suggested)
                continue
            remaining.append(e)
        save_changed_locators(remaining)
    else:
        cleanup_changed_locators_against_txt()


def normalize_locator_for_wrap(expr: str) -> str:
    expr = (expr or "").strip()
    if not expr:
        return expr
    if expr.startswith("page."):
        return expr
    # raw selector must be wrapped in page.locator(...)
    escaped = expr.replace("\\", "\\\\").replace("'", "\\'")
    return f"page.locator('{escaped}')"
