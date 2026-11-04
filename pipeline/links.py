"""Cross-platform links (README section 14) - grow the audience between platforms.

config.yaml, per channel (fill in only what exists; empty ones are left out everywhere):
  links:
    youtube: "https://www.youtube.com/@..."
    facebook: "https://www.facebook.com/..."
    tiktok: "https://www.tiktok.com/@..."
    sister_channels: [{name: "...", url: "https://..."}]
    shorts_line: "..."        # optional: replaces the default plain-text line in Shorts
    pinned_question: "..."    # optional: the question that opens the pinned comment

Only links set here are ever used - never other people's channels or affiliate links.

Where they go:
  long videos (stories, news long recaps): description ends with a "Follow us" block (chapters and
      sources come first, links last); YouTube link carries ?sub_confirmation=1. A pinned-comment
      text is posted in Discord to paste (the API can't pin comments).
  Shorts: links aren't clickable there, so only a plain-text line ("ดูฉบับเต็มบนช่อง ...").
  Facebook / Instagram / TikTok captions: a short line back to YouTube - with the URL on Facebook,
      text only on TikTok and Instagram (captions there aren't clickable).
"""
import requests

FOLLOW = {"th": "ติดตามเราได้ที่:", "en": "Follow us:", "zh": "关注我们:"}
SISTERS = {"th": "ช่องในเครือ:", "en": "Also from us:", "zh": "姊妹频道:"}
SHORTS = {"th": "ดูฉบับเต็มบนช่อง {name}", "en": "Full version on our channel: {name}",
          "zh": "完整版请看我们的频道: {name}"}
BACK = {"th": "ฉบับเต็ม{mins}บน YouTube: {name}", "en": "Full video{mins} on YouTube: {name}",
        "zh": "完整版{mins}在 YouTube: {name}"}
MINS = {"th": " {n} นาที", "en": " ({n} min)", "zh": " ({n} 分钟)"}
ASK = {"th": "ติดตาม Facebook / TikTok ของเรา:", "en": "Follow us on Facebook / TikTok:",
       "zh": "关注我们的 Facebook / TikTok:"}
QUESTION = {
    "news": {"th": "อยากให้เราสรุปเรื่องไหนต่อ? คอมเมนต์บอกกันหน่อย 👇",
             "en": "Which story should we break down next? Tell us below 👇"},
    "story": {"th": "ฉากไหนที่คุณชอบที่สุด? คอมเมนต์บอกกันหน่อย 👇",
              "en": "Which moment stayed with you the most? Tell us below 👇",
              "zh": "你最喜欢哪个片段？在评论区告诉我们 👇"},
}
NETS = (("youtube", "YouTube"), ("facebook", "Facebook"), ("tiktok", "TikTok"))


def _lang(ch):
    lang = ch.get("language", "en")
    return lang if lang in FOLLOW else "en"


def channel_links(cfg, key):
    """{youtube, facebook, tiktok, sister_channels} with empty entries removed."""
    raw = cfg["channels"][key].get("links") or {}
    out = {k: str(raw.get(k) or "").strip() for k, _ in NETS if str(raw.get(k) or "").strip()}
    sis = [{"name": str(s.get("name", "")).strip(), "url": str(s.get("url", "")).strip()}
           for s in raw.get("sister_channels") or [] if isinstance(s, dict) and str(s.get("url") or "").strip()]
    if sis:
        out["sister_channels"] = sis
    return out


def subscribe_url(url):
    if "sub_confirmation=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "sub_confirmation=1"


def is_long(meta):
    return meta.get("kind") in ("story", "clip_compilation") or (meta.get("kind") == "news" and meta.get("variant") == "long")


def follow_block(cfg, key):
    """The 'Follow us' block for long-video descriptions ('' when no links are set)."""
    ch, links = cfg["channels"][key], channel_links(cfg, key)
    lang = _lang(ch)
    lines = [f"{label}: {subscribe_url(links[k]) if k == 'youtube' else links[k]}"
             for k, label in NETS if k in links]
    if not lines and not links.get("sister_channels"):
        return ""
    block = FOLLOW[lang] + "\n" + "\n".join(lines) if lines else ""
    if links.get("sister_channels"):
        block += ("\n\n" if block else "") + SISTERS[lang] + "\n" + "\n".join(
            f"{s['name']}: {s['url']}" if s["name"] else s["url"] for s in links["sister_channels"])
    return block


