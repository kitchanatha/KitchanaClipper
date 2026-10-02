"""Step 3 - download vertical stock footage from Pexels or Pixabay.

Uses Pexels when PEXELS_API_KEY is set, otherwise Pixabay (PIXABAY_API_KEY).
"""
import os
import random
from pathlib import Path

import requests

from .common import env

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"


def _pexels_pick_file(video):
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 1280]
    if not files:
        return None
    files.sort(key=lambda f: abs((f.get("height") or 0) - 1920))
    return files[0]["link"]


def _pexels_search(key, q, page, clip_seconds, seen):
    """Returns [(history_id, download_url)] for one search."""
    r = requests.get(PEXELS_SEARCH, headers={"Authorization": key},
                     params={"query": q, "orientation": "portrait", "size": "medium",
                             "per_page": 15, "page": page}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Pexels error {r.status_code}: {r.text[:300]}")
    out = []
    for v in r.json().get("videos", []):
        if v["id"] in seen or v.get("duration", 0) < clip_seconds:
            continue
        link = _pexels_pick_file(v)
        if link:
            out.append((v["id"], link))
    return out


def _pixabay_pick_file(video):
    # Pixabay has no orientation filter for videos: prefer portrait, else the
    # sharpest landscape file (it gets center-cropped to 9:16).
    files = [f for f in (video.get("videos") or {}).values()
             if f.get("url") and min(f.get("width") or 0, f.get("height") or 0) >= 720]
    if not files:
        return None, False
    portrait = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0)]
    pool = portrait or files
    pool.sort(key=lambda f: abs(max(f.get("width") or 0, f.get("height") or 0) - 1920))
    return pool[0]["url"], bool(portrait)


def _pixabay_search(key, q, page, clip_seconds, seen):
    """Returns [(history_id, download_url)] for one search, portrait clips first.

    Pixabay matches any word of the query, so only its top results stay on topic."""
    r = requests.get(PIXABAY_SEARCH,
                     params={"key": key, "q": q[:100], "video_type": "film",
                             "safesearch": "true", "per_page": 10, "page": page}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Pixabay error {r.status_code}: {r.text[:300]}")
    portrait, landscape = [], []
    for v in r.json().get("hits", []):
        hid = f"pixabay_{v['id']}"
        if hid in seen or v.get("duration", 0) < clip_seconds:
            continue
        link, is_portrait = _pixabay_pick_file(v)
        if link:
            (portrait if is_portrait else landscape).append((hid, link))
    random.shuffle(portrait)
    random.shuffle(landscape)
    return portrait + landscape


def get_footage(keywords, extra_hint, needed_seconds, clip_seconds, folder, used_ids):
    """Download enough clips to cover the voiceover. Returns (file paths, history ids)."""
    if os.environ.get("PEXELS_API_KEY", "").strip():
        key, search, shuffle = env("PEXELS_API_KEY"), _pexels_search, True
    elif os.environ.get("PIXABAY_API_KEY", "").strip():
        key, search, shuffle = env("PIXABAY_API_KEY"), _pixabay_search, False
    else:
        raise SystemExit("Missing stock footage key. Add PEXELS_API_KEY or PIXABAY_API_KEY "
                         "to the .env file (see .env.example).")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    need = int(needed_seconds // clip_seconds) + 1
    queries = list(keywords) + [h.strip() for h in extra_hint.split(",") if h.strip()]
    paths, ids, seen = [], [], set(used_ids)
    qi = 0
    while len(paths) < need and qi < len(queries) * 2:
        q = queries[qi % len(queries)]
        page = 1 + qi // len(queries)
        qi += 1
        found = search(key, q, page, clip_seconds, seen)
        if shuffle:
            random.shuffle(found)
        for hid, link in found[:2]:  # max 2 clips per search term keeps it varied
            name = hid if isinstance(hid, str) else f"pexels_{hid}"
            p = folder / f"{name}.mp4"
            with requests.get(link, stream=True, timeout=180) as d:
                d.raise_for_status()
                with open(p, "wb") as f:
                    for chunk in d.iter_content(1 << 20):
                        f.write(chunk)
            seen.add(hid)
            paths.append(str(p))
            ids.append(hid)
            if len(paths) >= need:
                break
    if not paths:
        raise RuntimeError("No stock footage found - try different keywords/footage_hint")
    return paths, ids
