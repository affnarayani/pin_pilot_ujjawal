import json
import os
import random
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "json" / "config.json"
_config_cache = None


def load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            _config_cache = json.load(f)
    else:
        _config_cache = {}
    return _config_cache


def get(key, default=None):
    return load_config().get(key, default)


def get_path(*parts):
    return _ROOT.joinpath(*parts)


def random_wait(min_sec=None, max_sec=None):
    cfg = load_config()
    if min_sec is None:
        min_sec = cfg.get("min_random_wait", 15)
    if max_sec is None:
        max_sec = cfg.get("max_random_wait", 30)
    seconds = random.uniform(float(min_sec), float(max_sec))
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)
    return seconds
