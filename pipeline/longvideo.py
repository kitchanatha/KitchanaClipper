"""Story mode - the long 16:9 video, cards and thumbnail.

Rendering plan (efficient for 20-60 minute videos):
  1. plan_shots: one continuous chain of footage shots with slow crossfades over the
     whole timeline (scene keywords decide which clips play when).
  2. plan_windows: cut the timeline into ~150 s windows, always in the middle of a shot
     where no crossfade happens, so the joins are invisible.
  3. render each window once (scale, crossfades, darken, vignette, subtitles + cards
     burned in from ONE global .ass file), then join the windows with -c copy and mux the
     voice + ducked music. Video is encoded exactly once.
"""
import random
from pathlib import Path

from .assemble import _filter_path, motion_filter, run
from .storylib import ass_color, ass_escape, ass_time, display_width, wrap_text

W, H, FPS = 1920, 1080, 30

SUB_STYLE = {"en": (58, 40), "th": (60, 38), "zh": (58, 20)}  # font size, max chars per line
TITLE_WRAP = {"en": 22, "th": 18, "zh": 12}
THUMB_WRAP = {"en": 16, "th": 14, "zh": 9}
SUBSCRIBE = {"en": "Subscribe for more stories", "th": "กดติดตามเพื่อฟังเรื่องเล่าต่อไป",
             "zh": "订阅频道，收听更多故事"}
BY = {"en": "by {}", "th": "โดย {}", "zh": "作者：{}"}
INTRO_CHAPTER = {"en": "Introduction", "th": "บทนำ", "zh": "开场"}


# ---------------------------------------------------------------- planning (pure)

def plan_shots(total, scene_starts, clips_by_scene, clip_len, xfade, rng=None):
    """scene_starts: sorted start times (first is 0). clips_by_scene: per scene a list of
    (path, length). Returns shots [{'start', 'dur', 'src', 'ss'}] covering 0..total, each
    overlapping the previous one by xfade seconds."""
    rng = rng or random.Random(1)
    step = clip_len - xfade
    used = [0] * len(clips_by_scene)
    shots, t = [], 0.0
    while t < total:
        mid = t + clip_len / 2
        scene = max(i for i, s in enumerate(scene_starts) if s <= mid) if mid >= scene_starts[0] else 0
        pool = clips_by_scene[scene] or next(c for c in clips_by_scene if c)
        path, length = pool[used[scene] % len(pool)]
        used[scene] += 1
        ss = rng.uniform(0, max(length - clip_len - 0.2, 0))
        shots.append({"start": round(t, 3), "dur": clip_len, "src": path, "ss": round(ss, 3)})
        t += step
    return shots


def plan_windows(shots, total, target, fps=FPS):
    """Frame-exact render windows [(start_frame, end_frame)] covering the whole video. Every
    inner boundary sits in the middle of a shot (outside any crossfade)."""
    total_frames = int(round(total * fps))
    bounds, next_target = [0], target
    for s in shots:
        mid = s["start"] + s["dur"] / 2
        if mid >= next_target and mid < total - target / 3:
            f = int(round(mid * fps))
            if f > bounds[-1]:
                bounds.append(f)
                next_target = mid + target
    bounds.append(total_frames)
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def window_inputs(shots, a_t, b_t, last):
    """Shots used in window [a_t, b_t) as (src, ss, length, offset_in_window)."""
    out = []
    for s in shots:
        s_end = s["start"] + s["dur"]
        if s["start"] >= b_t or s_end <= a_t:
            continue
        start = max(a_t, s["start"])
        end = s_end if last else min(b_t, s_end)
        out.append((s["src"], s["ss"] + (start - s["start"]), end - start, start - a_t))
    return out


# ---------------------------------------------------------------- ASS (subtitles + cards)

def _candle(x, y, scale, text_col, accent):
    """Small candle logo as ASS vector drawing events (body + flame + glow)."""
    s = scale
    body = f"m 0 {60*s} l {40*s} {60*s} l {40*s} {170*s} l 0 {170*s}"
    flame = f"m {20*s} 0 b {36*s} {22*s} {40*s} {40*s} {20*s} {56*s} b 0 {40*s} {4*s} {22*s} {20*s} 0"
    pos = rf"\an7\pos({x - 20*s:.0f},{y:.0f})\bord0\shad0\p1"
    return [(pos + rf"\1c{accent}\blur{8*s:.0f}\alpha&H80&", flame),
            (pos + rf"\1c{text_col}", body),
            (pos + rf"\1c{accent}", flame)]


