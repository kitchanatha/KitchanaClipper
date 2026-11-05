"""Clipper channels (type: clipper) - game highlights made ONLY from streamers who gave permission.

Every creator is listed in the channel's `creators:` in config.yaml with a recorded permission
(date + how). `clip-add` checks the clip's channel against that list BEFORE anything is downloaded
and refuses everything else. Each video credits the creator on screen and in the description.

  clips/<channel>/<clip_id>/source.mp4 + source.json     (clip-add)
  output/review/<channel>/<slug>/video.mp4 + meta.json   (clip-short / clip-compile -> Discord review)

Highlight Short: 1080x1920, the gameplay centred over a blurred copy of itself, hook headline on
top, creator credit at the bottom, the streamer's original audio. Compilation: 1920x1080, clips
joined with a credit lower-third on each and YouTube chapters in the description.
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .assemble import ASS_STYLE_FORMAT, _ass_color, _filter_path, _ts, duration, run
from .common import ROOT

PLATFORMS = {"twitch": "twitch.tv", "youtube": "youtube.com", "kick": "kick.com"}
SHORT_MAX_S = 60.0


class ClipError(RuntimeError):
    pass


# ---------- permission registry ----------
def clipper_keys(cfg):
    return [k for k, c in cfg["channels"].items() if c.get("type") == "clipper"]


def creators(ch):
    return [c for c in (ch.get("creators") or []) if isinstance(c, dict)]


def validate_creators(cfg):
    """Config problems for every clipper channel (empty list = fine)."""
    probs = []
    for key in clipper_keys(cfg):
        for i, c in enumerate(creators(cfg["channels"][key])):
            where = f"channels.{key}.creators[{i}] ({c.get('name', '?')})"
            if c.get("platform") not in PLATFORMS:
                probs.append(f"{where}: platform must be one of {', '.join(PLATFORMS)}")
            if not str(c.get("channel", "")).strip():
                probs.append(f"{where}: channel (login / @handle) is missing")
            if not str(c.get("url", "")).startswith("https://"):
                probs.append(f"{where}: url must start with https://")
            perm = c.get("permission") or {}
            if not (str(perm.get("date", "")).strip() and str(perm.get("how", "")).strip()):
                probs.append(f"{where}: permission needs date and how (e.g. 'Discord DM', 'email')")
    return probs


def _norm(s):
    return re.sub(r"[^a-z0-9_]", "", str(s or "").lower().lstrip("@"))


def match_creator(ch, info, url=""):
    """The registered creator this clip belongs to, or None. Matches the platform plus the channel
    login/@handle against the clip's metadata and URL path. Fails closed."""
    host = (urlparse(url).hostname or "").lower()
    platform = next((p for p, h in PLATFORMS.items() if host.endswith(h) or (p == "youtube" and host.endswith("youtu.be"))
                     or (p == "twitch" and host.endswith("clips.twitch.tv"))), None)
    seen = {_norm(info.get(k)) for k in ("channel", "channel_id", "uploader", "uploader_id", "channel_url",
                                          "uploader_url", "display_id_owner")}
    for k in ("channel_url", "uploader_url"):
        if info.get(k):
            seen.add(_norm(urlparse(info[k]).path.strip("/").split("/")[-1]))
    path = [p for p in urlparse(url).path.split("/") if p]
    if platform in ("twitch", "kick") and len(path) >= 2 and path[1] in ("clip", "clips", "videos"):
        seen.add(_norm(path[0]))
    seen.discard("")
    for c in creators(ch):
        if c.get("platform") == platform and _norm(c.get("channel")) in seen:
            return c
    return None


# ---------- clip library ----------
def clips_dir(key):
    return ROOT / "clips" / key


