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
- "hook_text": 2-6 words shown HUGE on screen from the very first frame, before anyone decides to scroll: the most surprising part as a question or a punchy quote-like line (e.g. "Your eye breathes air?", "Price tags lie."). Same language as the script, no hashtags, no emojis.
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


HOOKS_EXTRA = """
HOOK RULE (people swipe away in the first 2 seconds): do NOT put the opening line in "script".
Instead return:
- "hook_options": 3 DIFFERENT possible first sentences. Each 5-10 words, spoken in under 2
  seconds, creating an open question the viewer needs answered (e.g. "My neighbor left me a key
  and a warning."). No scene-setting, no greeting, no "Did you know".
- "body": the rest of the narration, which must continue naturally after ANY of the 3 hooks.
"""

SCORE_PROMPT = """Score these opening lines for a YouTube Short ({name}). People decide in 2 seconds.
For each: curiosity 1-10 (how badly you need to know what happens next), clarity 1-10 (instantly
understood, no confusion), and its word count.

{hooks}

Then pick the ONE that will stop the most people from swiping (must be 10 words or fewer).

Reply ONLY JSON: {{"scores": [{{"curiosity": 0, "clarity": 0, "words": 0}}], "best": 1, "why": "one short sentence on why the best one wins"}}"""


def choose_hook(options, scores, best=None):
    """Index of the hook to use: Claude's pick ("best", 1-based) if it is 10 words or fewer
    (counted here, not trusted from the model); else highest curiosity + clarity among those."""
    ok = [i for i, h in enumerate(options) if len(h.split()) <= 10] or list(range(len(options)))
    if isinstance(best, int) and best - 1 in ok:
        return best - 1
    return max(ok, key=lambda i: (scores[i].get("curiosity", 0) + scores[i].get("clarity", 0)
                                  if i < len(scores) else 0, -len(options[i].split())))


def pick_hook(cfg, ch, options):
    """Claude scores the 3 hooks; returns (best, details for the Discord card)."""
    text = ask_claude(cfg, SCORE_PROMPT.format(
        name=ch["name"], hooks="\n".join(f"{i + 1}. {h}" for i, h in enumerate(options))),
        max_tokens=4000, effort="low")
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    scores = data.get("scores") or []
    best = choose_hook(options, scores, data.get("best"))
    return options[best], {"options": options, "scores": scores, "chosen": best,
                           "why": data.get("why", "")}


def write_script(cfg, ch, recent_topics, idea=None):
    """idea: a topic picked by the owner (e.g. from the popular-video ideas) - else Claude picks.
    Channels with hook_options: true get 3 opening lines, scored, best one spoken first."""
    recent = "\n".join(f"- {t}" for t in recent_topics[-60:]) or "- (none yet)"
    s_lo, s_hi = ch.get("script_seconds") or cfg["video"].get("script_seconds", [35, 50])
    prompt = PROMPT.format(
        name=ch["name"], style=ch["style"].strip(),
        language=LANG.get(ch.get("language", "en"), "English"), recent=recent,
        s_lo=s_lo, s_hi=s_hi, w_lo=round(s_lo * 2.5), w_hi=round(s_hi * 2.5),
        t_lo=round(s_lo * 7), t_hi=round(s_hi * 7), z_lo=round(s_lo * 4.5), z_hi=round(s_hi * 4.5),
    )
    if idea:
        prompt += (f"\nThe owner picked the topic: {idea.get('idea')}\n"
                   + (f"Suggested first line (improve it if you can): {idea['hook']}\n"
                      if idea.get("hook") else "")
                   + "Write the script about exactly this topic.\n")
    hooks = bool(ch.get("hook_options"))
    if hooks:
        prompt += HOOKS_EXTRA
    text = ask_claude(cfg, prompt, max_tokens=8000)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Claude did not return JSON:\n{text[:500]}")
    data = json.loads(m.group(0))
    if hooks:
        opts = [str(h).strip() for h in data.get("hook_options") or [] if str(h).strip()][:3]
        if not opts or not data.get("body"):
            raise RuntimeError("Script is missing 'hook_options' / 'body'")
        best, data["hook_choice"] = pick_hook(cfg, ch, opts)
        data["script"] = f"{best} {data.pop('body').strip()}"
        if ch.get("hook_card") == "sentence":  # on screen: the spoken hook itself
            data["hook_text"] = best
    for k in ("title", "script", "keywords"):
        if not data.get(k):
            raise RuntimeError(f"Script is missing '{k}'")
    return data