def card_events(t0, t1, ch, lang, lines, fade=700, res=(W, H)):
    """A full-screen card: background colour panel + candle + text lines.
    lines: [(style, text, y)]"""
    w, h = res
    bg = ass_color(ch["bg_color"], 0x14)
    ev = [(0, t0, t1, "Card", rf"{{\an7\pos(0,0)\bord0\shad0\1c{bg}\fad({fade},{fade})\p1}}"
           f"m 0 0 l {w} 0 l {w} {h} l 0 {h}")]
    for tags, draw in _candle(w / 2, h * 0.16, 1.0, ass_color(ch["caption_color"]),
                              ass_color(ch["highlight_color"])):
        ev.append((1, t0, t1, "Card", "{" + tags + rf"\fad({fade},{fade})" + "}" + draw))
    for style, text, y in lines:
        ev.append((2, t0, t1, style, rf"{{\an5\pos({w // 2},{y})\fad({fade},{fade})}}" + text))
    return ev


def write_ass(path, ch, lang, cues, cards, res=(W, H)):
    w, h = res
    size, _ = SUB_STYLE[lang]
    text, accent = ass_color(ch["caption_color"]), ass_color(ch["highlight_color"])
    title_font, font = ch.get("title_font", ch["font"]), ch["font"]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{size},{text},{text},&H00000000,&H96000000,0,0,0,0,100,100,0,0,"
        f"1,3,1.5,2,120,120,64,1",
        f"Style: Card,{font},40,{text},{text},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        f"Style: CardChannel,{title_font},44,{accent},{accent},&H00000000,&H00000000,0,0,0,0,100,100,"
        f"{4 if lang == 'en' else 0},0,1,0,0,5,0,0,0,1",  # letter spacing suits Latin only
        f"Style: CardTitle,{title_font},96,{text},{text},&H00000000,&H64000000,1,0,0,0,100,100,1,0,"
        f"1,0,2,5,80,80,0,1",
        f"Style: CardSmall,{font},46,{text},{text},&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,0,0,5,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev = []
    for c in cues:
        ev.append((3, c["start"], c["end"], "Sub", r"\N".join(ass_escape(x) for x in c["lines"])))
    ev.extend(cards)
    ev.sort(key=lambda e: (e[1], e[0]))
    body = [f"Dialogue: {L},{ass_time(a)},{ass_time(b)},{st},,0,0,0,,{tx}" for L, a, b, st, tx in ev]
    Path(path).write_text("\n".join(head + body) + "\n", encoding="utf-8-sig")


def intro_outro_cards(ch, lang, story_title, author, intro, outro):
    """intro/outro: (start, end) seconds or None."""
    title_lines = wrap_text(story_title, lang, TITLE_WRAP[lang])
    th = 110
    title_y0 = int(H * 0.52 - (len(title_lines) - 1) * th / 2)
    ev = []
    if intro:
        lines = [("CardChannel", ass_escape(ch["name"]), int(H * 0.40))]
        lines += [("CardTitle", ass_escape(t), title_y0 + i * th) for i, t in enumerate(title_lines)]
        lines.append(("CardSmall", ass_escape(BY[lang].format(author)),
                      title_y0 + len(title_lines) * th + 30))
        ev += card_events(intro[0], intro[1], ch, lang, lines)
    if outro:
        ev += card_events(outro[0], outro[1], ch, lang, [
            ("CardTitle", ass_escape(SUBSCRIBE[lang]), int(H * 0.52)),
            ("CardChannel", ass_escape(ch["name"]), int(H * 0.66))])
    return ev


# ---------------------------------------------------------------- render