def shorts_line(cfg, key):
    ch = cfg["channels"][key]
    custom = str((ch.get("links") or {}).get("shorts_line") or "").strip()
    return custom or SHORTS[_lang(ch)].format(name=ch["name"])


def with_links(cfg, key, meta, desc):
    """The YouTube description with this channel's links added (once): long videos get the
    follow block at the very end, Shorts a plain-text line."""
    extra = follow_block(cfg, key) if is_long(meta) else shorts_line(cfg, key)
    if not extra or extra in desc:
        return desc
    return (desc.rstrip() + "\n\n" + extra) if desc.strip() else extra


def back_to_youtube(cfg, key, meta, with_url=True, long_url=None, long_sec=None):
    """One caption line pointing back to YouTube. with_url=False for TikTok / Instagram."""
    ch = cfg["channels"][key]
    lang = _lang(ch)
    mins = MINS[lang].format(n=max(1, round(long_sec / 60))) if long_sec and long_sec >= 120 else ""
    line = BACK[lang].format(mins=mins, name=ch["name"])
    if not with_url:
        return line
    url = long_url or channel_links(cfg, key).get("youtube") or (
        f"https://youtu.be/{meta['video_id']}" if meta.get("video_id") else "")
    return line + (f"\n{url}" if url else "")


def pinned_comment(cfg, key, meta):
    """Text to paste + pin under a long video: a question to start comments, then the links."""
    ch, links = cfg["channels"][key], channel_links(cfg, key)
    lang = _lang(ch)
    custom = str((ch.get("links") or {}).get("pinned_question") or "").strip()
    kind = "news" if ch.get("type") == "news" else "story"
    q = meta.get("pinned_question") or custom or QUESTION[kind].get(lang) or QUESTION[kind]["en"]
    social = [f"{label}: {links[k]}" for k, label in NETS if k in links and k != "youtube"]
    return q + ("\n\n" + ASK[lang] + "\n" + "\n".join(social) if social else "")


def validate_links(cfg):
    """Config mistakes in channels.<key>.links. [] = fine."""
    problems = []
    for key, ch in cfg.get("channels", {}).items():
        raw = ch.get("links")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            problems.append(f"{key}: links must be a mapping")
            continue
        for k in sorted(set(raw) - {"youtube", "facebook", "tiktok", "sister_channels", "shorts_line",
                                    "pinned_question"}):
            problems.append(f"{key}: unknown links entry '{k}'")
        for k, _ in NETS:
            u = str(raw.get(k) or "").strip()
            if u and not u.startswith("https://"):
                problems.append(f"{key}: links.{k} must start with https://")
        if raw.get("sister_channels") is not None and not isinstance(raw.get("sister_channels"), list):
            problems.append(f"{key}: links.sister_channels must be a list of {{name, url}}")
        for s in raw.get("sister_channels") or []:
            u = str((s or {}).get("url") or "").strip() if isinstance(s, dict) else ""
            if not u.startswith("https://"):
                problems.append(f"{key}: every sister_channels entry needs name + an https:// url")
    return problems


UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) link-check",
      "Accept-Language": "en-US,en;q=0.8"}


def check_url(url, get=requests.get):
    """(ok, detail) - ok when the final response is HTTP 200."""
    try:
        r = get(url, headers=UA, timeout=12, allow_redirects=True, stream=True)
        code = r.status_code
        r.close()
        return code == 200, f"HTTP {code}"
    except requests.RequestException as e:
        return False, type(e).__name__


def link_report(cfg, key, get=requests.get):
    """Lines for /links: every configured link and whether it answers 200."""
    links = channel_links(cfg, key)
    items = [(label, links[k]) for k, label in NETS if k in links]
    items += [(f"Sister: {s['name'] or s['url']}", s["url"]) for s in links.get("sister_channels", [])]
    lines = []
    for label, url in items:
        ok, detail = check_url(url, get)
        lines.append(f"{'✅' if ok else '❌'} {label}: <{url}> ({detail})")
    missing = [label for k, label in NETS if k not in links]
    if missing:
        lines.append("➖ not set: " + ", ".join(missing) + f" (config.yaml channels.{key}.links)")
    return lines
