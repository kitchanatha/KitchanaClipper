"""Story mode - build: narrate + render the long video, thumbnail, subtitles and trailers.

Output per language: output/review/<channel>/<slug>/ (video.mp4, thumbnail.jpg,
subtitles.srt, meta.json) and output/review/<channel>/<slug>-trailer-N/ (Shorts).
"""
import random
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import longvideo as lv
from . import narrate
from .assemble import duration
from .story import load_story, read_json, story_langs, text_file, write_json
from .storylib import (build_cues, chapters_text, credits_text, is_heading, paragraphs,
                       spoken_heading, split_for_narration, to_srt)
from .voice import check_voice_id

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
LANG_LABEL = {"en": "English", "th": "ภาษาไทย", "zh": "中文"}


def _norm(p):
    return re.sub(r"[ \t]+", " ", p).strip()


def narration_units(cfg, lang, text, hook):
    st = cfg["story"]
    paras = [_norm(spoken_heading(p, lang) if is_heading(p) else p) for p in paragraphs(text)]
    full = "\n\n".join(paras)
    chunks = split_for_narration(full, lang, int(st.get("narration_chunk_chars", 2500)))
    pause = float(st.get("paragraph_pause", 0.6))
    units = [{"text": _norm(hook), "kind": "hook",
              "pause_after": 1.0 + float(st.get("intro_card_seconds", 4))}]
    units += [{"text": c, "kind": "story", "pause_after": pause} for c in chunks]
    units[-1]["pause_after"] = 1.0 + float(st.get("outro_card_seconds", 6))
    return units, paras, full


def paragraph_times(units, paras, full):
    """Start time of every paragraph, from the recorded chunk timings."""
    gtimes, pos = [None] * len(full), 0
    for u in units:
        if u["kind"] != "story":
            continue
        idx = full.find(u["text"], pos)
        idx = pos if idx < 0 else idx
        for k, t in enumerate(u["times"]):
            if idx + k < len(full):
                gtimes[idx + k] = t
        pos = idx + len(u["text"])
    last = (0.0, 0.0)
    for i, t in enumerate(gtimes):  # forward-fill separators
        gtimes[i] = last = t or last
    starts, idx = [], 0
    for p in paras:
        idx = full.find(p, idx)
        starts.append(gtimes[max(idx, 0)][0] if gtimes else 0.0)
        idx += len(p)
    return starts


def scene_footage(cfg, d, scenes, log):
    """Download (once per story, shared by all languages) landscape clips for every scene."""
    from .footage import get_footage

    st = cfg["story"]
    clip_s = float(st["clip_seconds"])
    per = int(st.get("clips_per_scene", 6))
    used, out = [], []
    for i, sc in enumerate(scenes):
        folder = d / "cache" / "footage" / f"scene_{i:02d}"
        man = folder / "clips.json"
        if man.exists():
            m = read_json(man)
            if m.get("keywords") == sc["keywords"] and all((folder / n).exists() for n, _ in m["clips"]):
                out.append([(str(folder / n), length) for n, length in m["clips"]])
                used += m.get("ids", [])
                continue
        log(f"  footage for scene {i + 1}/{len(scenes)}: {', '.join(sc['keywords'])}")
        shutil.rmtree(folder, ignore_errors=True)
        paths, ids = get_footage(sc["keywords"], "", (per - 1) * clip_s, clip_s, folder, used,
                                 orientation="landscape")
        clips = [(Path(p).name, round(duration(p), 2)) for p in paths]
        write_json(man, {"keywords": sc["keywords"], "clips": clips, "ids": ids})
        used += ids
        out.append([(str(folder / n), length) for n, length in clips])
    return out


def pick_music(cfg, log):
    folder = Path(cfg["_root"]) / cfg["story"].get("music_dir", "music")
    tracks = [p for p in folder.glob("*") if p.suffix.lower() in AUDIO_EXT] if folder.exists() else []
    if not tracks:
        log(f"WARNING: no music in {folder} - building without background music.")
        return None
    return random.choice(tracks)