def render(cfg, ch, shots, ass_file, voice_wav, total, workdir, out_mp4, music=None, log=print):
    st = cfg["story"]
    workdir = Path(workdir)
    xfade, darken = float(st["crossfade_seconds"]), float(st.get("darken", 0.35))
    fonts = _filter_path(cfg["fonts_dir"])
    windows = plan_windows(shots, total, float(st.get("segment_seconds", 150)))
    seg_files = []
    for k, (fa, fb) in enumerate(windows):
        a_t, b_t = fa / FPS, fb / FPS
        ins = window_inputs(shots, a_t, b_t, last=(k == len(windows) - 1))
        log(f"  rendering part {k + 1}/{len(windows)} ({fmt(a_t)}-{fmt(b_t)}, {len(ins)} clips)")
        cmd, chains = ["ffmpeg", "-y", "-v", "error"], []
        motion = float(st.get("motion", 0))
        for j, (src, ss, length, _) in enumerate(ins):
            cmd += ["-stream_loop", "-1", "-ss", f"{ss:.3f}", "-t", f"{length + 0.5:.3f}", "-i", str(src)]
            chains.append(f"[{j}:v]{motion_filter(W, H, motion, length, k * 7 + j)},"
                          f"fps={FPS},setsar=1,format=yuv420p,trim=duration={length:.3f},"
                          f"setpts=PTS-STARTPTS,settb=AVTB[s{j}]")
        last = "s0"
        for j in range(1, len(ins)):
            off = ins[j][3]
            chains.append(f"[{last}][s{j}]xfade=transition=fade:duration={xfade}:offset={off:.3f}[x{j}]")
            last = f"x{j}"
        chains.append(
            f"[{last}]eq=saturation=0.85,drawbox=x=0:y=0:w=iw:h=ih:color=black@{darken}:t=fill,"
            f"vignette=angle=PI/5,setpts=PTS+{a_t:.6f}/TB,"
            f"subtitles={Path(ass_file).name}:fontsdir='{fonts}',setpts=PTS-STARTPTS[v]")
        seg = workdir / f"seg_{k:03d}.mp4"
        cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-frames:v", str(fb - fa),
                "-c:v", "libx264", "-preset", st.get("x264_preset", "veryfast"), "-crf", "21",
                "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2), "-an", seg.name]
        run(cmd, cwd=workdir)
        seg_files.append(seg)

    lst = workdir / "segments.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    log("  adding voice and music...")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst.name,
           "-i", str(Path(voice_wav).resolve())]
    if music:
        vol = float(st.get("music_volume", 0.12))
        cmd += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        fade_out = max(total - 5, 0)
        cmd += ["-filter_complex",
                "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,asplit=2[va][vs];"
                f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={vol},"
                f"afade=t=in:d=3,afade=t=out:st={fade_out:.2f}:d=5[m];"
                "[m][vs]sidechaincompress=threshold=0.03:ratio=6:attack=80:release=900[md];"
                "[va][md]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(Path(out_mp4).resolve())]
    run(cmd, cwd=workdir)
    for p in seg_files + [lst]:
        p.unlink(missing_ok=True)


def thumbnail(cfg, ch, lang, frame_src, ss, story_title, author, workdir, out_jpg):
    """1280x720 thumbnail: dark frame from the footage + title in the channel font + candle."""
    workdir = Path(workdir)
    tw, th = 1280, 720
    lines = wrap_text(story_title, lang, THUMB_WRAP[lang])
    widest = max(display_width(x) for x in lines)
    # short titles get bigger: aim for the widest line to fill ~70% of the width
    per_char = {"en": 0.62, "th": 0.55, "zh": 1.0}[lang]
    size = int(max(80, min(150, tw * 0.7 / max(widest * per_char, 1))))
    text, accent = ass_color(ch["caption_color"]), ass_color(ch["highlight_color"])
    title_font = ch.get("title_font", ch["font"])
    y_last = int(th * 0.80)
    ev = [(0, 0, 5, "Card", r"{\an7\pos(0,0)\bord0\shad0\1c&H000000&\alpha&H50&\blur60\p1}"
           f"m -80 {int(th * 0.30)} l {int(tw * 0.78)} {int(th * 0.30)} l {int(tw * 0.78)} {th + 80} "
           f"l -80 {th + 80}")]  # soft dark panel behind the title for contrast
    for tags, draw in _candle(86, 60, 0.55, text, accent):
        ev.append((1, 0, 5, "Card", "{" + tags + "}" + draw))
    ev.append((2, 0, 5, "Small", r"{\an4\pos(122,108)}" + ass_escape(ch["name"])))
    for i, line in enumerate(reversed(lines)):
        ev.append((2, 0, 5, "Title", rf"{{\an1\pos(70,{y_last - i * int(size * 1.08)})}}" + ass_escape(line)))
    ev.append((2, 0, 5, "Small", rf"{{\an1\pos(74,{y_last + 64})\1c{accent}}}"
               + ass_escape(BY[lang].format(author))))
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {tw}", f"PlayResY: {th}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,{title_font},{size},{text},{text},&H00000000,&H80000000,1,0,0,0,100,100,1,0,"
        f"1,4,3,1,0,0,0,1",
        f"Style: Small,{ch['font']},38,{text},{text},&H00000000,&H80000000,1,0,0,0,100,100,1,0,"
        f"1,2,2,4,0,0,0,1",
        f"Style: Card,{ch['font']},40,{text},{text},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = [f"Dialogue: {L},{ass_time(a)},{ass_time(b)},{s},,0,0,0,,{t}" for L, a, b, s, t in ev]
    (workdir / "thumb.ass").write_text("\n".join(head + body) + "\n", encoding="utf-8-sig")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ss:.2f}", "-i", str(frame_src), "-frames:v", "1",
         "-vf", f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
                f"eq=brightness=-0.10:saturation=0.75,vignette=angle=PI/4,"
                f"subtitles=thumb.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}'",
         "-q:v", "3", str(Path(out_jpg).resolve())], cwd=workdir)


def preview_clip(src, out, seconds=60):
    """First N seconds, small enough for Discord."""
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", str(seconds), "-vf", "scale=854:-2",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "96k",
         str(out)])
    return out


def fmt(t):
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"