def list_clips(key):
    out = []
    for f in sorted(clips_dir(key).glob("*/source.json")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def _ytdlp():
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        raise ClipError("yt-dlp is not installed: py -3 -m pip install yt-dlp")
    return [sys.executable, "-m", "yt_dlp"]


def _clip_id(platform, raw_id):
    return f"{platform}-{re.sub(r'[^A-Za-z0-9_-]', '', str(raw_id))[:60]}"


def add(cfg, key, source, creator_name=None, log=print):
    """Add a clip from a URL (checked against the creator list first) or from a local file the
    creator sent you (creator_name required). Returns the clip id."""
    ch = cfg["channels"][key]
    if ch.get("type") != "clipper":
        raise ClipError(f"{key} is not a clipper channel")
    p = Path(source)
    if p.exists():
        c = next((c for c in creators(ch) if c.get("name", "").lower() == str(creator_name or "").lower()), None)
        if not c:
            raise ClipError("a local file needs --creator <name> of a creator listed (with permission) in config")
        cid = _clip_id("file", f"{_norm(c['name'])}-{p.stem}")
        d = clips_dir(key) / cid
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, d / "source.mp4")
        info = {"title": p.stem, "webpage_url": ""}
    else:
        if not source.startswith("https://"):
            raise ClipError("give an https:// clip URL or an existing file path")
        base = _ytdlp()
        meta = subprocess.run(base + ["--dump-single-json", "--no-playlist", "--skip-download", source],
                              capture_output=True, text=True, encoding="utf-8")
        if meta.returncode != 0:
            raise ClipError("could not read the clip: " + meta.stderr.strip()[-400:])
        info = json.loads(meta.stdout)
        c = match_creator(ch, info, info.get("webpage_url") or source)
        if not c:
            who = info.get("channel") or info.get("uploader") or "unknown"
            raise ClipError(f"REFUSED: '{who}' is not in channels.{key}.creators with permission. "
                            "Ask the creator first, then add them to config.yaml.")
        cid = _clip_id(c["platform"], info.get("id", "clip"))
        d = clips_dir(key) / cid
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "source.mp4").exists():
            log(f"Downloading clip by {c['name']}...")
            dl = subprocess.run(base + ["--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
                                        "--merge-output-format", "mp4", "-o", str(d / "source.%(ext)s"), source],
                                capture_output=True, text=True, encoding="utf-8")
            if dl.returncode != 0 or not (d / "source.mp4").exists():
                shutil.rmtree(d, ignore_errors=True)
                raise ClipError("download failed: " + dl.stderr.strip()[-400:])
    rec = {"id": cid, "channel": key, "creator": c["name"], "platform": c["platform"], "creator_url": c["url"],
           "creator_channel": c["channel"], "extra_links": list(c.get("extra_links") or []),
           "permission": c.get("permission", {}), "title": info.get("title", ""),
           "source_url": info.get("webpage_url") or (source if not p.exists() else ""),
           "duration": round(duration(d / "source.mp4"), 2), "added": datetime.now().isoformat(timespec="seconds")}
    (d / "source.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Clip {cid} added ({rec['duration']:.0f}s, {c['name']})")
    return cid


def get_clip(key, cid):
    f = clips_dir(key) / cid / "source.json"
    if not f.exists():
        raise ClipError(f"no clip '{cid}' in clips/{key} (see: python run.py clip-list --channel {key})")
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["file"] = str(f.parent / "source.mp4")
    return rec


def credit_text(rec):
    host = PLATFORMS.get(rec["platform"], "")
    handle = rec["creator_channel"].lstrip("@")
    return f"{host}/{'@' if rec['platform'] == 'youtube' else ''}{handle}" if host else rec["creator"]


def credit_lines(recs):
    seen, out = set(), []
    for r in recs:
        if r["creator"] in seen:
            continue
        seen.add(r["creator"])
        out.append(f"🎮 {r['creator']}: {r['creator_url']}")
        out += [f"   {u}" for u in r.get("extra_links") or [] if u != r["creator_url"]]
    return out


# ---------- rendering ----------
def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]
    return f"{datetime.now():%Y%m%d-%H%M}-{s or 'clip'}"


def _ass_header(W, H, ch, styles):
    return ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", ASS_STYLE_FORMAT, *styles, "",
            "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]


def _ass_escape(t):
    return str(t).replace("{", "(").replace("}", ")").replace("\n", " ")