def describe(cfg, meta, ch, key, lang, prep, chapters):
    urls = cfg["story"].get("channel_urls") or {}
    links = [f"{LANG_LABEL[l]}: {urls[k]}" for l, k in cfg["story"]["channels"].items()
             if k != key and urls.get(k)]
    footer = ch.get("description_footer", "") + ("\n" + "\n".join(links) if links else "")
    parts = [prep["hook"], prep["description"], chapters, credits_text(meta, lang), footer,
             " ".join(ch.get("hashtags", []))]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def tags_for(prep, ch):
    tags, total = [], 0
    for t in list(prep.get("tags", [])) + [h.lstrip("#") for h in ch.get("hashtags", [])]:
        t = str(t).strip().lstrip("#")
        if t and t not in tags and total + len(t) + 1 <= 480:
            tags.append(t)
            total += len(t) + 1
    return tags


def build(cfg, slug, langs=None, yes=False, confirm=None, log=print, force=False, trailers=True):
    import run as core

    st = cfg["story"]
    d, meta = load_story(cfg, slug)
    langs = story_langs(cfg, langs)
    scenes_f = d / "scenes.json"
    if not scenes_f.exists():
        raise SystemExit("Run story-prep first (scenes.json is missing).")
    scenes = read_json(scenes_f)

    # ---- plan every language and show the cost before spending anything
    plans, lines = [], []
    model, settings = st.get("model_id", "eleven_multilingual_v2"), st.get("voice_settings", {})
    cpc = float(st.get("credits_per_char", 1.0))
    dirs = core.dirs(cfg)
    for lang, key in langs.items():
        ch = cfg["channels"][key]
        tf, pf = text_file(d, lang), d / f"prep_{lang}.json"
        if not tf.exists() or not pf.exists():
            raise SystemExit(f"[{lang}] run story-prep first ({tf.name} / {pf.name} missing).")
        done = [p for p in (dirs["approved"] / key / slug, dirs["uploaded"] / key / slug) if p.exists()]
        if done and not force:
            log(f"[{lang}] skipped - already approved/uploaded ({done[0]}). Use --force to rebuild.")
            continue
        voice = check_voice_id(ch)
        prep = read_json(pf)
        units, paras, full = narration_units(cfg, lang, tf.read_text(encoding="utf-8"), prep["hook"])
        cache = d / "cache" / lang
        chars, credits, cached = narrate.plan_cost(units, cache, voice, model, settings, cpc)
        tr_chars = sum(len(t["script"]) for t in prep["trailers"]) if trailers else 0
        lines.append(f"  {lang} -> {ch['name']}: {len(units)} chunks ({cached} cached), "
                     f"{chars:,} new characters ~{credits:,.0f} credits"
                     + (f" + trailers {tr_chars:,} chars ~{tr_chars * cpc:,.0f} credits" if tr_chars else ""))
        plans.append((lang, key, ch, prep, units, paras, full, cache, chars + tr_chars))
    if not plans:
        return []
    total_new = sum(p[-1] for p in plans)
    summary = (f"Narration for '{meta['title']}':\n" + "\n".join(lines)
               + f"\n  TOTAL ~{total_new * cpc:,.0f} ElevenLabs credits")
    log(summary)
    if total_new and not yes:
        if not (confirm or _ask)(summary):
            log("Cancelled - nothing was recorded.")
            return []

    log("Footage...")
    clips = scene_footage(cfg, d, scenes["scenes"], log)
    results = []
    for lang, key, ch, prep, units, paras, full, cache, _ in plans:
        results.append(build_language(cfg, core, d, meta, slug, scenes, clips, lang, key, ch, prep,
                                      units, paras, full, cache, model, settings, log, trailers))
    return results


