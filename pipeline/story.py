"""Story mode - checking rights and preparing texts (no audio cost).

  story-check  validate story.yaml rights + show length / cost estimates
  story-prep   clean source.txt, translate to Thai + Chinese with Claude (running glossary),
               plan scenes, write hook / title / description / tags / trailer scripts
"""
import json
import re
from pathlib import Path

import yaml

from .common import ROOT
from .storylib import (LANG_NAMES, RightsError, clean_text, group_for_translation, paragraphs,
                       validate_rights, word_count)

# rough narration speeds, for length estimates only
SPEED = {"en": ("words", 150), "th": ("chars", 620), "zh": ("chars", 250)}

TRAILER_LEN = {
    "en": "about 110-125 words",
    "th": "about 400-480 Thai characters",
    "zh": "about 200-240 Chinese characters",
}
TRAILER_END = {
    "en": "Full story on the channel.",
    "th": "ฟังเรื่องเต็มได้ที่ช่องนะ",
    "zh": "完整故事，请到频道收听。",
}

TRANSLATE_SYSTEM = """You are an award-winning literary translator who prepares classic fiction \
for audiobook narration. You translate from the English original only - never reuse or imitate an \
existing published translation. Your translations are faithful to the meaning, tone and period \
atmosphere of the original, and read naturally aloud in the target language."""

TRANSLATE_PROMPT = """Translate the next part of "{title}" by {author} into {language}.

Style:
{style}

Rules:
- Translate every paragraph. Output EXACTLY {n} paragraphs, separated by one blank line, in the \
same order. Never merge or split paragraphs.
- Keep chapter headings such as "I." or "II." exactly as they are.
- Use the glossary for names, places and recurring terms so they stay consistent. Add new \
names/terms you introduce.
- No notes, no explanations, no titles of your own.

Glossary so far (English -> {language}):
{glossary}

End of the previous part, for context (do NOT translate again):
{context}

Reply in exactly this format:
<translation>
...the {n} translated paragraphs...
</translation>
<glossary>
{{"English term": "translation", ...only NEW terms from this part...}}
</glossary>

Text to translate:
{text}"""

STYLE = {
    "th": "Natural, flowing Thai that sounds good read aloud by a storyteller. Literary but easy to "
          "follow by ear; avoid stiff, word-for-word phrasing. Transliterate English names "
          "consistently (e.g. Mr. White -> มิสเตอร์ไวต์). Use dialogue particles that suit each "
          "character.",
    "zh": "Natural, literary Simplified Chinese (Mainland conventions) that sounds good read "
          "aloud. Flowing spoken-friendly sentences; avoid translationese. Use standard "
          "transliterations for names and keep them consistent.",
}

PREP_PROMPT = """You are preparing a narrated video of the story below for the YouTube channel \
"{channel}". Language: {language}.
Story: "{title}" by {author}, first published {year}.

Write everything in {language}. Return ONLY a JSON object with these keys:
- "story_title": the story's title in {language} (for English, the original title)
- "author_display": the author's name written for {language} readers
- "title": YouTube title under 90 characters with the story title and author plus a calm hook \
about what kind of story it is. No clickbait, no spoilers.
- "hook": 2-3 short spoken sentences that open the video and make people want to listen, \
without spoilers. It is narrated before the title card.
- "description": 2-3 sentences about the story for the YouTube description, no spoilers.
- "tags": 12 YouTube tags ({language}; a few English ones are fine)
- "trailers": 3 objects for 45-second vertical Shorts. Each one retells a REAL suspenseful or \
cliffhanger moment from the text (stay close to the text, do not invent events) and stops before \
the resolution. Keys: "title" (under 70 characters), "script" (the narration, {trailer_len}, \
ending exactly with "{ending}"), "keywords" (5 short ENGLISH search terms for vertical stock \
video that fits the moment, no specific people's faces), "description" (one sentence).

<story>
{text}
</story>"""

SCENES_PROMPT = """Below is a story split into numbered paragraphs. Divide it into {n_min}-{n_max} \
scenes for a narrated video. Each scene should cover at least {min_words} words and start where \
the setting or mood changes. Always start a new scene at chapter headings such as "II.".

Return ONLY JSON: {{"scenes": [{{"start_paragraph": 0, "title_en": "...", "title_th": "...", \
"title_zh": "...", "keywords": ["...", "...", "..."]}}]}}
- title_*: 2-5 word chapter titles without spoilers, in English, Thai and Simplified Chinese
- keywords: 3 short English search terms for calm, atmospheric LANDSCAPE stock video matching the \
scene's setting and mood (places, weather, light, objects - no faces, no text on screen)
- the first scene starts at paragraph 0; scenes in order

{numbered}"""


