"""Step 4 - build the 9:16 video: stock footage + voice + word-by-word captions."""
import json
import os
import random
import subprocess
from pathlib import Path


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("FFmpeg failed:\n" + " ".join(map(str, cmd)) + "\n" + p.stderr[-2000:])
    return p.stdout


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


# ---------- captions (.ass) ----------

def _ass_color(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ts(t):
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(w):
    return w.replace("{", "(").replace("}", ")").replace("\\", "/")


def group_words(words, language, max_words=3, max_chars=18):
    if language == "th":
        max_words, max_chars = 2, 16
    elif language == "zh":  # "words" are single characters
        max_words, max_chars = 7, 8
    groups, cur = [], []
    for w in words:
        text = " ".join(x["word"] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(text) > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w["word"][-1:] in ".!?":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


ASS_STYLE_FORMAT = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")


def caption_style(ch, size=None):
    """The 'Cap' style line for word-by-word captions."""
    lang = ch.get("language", "en")
    size = size or (104 if lang == "en" else 92)
    base = _ass_color(ch["caption_color"])
    return (f"Style: Cap,{ch.get('font', 'Anton')},{size},{base},{base},&H00000000,&H64000000,1,0,0,0,"
            f"100,100,1,0,1,7,3,5,60,60,0,1")


def hook_end(words, lo=1.5, hi=4.0):
    """When the first spoken sentence ends (the hook headline stays that long)."""
    for w in words:
        if str(w["word"]).rstrip("\"')").endswith((".", "?", "!")):
            return min(max(w["end"] + 0.2, lo), hi)
    return hi


def hook_lines(text, width=13, max_lines=3):
    """Split the headline into short lines (English: by words)."""
    out, cur = [], ""
    for w in str(text).split():
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return (out + [cur])[:max_lines] if cur else out[:max_lines]


def hook_layout(text):
    """(lines, font scale): short headlines big; a whole hook sentence (up to ~10 words) smaller
    so it still fits in at most 4 lines."""
    for width, scale in ((13, 1.0), (16, 0.82), (20, 0.66)):
        full = hook_lines(text, width, 99)
        if len(full) <= (3 if width == 13 else 4):
            return full, scale
    return hook_lines(text, 20, 4), 0.66


def hook_events(hook, words, ch, cfg):
    """Big headline in the top third from the first frame until the first sentence ends."""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    end = hook_end(words)
    hi, base = _ass_color(ch["highlight_color"]), _ass_color(ch["caption_color"])
    pop = r"\fscx70\fscy70\t(0,160,\fscx108\fscy108)\t(160,280,\fscx100\fscy100)\fad(0,200)"
    ls, scale = hook_layout(hook.upper() if ch.get("language", "en") == "en" else hook)
    pop = pop.replace(r"\fscx100\fscy100", rf"\fscx{100 * scale:.0f}\fscy{100 * scale:.0f}") \
             .replace(r"\fscx108\fscy108", rf"\fscx{108 * scale:.0f}\fscy{108 * scale:.0f}") \
             .replace(r"\fscx70\fscy70", rf"\fscx{70 * scale:.0f}\fscy{70 * scale:.0f}")
    text = r"\N".join((r"{\c" + hi + "&}" if i % 2 == 0 else r"{\c" + base + "&}") + _clean(x)
                      for i, x in enumerate(ls))
    return [f"Dialogue: 5,{_ts(0)},{_ts(end)},Hook,,0,0,0,,"
            + r"{\an8\pos(" + f"{W // 2},{int(H * 0.17)}" + ")" + pop + "}" + text]


def write_ass(words, path, ch, cfg, total, hook=None):
    """hook: optional big opening headline (channels with hook_card: true)."""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    size = 150 if ch.get("language", "en") == "en" else 120
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", ASS_STYLE_FORMAT, caption_style(ch),
        f"Style: Hook,{ch.get('font', 'Anton')},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        f"1,0,0,0,100,100,2,0,1,9,5,8,60,60,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        lines += hook_events(hook, words, ch, cfg)
    lines += caption_events(words, ch, cfg, total)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def caption_events(words, ch, cfg, total, y_frac=0.62, layer=0):
    """Word-by-word caption Dialogue lines (current word highlighted), centred at y_frac."""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    lang = ch.get("language", "en")
    base, hi = _ass_color(ch["caption_color"]), _ass_color(ch["highlight_color"])
    sep = " " if lang == "en" else ""
    lines = []
    groups = group_words(words, lang)
    flat = [w for g in groups for w in g]
    for gi, g in enumerate(groups):
        for wi, w in enumerate(g):
            idx = flat.index(w)
            start = w["start"]
            end = flat[idx + 1]["start"] if idx + 1 < len(flat) else min(w["end"] + 0.6, total)
            parts = []
            for j, x in enumerate(g):
                t = _clean(x["word"].upper() if lang == "en" else x["word"])
                if j == wi:
                    parts.append(r"{\c" + hi + r"&\fscx112\fscy112}" + t + r"{\c" + base + r"&\fscx100\fscy100}")
                else:
                    parts.append(t)
            if sep:
                joined = sep.join(parts)
            else:  # Thai / Chinese: keep only the spaces the script itself had
                joined = "".join((" " if j and x.get("space_before") else "") + p
                                 for j, (x, p) in enumerate(zip(g, parts)))
            text = r"{\an5\pos(" + f"{W//2},{int(H*y_frac)}" + ")}" + joined
            lines.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},Cap,,0,0,0,,{text}")
    return lines


# ---------- video ----------

def _filter_path(p):
    p = str(Path(p).resolve()).replace("\\", "/")
    return p.replace(":", r"\:").replace("'", r"\'")


def motion_filter(W, H, amount, dur, i):
    """Scale + crop to WxH. With amount > 0 the frame is enlarged by that much and the crop
    window drifts slowly across it (direction alternates per clip), so stills never sit still."""
    if amount <= 0:
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    bw, bh = int(W * (1 + amount)) // 2 * 2, int(H * (1 + amount)) // 2 * 2
    p = f"min(t/{max(dur, 0.1):.3f},1)"
    dx, dy = W * amount, H * amount  # drift distance stays small even when the source is much wider
    cx, cy = "(iw-ow)/2", "(ih-oh)/2"
    x = [f"{cx}-{dx / 2:.1f}+{dx:.1f}*{p}", f"{cx}+{dx / 2:.1f}-{dx:.1f}*{p}", cx, cx][i % 4]
    y = [cy, cy, f"{cy}-{dy / 2:.1f}+{dy:.1f}*{p}", f"{cy}+{dy / 2:.1f}-{dy:.1f}*{p}"][i % 4]
    return (f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={W}:{H}:x='{x}':y='{y}'")


def build_video(cfg, ch, clips, voice_mp3, words, workdir, out_mp4, hook=None):
    """hook: big opening headline; the video then also opens on the best-matching clip."""
    workdir = Path(workdir)
    total = duration(voice_mp3) + 0.6
    seg_files = make_background(cfg, clips, total, workdir, first=clips[0] if hook and clips else None)
    write_ass(words, workdir / "captions.ass", ch, cfg, total, hook)
    finish_video(cfg, workdir, "captions.ass", voice_mp3, total, out_mp4, seg_files)
    return total


def make_background(cfg, clips, total, workdir, first=None):
    """Cut the clips into clip_seconds segments (slow pan) and join them as workdir/bg.mp4.
    first: the clip to open on (the top search result for the subject).
    Returns the segment files (removed by finish_video)."""
    workdir = Path(workdir)
    v = cfg["video"]
    W, H, fps, seg = v["width"], v["height"], v["fps"], float(v["clip_seconds"])
    n = int(total // seg) + 1
    order = list(clips)
    random.shuffle(order)
    if first in order:
        order.remove(first)
        order.insert(0, first)
    seg_files = []
    for i in range(n):
        src = order[i % len(order)]
        dur = min(seg, total - i * seg)
        if dur <= 0.05:
            break
        src_len = duration(src)
        offset = random.uniform(0, max(src_len - dur - 0.1, 0))
        out = workdir / f"seg_{i:02d}.mp4"
        run(["ffmpeg", "-y", "-ss", f"{offset:.2f}", "-i", str(src), "-t", f"{dur:.3f}", "-an",
             "-vf", f"{motion_filter(W, H, float(v.get('motion', 0)), dur, i)},"
                    f"fps={fps},setsar=1,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])
        seg_files.append(out)
    lst = workdir / "segments.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", "bg.mp4"],
        cwd=workdir)
    return seg_files + [lst]


def finish_video(cfg, workdir, ass_name, voice_mp3, total, out_mp4, temp_files=(), darken=None):
    """Darken workdir/bg.mp4, burn in the .ass overlay, add the voice, write out_mp4."""
    workdir = Path(workdir)
    v = cfg["video"]
    darken = v["darken"] if darken is None else darken
    vf = (f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{darken}:t=fill,"
          f"subtitles={ass_name}:fontsdir='{_filter_path(cfg['fonts_dir'])}'")
    run(["ffmpeg", "-y", "-i", "bg.mp4", "-i", str(Path(voice_mp3).resolve()),
         "-filter_complex", f"[0:v]{vf}[v];[1:a]apad[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         str(Path(out_mp4).resolve())], cwd=workdir)
    for p in list(temp_files) + [workdir / "bg.mp4"]:
        try:
            os.remove(p)
        except OSError:
            pass
