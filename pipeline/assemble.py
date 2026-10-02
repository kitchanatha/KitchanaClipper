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


def write_ass(words, path, ch, cfg, total):
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    lang = ch.get("language", "en")
    font = ch.get("font", "Anton")
    size = 104 if lang == "en" else 92
    base, hi = _ass_color(ch["caption_color"]), _ass_color(ch["highlight_color"])
    sep = " " if lang == "en" else ""
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{size},{base},{base},&H00000000,&H64000000,1,0,0,0,100,100,1,0,"
        f"1,7,3,5,60,60,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
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
            text = r"{\an5\pos(" + f"{W//2},{int(H*0.62)}" + ")}" + sep.join(parts)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{text}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- video ----------

def _filter_path(p):
    p = str(Path(p).resolve()).replace("\\", "/")
    return p.replace(":", r"\:").replace("'", r"\'")


def build_video(cfg, ch, clips, voice_mp3, words, workdir, out_mp4):
    workdir = Path(workdir)
    v = cfg["video"]
    W, H, fps, seg = v["width"], v["height"], v["fps"], float(v["clip_seconds"])
    total = duration(voice_mp3) + 0.6
    n = int(total // seg) + 1
    order = list(clips)
    random.shuffle(order)
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
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    f"fps={fps},setsar=1,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])
        seg_files.append(out)
    lst = workdir / "segments.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", "bg.mp4"],
        cwd=workdir)

    write_ass(words, workdir / "captions.ass", ch, cfg, total)
    vf = (f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{v['darken']}:t=fill,"
          f"subtitles=captions.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}'")
    run(["ffmpeg", "-y", "-i", "bg.mp4", "-i", str(Path(voice_mp3).resolve()),
         "-filter_complex", f"[0:v]{vf}[v];[1:a]apad[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         str(Path(out_mp4).resolve())], cwd=workdir)
    for p in seg_files + [workdir / "bg.mp4", lst]:
        try:
            os.remove(p)
        except OSError:
            pass
    return total
