"""After a video is approved and uploaded to YouTube.

1. Cross-post pack (every video): output/crosspost/<channel>/<date>_<slug>/ with video.mp4 and
   caption_<lang>.txt - Facebook / Instagram caption and a shorter TikTok caption with hashtags,
   plus sources for news.
2. Per channel, opt-in in config.yaml (channels.<key>.crosspost):
   facebook  -> Reel on the channel's own Page (pipeline/facebook.py, tokens/facebook_<key>.json)
   instagram -> Reel on the linked Instagram account (same Page token + instagram.ig_id)
   Only after your Approve in Discord. Problems are reported, never fatal: the YouTube upload stands.
   Each platform is posted once per video (meta.json "platforms"); force=True posts again.
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
    if src:  # news: the full source lines from the YouTube description (rule: sources everywhere)
        links = [ln.strip() for ln in (meta.get("description") or "").split("\n") if "http" in ln]
        fb += "\n\n" + ("แหล่งข่าว:\n" + "\n".join(links) if links else src)
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

def channel_xp(cfg, key):
    """channels.<key>.crosspost -> {facebook: {...}, instagram: {...}, tiktok: {...}}."""
    return cfg["channels"][key].get("crosspost") or {}


def meta_creds(cfg, key):
    from .facebook import fb_settings, load_token
    t = load_token(key) or {}
    ig = channel_xp(cfg, key).get("instagram") or {}
    return {"page_id": fb_settings(cfg, key)["page_id"], "token": t.get("page_token", ""),
            "ig_id": str(ig.get("ig_id") or "")}


def _check(r, what):
    if r.status_code != 200:
        raise RuntimeError(f"{what}: Meta API {r.status_code} {r.text[:300]}")
    return r.json()


def post_instagram_reel(cfg, key, video, caption, wait_s=600):
    """Instagram Reel (resumable upload -> wait until processed -> publish). Returns the permalink."""
    from .facebook import graph_version, token_status
    st = token_status(cfg, key)
    if not st["ok"]:
        raise RuntimeError(st["problem"])
    ver = graph_version(cfg)
    c = meta_creds(cfg, key)
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


def _post_facebook(cfg, key, folder, cap, slot):
    from .facebook import publish_reel, video_problems
    bad = video_problems(folder / "video.mp4")
    if bad:
        return {"platform": "facebook", "ok": False, "skipped": True,
                "error": "not posted - " + "; ".join(bad)}
    url, note = publish_reel(cfg, key, folder / "video.mp4", cap["facebook"], slot)
    return {"platform": "facebook", "ok": True, "url": url, "note": note}


def _post_instagram(cfg, key, folder, cap, slot):
    if not meta_creds(cfg, key)["ig_id"]:
        return {"platform": "instagram", "ok": False, "skipped": True,
                "error": f"instagram.ig_id missing in channels.{key}.crosspost"}
    return {"platform": "instagram", "ok": True,
            "url": post_instagram_reel(cfg, key, folder / "video.mp4", cap["instagram"])}


def _post_tiktok(cfg, key, folder, cap, slot):
    from .tiktok import upload_draft, video_problems
    bad = video_problems(folder / "video.mp4")
    if bad:
        return {"platform": "tiktok", "ok": False, "skipped": True, "error": "not sent - " + "; ".join(bad)}
    pid, who = upload_draft(cfg, key, folder / "video.mp4")
    caption = cap["tiktok"] + "\nAI voice"  # drafts can't carry a caption: you paste this one
    return {"platform": "tiktok", "ok": True, "url": f"tiktok-draft:{pid}", "draft": True,
            "note": f"sent to TikTok drafts for {who} - open TikTok > Inbox to finish posting "
                    f"(switch on 'AI-generated content')", "caption": caption}


POSTERS = {"facebook": _post_facebook, "instagram": _post_instagram, "tiktok": _post_tiktok}
ICONS = {"facebook": "📘", "instagram": "📸", "tiktok": "🎵"}


def result_line(r):
    name = r["platform"].title()
    if r.get("ok") and r.get("draft"):
        cap = f"\nCaption to paste:\n```\n{r['caption']}\n```" if r.get("caption") else ""
        return f"{ICONS.get(r['platform'], '')} {name} 📥 {r.get('note', 'draft sent')}{cap}"
    if r.get("ok"):
        return f"{ICONS.get(r['platform'], '')} {name} ✅ {r.get('url', '')} {('(' + r['note'] + ')') if r.get('note') else ''}".strip()
    return f"{ICONS.get(r['platform'], '')} {name} ❌ {r.get('error', '')[:300]}"


def post_platforms(cfg, key, folder, slot=None, only=None, force=False, log=print):
    """Post an uploaded video to the channel's enabled platforms. Returns result dicts; never raises.
    only: list of platforms (e.g. ["facebook"]); force: post again even if already posted."""
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    xp = channel_xp(cfg, key)
    cap = captions(cfg, key, meta)
    done = meta.setdefault("platforms", {})
    results = []
    for net, fn in POSTERS.items():
        if only and net not in only:
            continue
        if not (xp.get(net) or {}).get("enabled"):
            if only:
                results.append({"platform": net, "ok": False, "skipped": True,
                                "error": f"switched off in config.yaml (channels.{key}.crosspost.{net})"})
            continue
        if done.get(net, {}).get("url") and not force:
            results.append({"platform": net, "ok": True, "url": done[net]["url"], "note": "already posted"})
            continue
        try:
            log(f"  posting to {net}...")
            r = fn(cfg, key, folder, cap, slot)
        except Exception as e:
            r = {"platform": net, "ok": False, "error": str(e)[:400]}
        results.append(r)
        if r.get("ok"):
            done[net] = {"url": r["url"], "note": r.get("note", ""), "at": datetime.now().isoformat(timespec="seconds")}
            _log_csv(cfg, key, meta, net, r["url"])
    meta["crosspost"] = [result_line(r) for r in results]
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def _log_csv(cfg, key, meta, platform, url):
    import run as core
    core.log_upload(cfg, key, meta, datetime.now(), url, platform)


def crosspost(cfg, key, folder, youtube_url=None, slot=None, log=print):
    """Pack + the channel's enabled platforms. Returns notes for Discord; never raises."""
    s = _settings(cfg)
    notes = []
    folder = Path(folder)
    try:
        if s.get("pack", True):
            pack = make_pack(cfg, key, folder, youtube_url)
            notes.append(f"📦 Cross-post pack (TikTok etc.): {pack}")
    except Exception as e:
        notes.append(f"📦 pack failed: {str(e)[:200]}")
    try:
        notes += [result_line(r) for r in post_platforms(cfg, key, folder, slot, log=log)]
    except Exception as e:
        notes.append(f"cross-post failed: {str(e)[:300]}")
    return notes


def find_uploaded(cfg, key, which="last"):
    """An uploaded video folder of this channel: 'last' or a (partial) folder name."""
    import run as core
    base = core.dirs(cfg)["uploaded"] / key
    folders = sorted((p for p in base.glob("*") if (p / "meta.json").exists()), key=lambda p: p.name)
    if not folders:
        return None
    if which in (None, "", "last"):
        return folders[-1]
    hits = [p for p in folders if which in p.name]
    return hits[-1] if hits else None