# ---------------------------------------------------------------- paths & loading

def stories_root(cfg):
    return ROOT / cfg["story"].get("stories_dir", "stories")


def story_path(cfg, slug):
    d = stories_root(cfg) / slug
    if not d.is_dir():
        raise SystemExit(f"No story folder {d}. Create it with source.txt and story.yaml.")
    return d


def text_file(d, lang):
    return d / ("clean_en.txt" if lang == "en" else f"translation_{lang}.txt")


def load_story(cfg, slug):
    """Load story.yaml and enforce the copyright rules. Raises SystemExit with the reasons."""
    d = story_path(cfg, slug)
    meta_file = d / "story.yaml"
    if not meta_file.exists():
        raise SystemExit(f"{meta_file} is missing - every story needs story.yaml (see README).")
    if not (d / "source.txt").exists():
        raise SystemExit(f"{d / 'source.txt'} is missing.")
    meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
    try:
        validate_rights(meta, d)
    except RightsError as e:
        raise SystemExit(f"{slug}: {e}\nRefusing to process this story.")
    return d, meta


def story_langs(cfg, only=None):
    langs = dict(cfg["story"]["channels"])
    if only:
        want = [x.strip() for x in only.split(",") if x.strip()]
        bad = [x for x in want if x not in langs]
        if bad:
            raise SystemExit(f"Unknown language(s) {bad}. Configured: {', '.join(langs)}")
        langs = {k: langs[k] for k in want}
    return langs


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_from(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Claude did not return JSON:\n{text[:400]}")
    return json.loads(m.group(0))


# ---------------------------------------------------------------- check

def estimate_minutes(text, lang):
    unit, rate = SPEED[lang]
    n = word_count(text, lang) if unit == "words" else sum(1 for c in text if not c.isspace())
    return n / rate


def check(cfg, slug):
    d, meta = load_story(cfg, slug)
    print(f"Rights OK: {meta['title']} by {meta['author']} ({meta.get('rights')})")
    raw = (d / "source.txt").read_text(encoding="utf-8")
    clean = clean_text(raw)
    if "gutenberg" in clean.lower():
        print("WARNING: 'Gutenberg' still appears in the cleaned text - check source.txt")
    lo, hi = cfg["story"].get("target_minutes", [15, 60])
    cpc = float(cfg["story"].get("credits_per_char", 1.0))
    print(f"Source: {len(paragraphs(clean))} paragraphs, {word_count(clean, 'en')} words")
    for lang, key in story_langs(cfg).items():
        f = text_file(d, lang)
        text = f.read_text(encoding="utf-8") if f.exists() else (clean if lang == "en" else None)
        if text is None:
            print(f"  {lang} ({key}): not translated yet - run story-prep")
            continue
        mins = estimate_minutes(text, lang)
        chars = len(text)
        note = "" if lo <= mins <= hi else f"  <- outside target {lo}-{hi} min"
        print(f"  {lang} ({key}): {chars:,} characters, ~{mins:.0f} min narration, "
              f"~{chars * cpc:,.0f} ElevenLabs credits{note}")
    return d, meta


# ---------------------------------------------------------------- prep

def prep(cfg, slug, langs=None, force=False, log=print):
    from .script import ask_claude

    d, meta = load_story(cfg, slug)
    langs = story_langs(cfg, langs)
    clean_f = d / "clean_en.txt"
    if force or not clean_f.exists():
        clean_f.write_text(clean_text((d / "source.txt").read_text(encoding="utf-8")) + "\n",
                           encoding="utf-8")
        log(f"cleaned source -> {clean_f.name}")
    en_text = clean_f.read_text(encoding="utf-8")

    for lang in langs:
        if lang != "en" and (force or not text_file(d, lang).exists()):
            translate(cfg, meta, d, en_text, lang, ask_claude, log)

    scenes_f = d / "scenes.json"
    if force or not scenes_f.exists():
        log("planning scenes...")
        write_json(scenes_f, plan_scenes(cfg, en_text, ask_claude))

    for lang, key in langs.items():
        out = d / f"prep_{lang}.json"
        if force or not out.exists():
            log(f"[{lang}] writing title, hook, description and trailer scripts...")
            text = text_file(d, lang).read_text(encoding="utf-8")
            write_json(out, write_prep(cfg, meta, cfg["channels"][key], lang, text, ask_claude))
    log(f"Prep done. Read and edit the translations in {d} before story-build.")
    return d


def translate(cfg, meta, d, en_text, lang, ask, log=print):
    """Translate in chunks with a running glossary. Progress is saved after every chunk,
    so a failed run continues where it stopped."""
    groups = group_for_translation(en_text, int(cfg["story"].get("translation_words", 1500)))
    gloss_f, part_f = d / f"glossary_{lang}.json", d / f".translation_{lang}.partial.json"
    glossary = read_json(gloss_f) if gloss_f.exists() else {}
    done = read_json(part_f) if part_f.exists() else []
    for i in range(len(done), len(groups)):
        src = groups[i]
        log(f"[{lang}] translating part {i + 1}/{len(groups)} ({sum(len(p.split()) for p in src)} words)...")
        context = ""
        if i:
            context = "\n\n".join(groups[i - 1][-2:]) + "\n---\n" + "\n\n".join(done[i - 1][-2:])
        prompt = TRANSLATE_PROMPT.format(
            title=meta["title"], author=meta["author"], language=LANG_NAMES[lang],
            style=STYLE.get(lang, ""), n=len(src),
            glossary=json.dumps(glossary, ensure_ascii=False, indent=0) if glossary else "(empty)",
            context=context or "(this is the beginning)", text="\n\n".join(src))
        for attempt in range(2):
            reply = ask(cfg, prompt, max_tokens=12000, system=TRANSLATE_SYSTEM, timeout=600)
            m = re.search(r"<translation>\s*(.*?)\s*</translation>", reply, re.S)
            if not m:
                raise RuntimeError(f"Translation reply had no <translation> block:\n{reply[:300]}")
            out = paragraphs(m.group(1))
            if len(out) == len(src):
                break
            log(f"[{lang}]   got {len(out)} paragraphs, expected {len(src)} - "
                + ("retrying" if attempt == 0 else "keeping it (scene timing falls back to position)"))
        g = re.search(r"<glossary>\s*(\{.*?\})\s*</glossary>", reply, re.S)
        if g:
            try:
                glossary.update(json.loads(g.group(1)))
            except json.JSONDecodeError:
                log(f"[{lang}]   glossary block was not valid JSON - skipped")
        done.append(out)
        write_json(part_f, done)
        write_json(gloss_f, glossary)
    text_file(d, lang).write_text("\n\n".join(p for g in done for p in g) + "\n", encoding="utf-8")
    part_f.unlink(missing_ok=True)
    log(f"[{lang}] translation -> {text_file(d, lang).name} ({len(glossary)} glossary terms)")


def plan_scenes(cfg, en_text, ask):
    paras = paragraphs(en_text)
    words = word_count(en_text, "en")
    n_min, n_max = max(3, min(words // 900, 10)), max(4, min(words // 450, 14))
    numbered = "\n".join(f"[{i}] {p[:400]}" for i, p in enumerate(paras))
    reply = ask(cfg, SCENES_PROMPT.format(n_min=n_min, n_max=n_max, min_words=max(200, words // (n_max + 2)),
                                          numbered=numbered), max_tokens=4000, timeout=300)
    scenes = _json_from(reply).get("scenes") or []
    clean, seen = [], set()
    for s in sorted(scenes, key=lambda s: int(s.get("start_paragraph", 0))):
        sp = max(0, min(int(s.get("start_paragraph", 0)), len(paras) - 1))
        if sp in seen:
            continue
        seen.add(sp)
        clean.append({"start_paragraph": sp, "start_fraction": round(sp / len(paras), 4),
                      "title": {k: s.get(f"title_{k}") or s.get("title_en") or f"Scene {len(clean) + 1}"
                                for k in ("en", "th", "zh")},
                      "keywords": [k for k in s.get("keywords", []) if k][:4] or ["candle light"]})
    if not clean:
        raise RuntimeError("Scene planning returned no scenes")
    clean[0]["start_paragraph"], clean[0]["start_fraction"] = 0, 0.0
    return {"paragraphs": len(paras), "scenes": clean}


def write_prep(cfg, meta, ch, lang, text, ask):
    reply = ask(cfg, PREP_PROMPT.format(
        channel=ch["name"], language=LANG_NAMES[lang], title=meta["title"], author=meta["author"],
        year=meta.get("year_published", "unknown"), trailer_len=TRAILER_LEN[lang],
        ending=TRAILER_END[lang], text=text), max_tokens=6000, timeout=600)
    data = _json_from(reply)
    for k in ("story_title", "author_display", "title", "hook", "description", "tags", "trailers"):
        if not data.get(k):
            raise RuntimeError(f"[{lang}] prep is missing '{k}'")
    for t in data["trailers"]:
        if TRAILER_END[lang] not in t.get("script", ""):
            t["script"] = t.get("script", "").rstrip() + " " + TRAILER_END[lang]
    return data
