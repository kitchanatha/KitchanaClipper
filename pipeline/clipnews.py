"""Voiced patch / tips / news Shorts for clipper channels (Daeva Highlights).

clipnews/<slug>/short.json:
  {"channel": "aion2_clips", "title", "description", "sources": [...], "hashtags": [...],
   "hook_text": "...",
   "parts": [{"text": narration, "card": ["BIG LINE", "small line", ...] | absent,
              "clip": clip id from clip-list, "src": start second in that clip}]}

Each part's narration decides how long its piece of gameplay is shown. The gameplay comes ONLY
from permitted creators' clips (clip-add), cropped to the game view (the streamer's facecam and
burned-in captions are cut off) and muted under the narration; the creator is credited on screen
and in the description, next to the sources. Layout (1080x1920): channel name, card / hook text
above the gameplay, word captions below it, credit at the bottom.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from .assemble import ASS_STYLE_FORMAT, _ass_color, _filter_path, _ts, caption_events, duration, run
from .clipper import ClipError, credit_lines, credit_text, get_clip
from .common import ROOT

W, H = 1080, 1920
GAME_Y, GAME_H = 640, 630          # gameplay strip
CROP = "crop=1200:700:360:0"       # 1920x1080 source: game view without bottom-left facecam / caption bar


def _ass(path, ch, parts, starts, total, words, hook, credit):
    font = ch.get("font", "Anton")
    white, accent = _ass_color(ch.get("caption_color", "#FFFFFF")), _ass_color(ch.get("highlight_color", "#7FD3FF"))
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", ASS_STYLE_FORMAT,
             f"Style: Cap,{font},84,{white},{white},&H00000000,&H64000000,1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1",
             f"Style: Hook,{font},112,{white},{white},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,8,4,8,60,60,0,1",
             f"Style: Card,{font},100,{accent},{accent},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,8,3,5,50,50,0,1",
             f"Style: Sub,{font},62,{white},{white},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,6,2,5,50,50,0,1",
             f"Style: Credit,{font},46,{accent},{accent},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,5,2,2,40,40,0,1",
             f"Style: Brand,{font},44,{accent},{accent},&H00000000,&H00000000,1,0,0,0,100,100,6,0,1,0,0,8,0,0,0,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    clean = lambda t: str(t).replace("{", "(").replace("}", ")")  # noqa: E731
    ev = [f"Dialogue: 1,{_ts(0)},{_ts(total)},Brand,,0,0,0,,{{\\an8\\pos({W // 2},90)}}{clean(ch['name'].upper())}",
          f"Dialogue: 1,{_ts(0)},{_ts(total)},Credit,,0,0,0,,{{\\an2\\pos({W // 2},1830)}}{clean(credit)}"]
    if hook:
        ev.append(f"Dialogue: 4,{_ts(0)},{_ts(starts[1] if len(starts) > 1 else total)},Hook,,0,0,0,,"
                  f"{{\\an2\\pos({W // 2},{GAME_Y - 60})\\fscx80\\fscy80\\t(0,180,\\fscx100\\fscy100)\\fad(100,150)}}{clean(hook.upper())}")
    for p, t0, t1 in zip(parts, starts, starts[1:] + [total]):
        card = p.get("card") or []
        for i, line in enumerate(card):  # first line big (accent), the rest smaller, appearing one by one
            style, y = ("Card", 300) if i == 0 else ("Sub", 300 + 110 + (i - 1) * 85)
            ev.append(f"Dialogue: 5,{_ts(t0 + i * 0.35)},{_ts(t1)},{style},,0,0,0,,{{\\an5\\pos({W // 2},{y})"
                      f"\\fscx85\\fscy85\\t(0,150,\\fscx100\\fscy100)\\fad(120,120)}}{clean(line)}")
    lines += ev + caption_events(words, ch, {"video": {"width": W, "height": H}}, total, y_frac=0.79, layer=8)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _segment(clip_file, src, dur, out):
    """dur seconds of gameplay from src (looped if the clip is shorter), game view only, muted."""
    run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-ss", f"{src:.2f}", "-i", str(clip_file),
         "-filter_complex",
         f"[0:v]{CROP},split=2[a][b];"
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,eq=brightness=-0.35[bg];"
         f"[b]scale={W}:{GAME_H}:force_original_aspect_ratio=increase,crop={W}:{GAME_H}[fg];"
         f"[bg][fg]overlay=0:{GAME_Y},fps=30,trim=duration={dur:.3f},setpts=PTS-STARTPTS,format=yuv420p[v]",
         "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", str(out)])


def build(cfg, slug, log=print):
    """clipnews/<slug>/short.json -> output/review/<channel>/<slug>/video.mp4 + meta.json."""
    import run as core
    from .storylib import char_times
    from .voice import check_voice_id, tts_with_timestamps, word_timings
    d = ROOT / "clipnews" / slug
    spec = json.loads((d / "short.json").read_text(encoding="utf-8"))
    key = spec["channel"]
    ch = cfg["channels"][key]
    parts = spec["parts"]
    recs = {p["clip"]: get_clip(key, p["clip"]) for p in parts}  # every clip must be a permitted, added clip
    text = " ".join(p["text"].strip() for p in parts)
    mp3, alf = d / "voice.mp3", d / "voice.json"
    if not (mp3.exists() and alf.exists()) or json.loads(alf.read_text(encoding="utf-8")).get("_text") != text:
        log(f"Recording voice ({len(text)} characters)...")
        al = tts_with_timestamps(check_voice_id(ch), text, d / "voice.part.mp3", cfg["elevenlabs"]["model_id"],
                                 {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3}) or {}
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
    total = duration(mp3) + 0.6
    if total > 60:
        raise ClipError(f"{total:.0f} s is too long for a Short - shorten the script")
    creators = list(dict.fromkeys(r["creator"] for r in recs.values()))
    first = next(iter(recs.values()))
    credit = (f"GAMEPLAY: {first['creator'].upper()} - {credit_text(first)}" if len(creators) == 1
              else "GAMEPLAY: " + ", ".join(c.upper() for c in creators))

    out_dir = core.dirs(cfg)["review"] / key / slug
    work = core.dirs(cfg)["work"] / f"clipnews-{slug}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        segs = []
        for k, (p, t0, t1) in enumerate(zip(parts, starts, starts[1:] + [total])):
            seg = work / f"seg_{k:02d}.mp4"
            _segment(recs[p["clip"]]["file"], float(p.get("src", 0)), max(t1 - t0, 0.2), seg)
            segs.append(seg)
        (work / "segs.txt").write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "segs.txt", "-c", "copy", "bg.mp4"], cwd=work)
        _ass(work / "news.ass", ch, parts, starts, total, words, spec.get("hook_text"), credit)
        log("Rendering...")
        run(["ffmpeg", "-y", "-v", "error", "-i", "bg.mp4", "-i", str(mp3.resolve()),
             "-filter_complex", f"[0:v]subtitles=news.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}'[v];"
                                f"[1:a]loudnorm=I=-14:TP=-1.5,apad[a]",
             "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium",
             "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             str((out_dir / "video.mp4").resolve())], cwd=work)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    desc = "\n".join([spec.get("description", ""), "", "Sources:", *spec.get("sources", []), "",
                      "Gameplay used with permission from:", *credit_lines(list(recs.values()))])
    meta = {"kind": "clip_news", "channel": key, "channel_name": ch["name"], "title": spec["title"][:100],
            "topic": spec.get("topic", spec["title"]), "script": text, "hook_text": spec.get("hook_text"),
            "description": desc, "hashtags": spec.get("hashtags", []), "clips": list(recs), "creators": creators,
            "contains_synthetic_media": True,   # AI narration voice
            "length_sec": round(total, 1), "created": datetime.now().isoformat(timespec="seconds")}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "script.txt").write_text(spec["title"] + "\n\n" + text + "\n", encoding="utf-8")
    log(f"Patch/news Short ready for review -> {out_dir}")
    return out_dir
