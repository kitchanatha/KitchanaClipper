"""After a video is approved and uploaded to YouTube.

1. Cross-post pack (every video): output/crosspost/<channel>/<date>_<slug>/ with video.mp4 and
   caption_<lang>.txt - a Facebook Reels / Instagram caption and a shorter TikTok caption with
   hashtags, plus a sources line for news. Upload it from your phone/PC (TikTok is never automatic).
2. Facebook Reels + Instagram Reels (optional, per channel in config `crosspost.channels`): posted
   through Meta's official Graph API - only after your Approve in Discord, only when the channel is
   switched on and its tokens are in .env (META_PAGE_ID_<KEY>, META_PAGE_TOKEN_<KEY>,
   META_IG_ID_<KEY>; see README). Problems are reported, never fatal: the YouTube upload stands.
"""
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import requests

from .common import ROOT

TIKTOK_MAX = 150  # keep TikTok captions short (hashtags included)


def _settings(cfg):
    return cfg.get("crosspost") or {}


def _outlets(cfg, meta):
    from .newslib import outlet_for
    credible = (cfg.get("news") or {}).get("credible_outlets", {})
    return list(dict.fromkeys(o for o in (outlet_for(u, credible) for u in meta.get("sources", [])) if o))


def captions(cfg, key, meta):
    """{"facebook", "instagram", "tiktok"} - the written social captions when they exist, else
    built from the title / description / hashtags (works without any API credit)."""
    ch = cfg["channels"][key]
    social = meta.get("social") or {}
    tags = [t if t.startswith("#") else "#" + t for t in
            list(dict.fromkeys((meta.get("hashtags") or []) + ch.get("hashtags", [])))
            if t.lstrip("#").lower() != "shorts"]
    src = ""
    if meta.get("kind") == "news":
        outs = _outlets(cfg, meta)
        if outs:
            src = "แหล่งข่าว: " + ", ".join(outs) + " (ลิงก์ครบในคำอธิบายบน YouTube)"
    desc = (meta.get("description") or "").split("\n\n")[0].strip()
    base = "\n\n".join(x for x in [meta.get("title", ""), desc, src, " ".join(tags[:6])] if x)
    fb = social.get("facebook") or base
    ig = social.get("instagram") or base
    if src and src not in fb:
        fb += "\n\n" + src
    if src and src not in ig:
        ig += "\n\n" + src
    tt = meta.get("title", "")
    for t in tags[:5]:
        if len(tt) + 1 + len(t) <= TIKTOK_MAX:
            tt += " " + t
    if src:  # news: TikTok gets the outlets too (short form)
        tt = (tt + "\n" + "แหล่งข่าว: " + ", ".join(_outlets(cfg, meta)))[:TIKTOK_MAX + 80]
    return {"facebook": fb.strip(), "instagram": ig.strip(), "tiktok": tt.strip()}


def make_pack(cfg, key, folder, youtube_url=None):
    """Copy the video + write the captions. Returns the pack folder."""
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    base = ROOT / _settings(cfg).get("dir", "output/crosspost") / key
    pack = base / f"{datetime.now():%Y%m%d}_{folder.name}"
    pack.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(folder / "video.mp4", pack / "video.mp4")
    c = captions(cfg, key, meta)
    lang = cfg["channels"][key].get("language", "en")
    text = (f"FACEBOOK REELS\n\n{c['facebook']}\n\n\nINSTAGRAM REELS\n\n{c['instagram']}\n\n\n"
            f"TIKTOK (short)\n\n{c['tiktok']}\n")
    if youtube_url:
        text += f"\n\nYouTube: {youtube_url}\n"
    (pack / f"caption_{lang}.txt").write_text(text, encoding="utf-8")
    return pack


# ---------------------------------------------------------------- Meta Graph API

def _env(name):
    return os.environ.get(name, "").strip()


def meta_creds(key):
    k = key.upper()
    return {"page_id": _env(f"META_PAGE_ID_{k}"), "token": _env(f"META_PAGE_TOKEN_{k}"),
            "ig_id": _env(f"META_IG_ID_{k}")}


def _check(r, what):
    if r.status_code != 200:
        raise RuntimeError(f"{what}: Meta API {r.status_code} {r.text[:300]}")
    return r.json()