def _ask(summary):
    try:
        return input("Record this narration? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def build_language(cfg, core, d, meta, slug, scenes, clips, lang, key, ch, prep, units, paras,
                   full, cache, model, settings, log, trailers):
    st = cfg["story"]
    dirs = core.dirs(cfg)
    work = dirs["work"] / f"story-{slug}-{lang}"
    work.mkdir(parents=True, exist_ok=True)
    log(f"[{lang}] narrating {len(units)} chunks...")
    narrate.record(ch, units, cache, model, settings, lang, log)
    voice_wav = work / "voice.wav"
    total = narrate.join(units, work, voice_wav)

    # ---- timeline
    hook, last = units[0], units[-1]
    intro_t0 = hook["offset"] + hook["duration"] + 0.5
    intro = (intro_t0, intro_t0 + float(st.get("intro_card_seconds", 4)))
    outro = (last["offset"] + last["duration"] + 1.0, total)
    p_start = paragraph_times(units, paras, full)
    n_src = scenes["paragraphs"]
    scene_times = []
    for i, sc in enumerate(scenes["scenes"]):
        if i == 0:
            scene_times.append(0.0)
            continue
        idx = sc["start_paragraph"] if len(paras) == n_src else round(sc["start_fraction"] * len(paras))
        scene_times.append(max(p_start[min(idx, len(paras) - 1)], scene_times[-1] + 1))
    chapter_list = [(0, lv.INTRO_CHAPTER[lang])] + [
        (units[1]["offset"] if i == 0 else t, sc["title"][lang])
        for i, (sc, t) in enumerate(zip(scenes["scenes"], scene_times))]

    # ---- subtitles (SRT for YouTube + burned-in ASS)
    _, max_chars = lv.SUB_STYLE[lang]
    cues = [c for u in units for c in build_cues(u["text"], u["times"], lang, max_chars)]
    cues.sort(key=lambda c: c["start"])
    out_dir = dirs["review"] / key / slug
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    (out_dir / "subtitles.srt").write_text(to_srt(cues), encoding="utf-8")
    cards = lv.intro_outro_cards(ch, lang, prep["story_title"], prep["author_display"], intro, outro)
    lv.write_ass(work / "story.ass", ch, lang, cues, cards)

    # ---- video + thumbnail
    rng = random.Random(f"{slug}-{lang}")
    shots = lv.plan_shots(total, scene_times, clips, float(st["clip_seconds"]),
                          float(st["crossfade_seconds"]), rng)
    log(f"[{lang}] rendering {lv.fmt(total)} of video...")
    try:
        lv.render(cfg, ch, shots, work / "story.ass", voice_wav, total, work, out_dir / "video.mp4",
                  music=pick_music(cfg, log), log=log)
        first = clips[0][0]
        lv.thumbnail(cfg, ch, lang, first[0], min(3.0, first[1] / 2), prep["story_title"],
                     prep["author_display"], work, out_dir / "thumbnail.jpg")
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    chapters = chapters_text(chapter_list)
    desc = describe(cfg, meta, ch, key, lang, prep, chapters)
    m = {"kind": "story", "story": slug, "lang": lang, "channel": key, "channel_name": ch["name"],
         "title": prep["title"][:100], "script": desc, "description": desc, "tags": tags_for(prep, ch),
         "length_sec": round(total, 1), "chapters": chapter_list, "thumbnail": "thumbnail.jpg",
         "captions": "subtitles.srt", "created": datetime.now().isoformat(timespec="seconds")}
    write_json(out_dir / "meta.json", m)
    shutil.rmtree(work, ignore_errors=True)
    log(f"[{lang}] long video ready -> {out_dir}")
    made = [out_dir]

    # ---- trailers (Shorts, word-by-word captions, existing Shorts builder)
    if trailers:
        for i, tr in enumerate(prep["trailers"], 1):
            log(f"[{lang}] trailer {i}/{len(prep['trailers'])}...")
            tdesc = "\n\n".join([tr.get("description", ""), f"{prep['story_title']} — {ch['name']}",
                                 credits_text(meta, lang), ch.get("description_footer", ""),
                                 " ".join(ch.get("hashtags", [])) + " #Shorts"])
            script = {"title": tr["title"][:100], "script": tr["script"], "keywords": tr["keywords"],
                      "topic": f"{slug} trailer {i}", "description": tdesc}
            folder = dirs["review"] / key / f"{slug}-trailer-{i}"
            made.append(core.generate_one(cfg, key, script=script, folder=folder, extra_meta={
                "kind": "trailer", "story": slug, "lang": lang, "description": tdesc,
                "tags": tags_for(prep, ch)}))
    return made
