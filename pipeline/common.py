"""Shared helpers: config loading, paths, history."""
import json
import os
import re
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = ROOT
    for key in ("output_dir", "fonts_dir"):
        cfg[key] = str((ROOT / cfg[key]).resolve())
    return cfg


def env(name):
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Missing {name}. Add it to the .env file (see .env.example).")
    return val


def slugify(text, max_len=40):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-")
    return text[:max_len].strip("-") or "video"


HISTORY = ROOT / "data" / "history.json"


def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return {}


def save_history(hist):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
