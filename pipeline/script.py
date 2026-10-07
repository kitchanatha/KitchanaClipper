"""Step 1 - write a script with the Claude API."""
import json
import re
import time

import requests

from .common import env

API = "https://api.anthropic.com/v1/messages"

LANG = {"en": "English", "th": "Thai", "zh": "Simplified Chinese"}

PROMPT = """You write scripts for the YouTube Shorts channel "{name}".

Channel style:
{style}

Write ONE new script in {language}. Spoken length {s_lo}-{s_hi} seconds
(about {w_lo}-{w_hi} words in English, about {t_lo}-{t_hi} Thai characters, or about
{z_lo}-{z_hi} Chinese characters). Keep it tight: every sentence must earn its place.
It must be clearly different from these recent videos on the channel:
{recent}

Return ONLY a JSON object, no markdown, with these keys:
- "title": YouTube Shorts title, under 70 characters, in {language}
- "topic": 3-6 word summary in English (used to avoid repeats)
- "script": the full narration exactly as it should be spoken, including the hook as the first sentence. No stage directions, no emojis.
- "keywords": 5 short English search terms for vertical stock video that matches the mood and story, ordered to follow the script. Visual and concrete (e.g. "rain on window", "man counting cash").
- "description": 1-2 sentence YouTube description in {language}
- "hashtags": 3 extra relevant hashtags
"""


def ask_claude(cfg, prompt, max_tokens=8000, system=None, timeout=300, effort=None):
    """One Claude API call; returns the reply text.

    The model thinks before answering and its thinking counts toward max_tokens, so limits
    are generous (only tokens actually used are billed). A reply that still gets cut off is
    retried once with twice the room. effort ("low" / "medium" / "high") limits how much it
    thinks - lower suits writing tasks."""
    for attempt in range(2):
        data = _claude_call(cfg, prompt, max_tokens, system, timeout, effort)
        if data.get("stop_reason") != "max_tokens":
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        max_tokens = min(max_tokens * 2, 32000)
    raise RuntimeError("Claude reply was cut off (max_tokens) twice - try a smaller chunk")


def _claude_call(cfg, prompt, max_tokens, system, timeout, effort=None):
    body = {
        "model": cfg["claude"]["model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if effort:
        body["output_config"] = {"effort": effort}
    if system:
        body["system"] = system
    headers = {
        "x-api-key": env("ANTHROPIC_API_KEY"),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    for attempt in range(3):  # a stalled / dropped connection or an overloaded API: try again
        try:
            r = requests.post(API, headers=headers, json=body, timeout=(15, timeout))
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:500]}")
        return r.json()


def write_script(cfg, ch, recent_topics):
    recent = "\n".join(f"- {t}" for t in recent_topics[-60:]) or "- (none yet)"
    s_lo, s_hi = cfg["video"].get("script_seconds", [35, 50])
    prompt = PROMPT.format(
        name=ch["name"], style=ch["style"].strip(),
        language=LANG.get(ch.get("language", "en"), "English"), recent=recent,
        s_lo=s_lo, s_hi=s_hi, w_lo=round(s_lo * 2.5), w_hi=round(s_hi * 2.5),
        t_lo=round(s_lo * 7), t_hi=round(s_hi * 7), z_lo=round(s_lo * 4.5), z_hi=round(s_hi * 4.5),
    )
    text = ask_claude(cfg, prompt, max_tokens=8000)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Claude did not return JSON:\n{text[:500]}")
    data = json.loads(m.group(0))
    for k in ("title", "script", "keywords"):
        if not data.get(k):
            raise RuntimeError(f"Script is missing '{k}'")
    return data