def short_ass(path, ch, rec, hook, total):
    W, H = 1080, 1920
    font = ch.get("font", "Anton")
    white, accent = _ass_color(ch.get("caption_color", "#FFFFFF")), _ass_color(ch.get("highlight_color", "#F5C542"))
    lines = _ass_header(W, H, ch, [
        f"Style: Hook,{font},108,{white},{white},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,8,4,8,60,60,0,1",
        f"Style: Credit,{font},52,{accent},{accent},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,5,2,2,40,40,0,1",
        f"Style: Brand,{font},44,{accent},{accent},&H00000000,&H00000000,1,0,0,0,100,100,6,0,1,0,0,8,0,0,0,1"])
    ev = [f"Dialogue: 1,{_ts(0)},{_ts(total)},Brand,,0,0,0,,{{\\an8\\pos({W // 2},90)}}{_ass_escape(ch['name'].upper())}"]
    if hook:
        ev.append(f"Dialogue: 3,{_ts(0)},{_ts(min(total, 4.5))},Hook,,0,0,0,,{{\\an8\\pos({W // 2},300)"
                  f"\\fscx85\\fscy85\\t(0,180,\\fscx100\\fscy100)\\fad(120,250)}}{_ass_escape(hook.upper())}")
    ev.append(f"Dialogue: 2,{_ts(0)},{_ts(total)},Credit,,0,0,0,,{{\\an2\\pos({W // 2},1560)}}"
              f"CLIP: {_ass_escape(rec['creator'].upper())}\\N{{\\fs40\\c{white}&}}{_ass_escape(credit_text(rec))}")
    Path(path).write_text("\n".join(lines + ev) + "\n", encoding="utf-8-sig")


def _write_review(cfg, key, slug, meta, script_text, build):
    import run as core
    out_dir = core.dirs(cfg)["review"] / key / slug
    work = core.dirs(cfg)["work"] / f"clip-{slug}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        build(work, out_dir / "video.mp4")
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    meta["length_sec"] = round(duration(out_dir / "video.mp4"), 1)
    meta.setdefault("script", meta.get("description", ""))  # review cards show this (clips have no narration)
    meta["created"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "script.txt").write_text(script_text, encoding="utf-8")
    return out_dir


def short(cfg, key, cid, start, end, title, hook=None, log=print, gameplay_height=None):
    """A vertical highlight Short from one clip (start/end in seconds within the clip)."""
    ch = cfg["channels"][key]
    rec = get_clip(key, cid)
    end = min(end if end else rec["duration"], rec["duration"])
    if not 0 <= start < end:
        raise ClipError("need 0 <= start < end")
    if end - start > SHORT_MAX_S:
        raise ClipError(f"a highlight Short is at most {SHORT_MAX_S:.0f} s (asked {end - start:.0f} s)")
    total = end - start
    tags = list(dict.fromkeys(ch.get("hashtags", [])))
    desc = "\n".join([f"{title}", "", "Clip credit (used with permission):", *credit_lines([rec]),
                      *( [f"Original clip: {rec['source_url']}"] if rec.get("source_url") else [])])
    meta = {"kind": "clip", "channel": key, "channel_name": ch["name"], "title": title[:100], "topic": title,
            "hook_text": hook, "description": desc, "hashtags": tags, "clips": [cid], "creators": [rec["creator"]],
            "contains_synthetic_media": False}

    def build(work, out_mp4):
        short_ass(work / "short.ass", ch, rec, hook, total)
        fg_h = int(gameplay_height or ch.get("short_gameplay_height", 760)) // 2 * 2  # 608 = whole frame (keeps a corner facecam)
        vf = (f"[0:v]split=2[a][b];"
              f"[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2,eq=brightness=-0.25[bg];"
              f"[b]scale=1080:{fg_h}:force_original_aspect_ratio=increase,crop=1080:{fg_h}[fg];"
              f"[bg][fg]overlay=0:(H-h)/2,fps=30,subtitles=short.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}',"
              f"format=yuv420p[v];[0:a]loudnorm=I=-14:TP=-1.5,aresample=48000[au]")
        log("Rendering highlight Short...")
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{total:.2f}", "-i", rec["file"],
             "-filter_complex", vf, "-map", "[v]", "-map", "[au]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_mp4.resolve())], cwd=work)

    out = _write_review(cfg, key, _slug(title), meta, f"{title}\n\nClip: {cid} {start:.1f}-{end:.1f}s by {rec['creator']}\n", build)
    log(f"Highlight Short ready for review -> {out}")
    return out


