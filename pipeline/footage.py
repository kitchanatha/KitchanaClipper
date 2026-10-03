"""Step 3 - download vertical stock footage from Pexels or Pixabay.

Uses Pexels when PEXELS_API_KEY is set, otherwise Pixabay (PIXABAY_API_KEY).
"""
import os
import random
import re
from pathlib import Path

import requests

from .common import env

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"


def _long_side(f):
    return max(f.get("width") or 0, f.get("height") or 0)


def _pexels_pick_file(video):
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and _long_side(f) >= 1280]
    if not files:
        return None
    files.sort(key=lambda f: abs(_long_side(f) - 1920))
    return files[0]["link"]


def _excluded(text, exclude):
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return any(x in words for x in exclude)


# words that say how something looks, not what it is - they don't count as a topic match
_MOOD_WORDS = {"old", "vintage", "rustic", "antique", "dark", "night", "close", "with", "from",
               "into", "light", "slow", "motion", "shot", "view", "scene", "background", "quiet",
               "empty", "dim", "soft", "warm", "cold", "moody", "gloomy", "the", "and", "at", "in",
               "of", "on", "up", "low", "small", "big", "large", "long", "lit", "out", "through"}


def relevant(query, text):
    """True if the clip's tags (or Pexels page slug) share a real subject word with the
    query - 'old cottage interior' must not match a clip tagged only 'old, bridge, london'.
    Words match on a shared 4-letter start, so candle ~ candlelight, knock ~ knocking."""
    subjects = [w for w in re.findall(r"[a-z]+", query.lower()) if w not in _MOOD_WORDS and len(w) > 2]
    if not subjects:
        return True
    tags = re.findall(r"[a-z]+", (text or "").lower())
    for s in subjects:
        for t in tags:
            n = min(len(s), len(t))
            if n >= 3 and (s.startswith(t) or t.startswith(s) or (n >= 4 and s[:4] == t[:4])):
                return True
    return False


def _pexels_search(key, q, page, clip_seconds, seen, orientation="portrait", exclude=(),
                   strict=False):
    """Returns [(history_id, download_url)] for one search."""
    r = requests.get(PEXELS_SEARCH, headers={"Authorization": key},
                     params={"query": q, "orientation": orientation, "size": "medium",
                             "per_page": 15, "page": page}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Pexels error {r.status_code}: {r.text[:300]}")
    out = []
    for v in r.json().get("videos", []):
        if v["id"] in seen or v.get("duration", 0) < clip_seconds or _excluded(v.get("url"), exclude):
            continue
        if strict and not relevant(q, v.get("url")):
            continue
        link = _pexels_pick_file(v)
        if link:
            out.append((v["id"], link))
    return out


def _pixabay_pick_file(video, orientation="portrait"):
    # Pixabay has no orientation filter for videos: prefer the wanted orientation, else the
    # sharpest file of the other one (it gets center-cropped).
    files = [f for f in (video.get("videos") or {}).values()
             if f.get("url") and min(f.get("width") or 0, f.get("height") or 0) >= 720]
    if not files:
        return None, False
    if orientation == "landscape":
        match = [f for f in files if (f.get("width") or 0) > (f.get("height") or 0)]
    else:
        match = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0)]
    pool = match or files
    pool.sort(key=lambda f: abs(_long_side(f) - 1920))
    return pool[0]["url"], bool(match)


def _pixabay_search(key, q, page, clip_seconds, seen, orientation="portrait", exclude=(),
                    strict=False):
    """Returns [(history_id, download_url)] for one search, wanted orientation first.

    Pixabay matches any word of the query, so only its top results stay on topic."""
    r = requests.get(PIXABAY_SEARCH,
                     params={"key": key, "q": q[:100], "video_type": "film",
                             "safesearch": "true", "per_page": 10, "page": page}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Pixabay error {r.status_code}: {r.text[:300]}")
    wanted, other = [], []
    for v in r.json().get("hits", []):
        hid = f"pixabay_{v['id']}"
        if hid in seen or v.get("duration", 0) < clip_seconds or _excluded(v.get("tags"), exclude):
            continue
        if strict and not relevant(q, v.get("tags")):
            continue
        link, matches = _pixabay_pick_file(v, orientation)
        if link:
            (wanted if matches else other).append((hid, link))
    if not strict:  # strict keeps Pixabay's ranking: best matches first
        random.shuffle(wanted)
        random.shuffle(other)
    return wanted + other


def get_footage(keywords, extra_hint, needed_seconds, clip_seconds, folder, used_ids,
                orientation="portrait", exclude=(), strict=False):
    """Download enough clips to cover the voiceover. Returns (file paths, history ids).
    strict (story mode): keep search ranking and skip clips whose tags don't match the search."""
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
        found = search(key, q, page, clip_seconds, seen, orientation, [x.lower() for x in exclude],
                       strict)
        if shuffle and not strict:
            random.shuffle(found)
        for hid, link in found[:2]:  # max 2 clips per search term keeps it varied
            name = hid if isinstance(hid, str) else f"pexels_{hid}"
            p = folder / f"{name}.mp4"
            seen.add(hid)
            try:  # a stalled or broken download skips that clip instead of failing the video
                with requests.get(link, stream=True, timeout=(15, 60)) as d:
                    d.raise_for_status()
                    with open(p, "wb") as f:
                        for chunk in d.iter_content(1 << 20):
                            f.write(chunk)
            except requests.RequestException as e:
                print(f"  skipped a clip that failed to download ({type(e).__name__})")
                p.unlink(missing_ok=True)
                continue
            paths.append(str(p))
            ids.append(hid)
            if len(paths) >= need:
                break
    if not paths:
        raise RuntimeError("No stock footage found - try different keywords/footage_hint")
    return paths, ids
