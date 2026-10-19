"""Prompt Minute - screen-recorded AI tips (9:16 Shorts).

A tip is described by tutorials/<slug>/tutorial.json:
  {"channel": "prompt_minute", "title": "...", "hook_text": "...", "description": "...",
   "hashtags": [...], "raw": "raw.mp4", "crop": [w, h, x, y],
   "parts": [{"text": "narration", "src": [start, end], "zoom": [w, h, x, y] | null,
              "label": "VAGUE PROMPT" | null, "good": true | false | null,
              "card": ["ROLE", "TASK", ...] | null, "dim": false}]}

raw.mp4 is the screen recording (the demo window only - crop removes everything else). Each
part's narration decides how long its piece of the recording is shown (sped up / slowed to fit).
Layout: hook headline for the first sentence, the screen in the middle, a step label above it
(green check / red cross), optional full-screen card (e.g. the 4-part formula), word captions.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from .assemble import (ASS_STYLE_FORMAT, _ass_color, _filter_path, _ts, caption_events, duration,
                       hook_events, run)
from .common import ROOT

W, H = 1080, 1920
SCREEN_W, SCREEN_Y = 1000, 400


def _segment(raw, crop, part, dur, out, bg):
    """One part of the screen track: the recording piece fitted to `dur` seconds."""
    s0, s1 = part["src"]
    src_len = max(s1 - s0, 0.1)
    cw, ch_, cx, cy = crop
    chain = f"crop={cw}:{ch_}:{cx}:{cy}"
    if part.get("zoom"):
        zw, zh, zx, zy = part["zoom"]
        chain += f",crop={zw}:{zh}:{zx}:{zy}"
    chain += f",scale={SCREEN_W}:-2,setpts=PTS*{dur / src_len:.4f}"
    if part.get("dim"):  # behind a full-screen card: dark and soft so the card reads clearly
        chain += ",boxblur=8:1,eq=brightness=-0.55"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s0:.2f}", "-t", f"{src_len:.2f}", "-i", str(raw),
         "-f", "lavfi", "-i", f"color=c=0x{bg}:s={W}x{H}:r=30:d={dur:.3f}",
         "-filter_complex", f"[0:v]{chain},fps=30[s];[1:v][s]overlay=(W-w)/2:{SCREEN_Y}:shortest=1,"
                            f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,format=yuv420p[v]",
         "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


def _overlay_ass(path, ch, cfg, parts, starts, total, words, hook):
    accent, white = _ass_color(ch["highlight_color"]), _ass_color(ch["caption_color"])
    red, green = "&H004639E6&", accent
    font = ch.get("font", "Anton")
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", ASS_STYLE_FORMAT,
             f"Style: Cap,{font},92,{white},{white},&H00000000,&H64000000,1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1",
             f"Style: Hook,{font},150,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,2,0,1,9,5,8,60,60,0,1",
             f"Style: Label,{font},78,{white},{white},&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,6,2,8,40,40,0,1",
             f"Style: Card,{font},120,{white},{white},&H00000000,&H96000000,1,0,0,0,100,100,3,0,1,8,4,5,40,40,0,1",
             f"Style: Brand,{font},44,{accent},{accent},&H00000000,&H00000000,1,0,0,0,100,100,6,0,1,0,0,8,0,0,0,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = [f"Dialogue: 1,{_ts(0)},{_ts(total)},Brand,,0,0,0,,{{\\an8\\pos({W // 2},70)}}{ch['name'].upper()}"]
    if hook:
        ev += hook_events(hook, words, ch, cfg)
    for p, t0, t1 in zip(parts, starts, starts[1:] + [total]):
        if p.get("label"):
            col = green if p.get("good") else red if p.get("good") is False else white
            mark = "✓ " if p.get("good") else "✗ " if p.get("good") is False else ""
            ev.append(f"Dialogue: 4,{_ts(t0)},{_ts(t1)},Label,,0,0,0,,{{\\an8\\pos({W // 2},{SCREEN_Y - 120})"
                      f"\\c{col}&\\fad(150,100)}}{mark}{p['label']}")
        if p.get("card"):
            for i, word in enumerate(p["card"]):  # the words appear one by one
                y = 700 + i * 170
                ev.append(f"Dialogue: 6,{_ts(t0 + i * 0.5)},{_ts(t1)},Card,,0,0,0,,{{\\an5\\pos({W // 2},{y})"
                          f"\\c{accent if i % 2 == 0 else white}&\\fscx80\\fscy80\\t(0,150,\\fscx100\\fscy100)"
                          f"\\fad(120,100)}}{word}")
    lines += ev + caption_events(words, ch, cfg, total, y_frac=0.86, layer=8)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def build(cfg, slug, log=print):
    """tutorials/<slug>/tutorial.json -> output/review/<channel>/<slug>/video.mp4 + meta.json."""
    import run as core
    from .storylib import char_times
    from .voice import check_voice_id, tts_with_timestamps, word_timings
    d = ROOT / "tutorials" / slug
    spec = json.loads((d / "tutorial.json").read_text(encoding="utf-8"))
    key = spec["channel"]
    ch = cfg["channels"][key]
    parts = spec["parts"]
    text = " ".join(p["text"].strip() for p in parts)
    voice = check_voice_id(ch)
    model = cfg["elevenlabs"]["model_id"]
    mp3, alf = d / "voice.mp3", d / "voice.json"
    if not (mp3.exists() and alf.exists()) or json.loads(alf.read_text(encoding="utf-8")).get("_text") != text:
        log(f"Recording voice ({len(text)} characters)...")
        al = tts_with_timestamps(voice, text, d / "voice.part.mp3", model,
                                 {"stability": 0.5, "similarity_boost": 0.8, "style": 0.2}) or {}
        al["_text"] = text
        alf.write_text(json.dumps(al, ensure_ascii=False), encoding="utf-8")
        (d / "voice.part.mp3").replace(mp3)
    al = json.loads(alf.read_text(encoding="utf-8"))
    words = word_timings(text, al, ch.get("language", "en"))
    times = char_times(text, al)
    starts, pos = [], 0
    for p in parts:
        i = text.find(p["text"].strip(), pos)
        starts.append(times[i][0] if 0 <= i < len(times) else (starts[-1] if starts else 0.0))
        pos = max(i, pos) + len(p["text"].strip())
    starts[0] = 0.0
    total = duration(mp3) + 0.8

    out_dir = core.dirs(cfg)["review"] / key / slug
    work = core.dirs(cfg)["work"] / f"tut-{slug}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        bg = ch.get("bg_color", "#0F1115").lstrip("#")
        segs = []
        for k, (p, t0, t1) in enumerate(zip(parts, starts, starts[1:] + [total])):
            seg = work / f"seg_{k:02d}.mp4"
            _segment(d / spec.get("raw", "raw.mp4"), spec["crop"], p, max(t1 - t0, 0.2), seg, bg)
            segs.append(seg)
        (work / "segs.txt").write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "segs.txt", "-c", "copy",
             "screen.mp4"], cwd=work)
        _overlay_ass(work / "tut.ass", ch, cfg, parts, starts, total, words, spec.get("hook_text"))
        log("Rendering...")
        run(["ffmpeg", "-y", "-v", "error", "-i", "screen.mp4", "-i", str(mp3.resolve()),
             "-filter_complex", f"[0:v]subtitles=tut.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}'[v];[1:a]apad[a]",
             "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium",
             "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             str((out_dir / "video.mp4").resolve())], cwd=work)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tags = list(dict.fromkeys(spec.get("hashtags", []) + ch.get("hashtags", [])))
    meta = {"kind": "tutorial", "channel": key, "channel_name": ch["name"], "title": spec["title"][:100],
            "topic": spec.get("topic", spec["title"]), "script": text, "hook_text": spec.get("hook_text"),
            "description": spec.get("description", ""), "hashtags": tags, "length_sec": round(total, 1),
            "created": datetime.now().isoformat(timespec="seconds")}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "script.txt").write_text(spec["title"] + "\n\n" + text + "\n", encoding="utf-8")
    log(f"Tip ready for review -> {out_dir}")
    return out_dir
