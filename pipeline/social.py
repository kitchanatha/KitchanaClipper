"""Facebook + Instagram captions for every video, written by Claude after the video is made.

Saved in the video's meta.json under "social" and as social.txt, and offered on the Discord
review card ("Facebook caption" / "Instagram caption" buttons).
"""
import json
import re
from pathlib import Path

LANG = {"en": "English", "th": "Thai", "zh": "Simplified Chinese"}

PROMPT = """Write social media captions for a vertical video we already posted on YouTube, to \
re-post on Facebook (a Page) and Instagram (Reels). Language: {language}.

Channel: {channel}
Video title: {title}
Video type: {kind}
Narration / description:
{text}
{extra}
Rules for both captions:
- Do NOT spoil the twist or ending. Tease it.
- No links and no URLs (they are not clickable / they reduce reach).
- Say it is AI-narrated (e.g. "AI-narrated" / "AI voice") on its own short line near the end.
- If the story is fiction, say so ("Fictional story"). If a credit line is given above, include it word for word.
- {handle_rule}
- 1-2 fitting emoji at most per paragraph. Natural, not spammy.
- Only ONE follow prompt per caption. Separate paragraphs with a blank line.

Facebook: a hook line, 1-2 short lines that build curiosity, then ONE question that invites \
comments, then the disclosure line, then 4 hashtags.
Instagram: the FIRST line is the hook (only it shows before "more"), then 1-2 short lines, a \
follow / save / share prompt, the disclosure line, then 8-9 hashtags on the last line.

Return ONLY JSON: {{"facebook": "...", "instagram": "..."}} (use \\n for line breaks)."""


def handle_for(cfg, key):
    return ((cfg.get("social") or {}).get("handles") or {}).get(key, "").strip()


def write_social(cfg, key, meta, ask=None):
    """Returns {"facebook": ..., "instagram": ...}."""
    if ask is None:
        from .script import ask_claude as ask
    ch = cfg["channels"][key]
    kind = {"story": "long narrated story video (16:9)", "trailer": "Shorts trailer for a long story",
            }.get(meta.get("kind"), "YouTube Short")
    extra = ""
    if ch.get("description_footer") and ch.get("type") != "story":
        extra += (f"Background about the channel (do NOT copy it word for word): "
                  f"{ch['description_footer']}\n")
    credit = [line.strip() for line in meta.get("description", "").split("\n")
              if line.strip().startswith(("Story:", "Author:", "First published:", "Public domain",
                                          "Narrated with", "Translated by"))]
    if credit:  # story videos: keep the public-domain credit
        extra += "Credit line to include: " + " · ".join(credit) + "\n"
    handle = handle_for(cfg, key)
    handle_rule = (f"Our Instagram handle is {handle}; use it in the follow prompt." if handle else
                   "Don't invent an @handle - say 'our page' / 'our profile' instead.")
    text = meta.get("script") or meta.get("description", "")
    reply = ask(cfg, PROMPT.format(language=LANG.get(ch.get("language", "en"), "English"),
                                   channel=ch["name"], title=meta.get("title", ""), kind=kind,
                                   text=text[:3000], extra=extra, handle_rule=handle_rule),
                max_tokens=8000, timeout=300)
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        raise RuntimeError("Claude did not return caption JSON")
    data = json.loads(m.group(0))
    if not data.get("facebook") or not data.get("instagram"):
        raise RuntimeError("caption JSON is missing facebook/instagram")
    return {"facebook": data["facebook"].strip(), "instagram": data["instagram"].strip()}


def save_social(folder, social):
    folder = Path(folder)
    f = folder / "meta.json"
    meta = json.loads(f.read_text(encoding="utf-8"))
    meta["social"] = social
    f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "social.txt").write_text("FACEBOOK\n\n" + social["facebook"] + "\n\n\nINSTAGRAM\n\n"
                                       + social["instagram"] + "\n", encoding="utf-8")


def add_social(cfg, key, folder, log=print):
    """Write + save captions for a finished video. Never fails the video: errors are logged."""
    if not (cfg.get("social") or {}).get("enabled", True):
        return None
    try:
        meta = json.loads((Path(folder) / "meta.json").read_text(encoding="utf-8"))
        social = write_social(cfg, key, meta)
        save_social(folder, social)
        return social
    except Exception as e:
        log(f"  (social captions skipped: {str(e)[:150]})")
        return None


def ensure_social(cfg, key, folder):
    """Captions for the review card buttons: existing ones, or write them now (older videos)."""
    meta = json.loads((Path(folder) / "meta.json").read_text(encoding="utf-8"))
    if meta.get("social"):
        return meta["social"]
    social = write_social(cfg, key, meta)
    save_social(folder, social)
    return social
