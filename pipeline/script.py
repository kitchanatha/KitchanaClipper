"""Step 1 - write a script with the Claude API."""
import json
import re

import requests

from .common import env

API = "https://api.anthropic.com/v1/messages"

LANG = {"en": "English", "th": "Thai"}

PROMPT = """You write scripts for the YouTube Shorts channel "{name}".

Channel style:
{style}

Write ONE new script in {language}. Spoken length 35-50 seconds
(about 90-130 words in English, or about 250-350 Thai characters).
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


def write_script(cfg, ch, recent_topics):
    recent = "\n".join(f"- {t}" for t in recent_topics[-60:]) or "- (none yet)"
    prompt = PROMPT.format(
        name=ch["name"], style=ch["style"].strip(),
        language=LANG.get(ch.get("language", "en"), "English"), recent=recent,
    )
    r = requests.post(
        API,
        headers={
            "x-api-key": env("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": cfg["claude"]["model"],
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:500]}")
    text = "".join(b.get("text", "") for b in r.json()["content"])
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Claude did not return JSON:\n{text[:500]}")
    data = json.loads(m.group(0))
    for k in ("title", "script", "keywords"):
        if not data.get(k):
            raise RuntimeError(f"Script is missing '{k}'")
    return data