def post_facebook_reel(cfg, key, video, caption):
    """Facebook Page Reel (start -> upload -> finish). Returns the reel URL."""
    ver = _settings(cfg).get("graph_version", "v23.0")
    c = meta_creds(key)
    api = f"https://graph.facebook.com/{ver}/{c['page_id']}/video_reels"
    start = _check(requests.post(api, data={"upload_phase": "start", "access_token": c["token"]},
                                 timeout=60), "facebook start")
    vid = start["video_id"]
    data = Path(video).read_bytes()
    up = requests.post(start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{ver}/{vid}",
                       headers={"Authorization": f"OAuth {c['token']}", "offset": "0",
                                "file_size": str(len(data))}, data=data, timeout=600)
    _check(up, "facebook upload")
    _check(requests.post(api, data={"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                    "description": caption, "access_token": c["token"]}, timeout=60),
           "facebook publish")
    return f"https://www.facebook.com/reel/{vid}"


def post_instagram_reel(cfg, key, video, caption, wait_s=600):
    """Instagram Reel (resumable upload -> wait until processed -> publish). Returns the permalink."""
    ver = _settings(cfg).get("graph_version", "v23.0")
    c = meta_creds(key)
    g = f"https://graph.facebook.com/{ver}"
    cont = _check(requests.post(f"{g}/{c['ig_id']}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": c["token"]}, timeout=60), "instagram container")
    cid = cont["id"]
    data = Path(video).read_bytes()
    _check(requests.post(cont.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{ver}/{cid}",
                         headers={"Authorization": f"OAuth {c['token']}", "offset": "0",
                                  "file_size": str(len(data))}, data=data, timeout=600), "instagram upload")
    t0 = time.time()
    while True:  # Instagram processes the video before it can be published
        st = _check(requests.get(f"{g}/{cid}", params={"fields": "status_code,status",
                                                        "access_token": c["token"]}, timeout=60),
                    "instagram status")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED") or time.time() - t0 > wait_s:
            raise RuntimeError(f"instagram processing: {st.get('status_code')} {st.get('status', '')}")
        time.sleep(10)
    media = _check(requests.post(f"{g}/{c['ig_id']}/media_publish",
                                 data={"creation_id": cid, "access_token": c["token"]}, timeout=60),
                   "instagram publish")
    link = _check(requests.get(f"{g}/{media['id']}", params={"fields": "permalink",
                                                             "access_token": c["token"]}, timeout=60),
                  "instagram permalink")
    return link.get("permalink") or f"instagram media {media['id']}"


def crosspost(cfg, key, folder, youtube_url=None, log=print):
    """Pack + optional Facebook / Instagram posting. Returns notes for Discord; never raises."""
    s = _settings(cfg)
    notes = []
    folder = Path(folder)
    try:
        if s.get("pack", True):
            pack = make_pack(cfg, key, folder, youtube_url)
            notes.append(f"📦 Cross-post pack (TikTok etc.): {pack}")
    except Exception as e:
        notes.append(f"📦 pack failed: {str(e)[:200]}")
    want = (s.get("channels") or {}).get(key) or {}
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    cap = captions(cfg, key, meta)
    c = meta_creds(key)
    env_name = {"page_id": "META_PAGE_ID_", "token": "META_PAGE_TOKEN_", "ig_id": "META_IG_ID_"}
    for net, fn, needs in (("facebook", post_facebook_reel, ("page_id", "token")),
                           ("instagram", post_instagram_reel, ("ig_id", "token"))):
        if not want.get(net):
            continue
        missing = [env_name[n] + key.upper() for n in needs if not c[n]]
        if missing:
            notes.append(f"{net}: switched on but {', '.join(missing)} not in .env - skipped")
            continue
        try:
            log(f"  posting to {net}...")
            url = fn(cfg, key, folder / "video.mp4", cap[net])
            notes.append(f"{'📘' if net == 'facebook' else '📸'} {net.title()}: {url}")
        except Exception as e:
            notes.append(f"{net} post failed: {str(e)[:300]} - post the pack by hand")
    meta["crosspost"] = notes
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return notes