def _chapters(segments):
    """YouTube chapters (first at 0:00, >= 3 entries, each >= 10 s) or [] if they don't qualify."""
    if len(segments) < 3 or any(d < 10 for _, d, _ in segments):
        return []
    return [f"{int(t // 60)}:{int(t % 60):02d} {label}" for t, _, label in segments]


def compile_video(cfg, key, cids, title, log=print):
    """A 16:9 compilation of several clips with a credit lower-third on each and chapters."""
    ch = cfg["channels"][key]
    recs = [get_clip(key, c) for c in cids]
    if len(recs) < 2:
        raise ClipError("a compilation needs at least 2 clips")
    W, H, xf = 1920, 1080, 0.5
    font = ch.get("font", "Anton")
    white, accent = _ass_color(ch.get("caption_color", "#FFFFFF")), _ass_color(ch.get("highlight_color", "#F5C542"))
    starts, t = [], 0.0
    for i, r in enumerate(recs):
        starts.append(t)
        t += r["duration"] - (xf if i < len(recs) - 1 else 0)
    total = t
    segments = [(s, r["duration"], (r.get("title") or f"Clip by {r['creator']}")[:60]) for s, r in zip(starts, recs)]
    chapters = _chapters(segments)
    desc = "\n".join([title, "", *(["Chapters:", *chapters, ""] if chapters else []),
                      "Clips used with permission from:", *credit_lines(recs)])
    meta = {"kind": "clip_compilation", "channel": key, "channel_name": ch["name"], "title": title[:100],
            "topic": title, "description": desc, "hashtags": [h for h in ch.get("hashtags", []) if h != "#shorts"],
            "clips": cids, "creators": sorted({r["creator"] for r in recs}), "contains_synthetic_media": False}

    def build(work, out_mp4):
        lines = _ass_header(W, H, ch, [
            f"Style: Lower,{font},54,{white},{white},&H00000000,&H96000000,1,0,0,0,100,100,1,0,3,14,0,1,60,60,70,1",
            f"Style: Brand,{font},34,{accent},{accent},&H00000000,&H00000000,1,0,0,0,100,100,4,0,1,0,0,9,40,40,30,1"])
        ev = [f"Dialogue: 1,{_ts(0)},{_ts(total)},Brand,,0,0,0,,{_ass_escape(ch['name'].upper())}"]
        for s, r in zip(starts, recs):
            ev.append(f"Dialogue: 2,{_ts(s + 0.4)},{_ts(s + 6)},Lower,,0,0,0,,{{\\fad(200,300)}}"
                      f"{{\\c{accent}&}}CLIP: {_ass_escape(r['creator'].upper())}{{\\c{white}&}}  {_ass_escape(credit_text(r))}")
        Path(work / "comp.ass").write_text("\n".join(lines + ev) + "\n", encoding="utf-8-sig")
        inputs, chains = [], []
        for i, r in enumerate(recs):
            inputs += ["-i", r["file"]]
            chains.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                          f"fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{i}]")
            chains.append(f"[{i}:a]loudnorm=I=-16:TP=-1.5,aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
        lv, la = "v0", "a0"
        for i in range(1, len(recs)):
            chains.append(f"[{lv}][v{i}]xfade=transition=fade:duration={xf}:offset={starts[i]:.3f}[xv{i}]")
            chains.append(f"[{la}][a{i}]acrossfade=d={xf}[xa{i}]")
            lv, la = f"xv{i}", f"xa{i}"
        chains.append(f"[{lv}]subtitles=comp.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}'[vout]")
        (work / "graph.txt").write_text(";\n".join(chains), encoding="utf-8")
        log(f"Rendering compilation ({len(recs)} clips, {total / 60:.1f} min)...")
        run(["ffmpeg", "-y", "-v", "error", *inputs, "-/filter_complex", "graph.txt", "-map", "[vout]", "-map", f"[{la}]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(out_mp4.resolve())], cwd=work)

    out = _write_review(cfg, key, _slug(title), meta, title + "\n\n" + "\n".join(
        f"{c} by {r['creator']}" for c, r in zip(cids, recs)) + "\n", build)
    log(f"Compilation ready for review -> {out}")
    return out
