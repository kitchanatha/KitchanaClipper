"""Faceless Shorts Pipeline

  python run.py generate --channel mindset_minute --count 2   make videos for review
  python run.py daily                                          make daily_count videos for every channel
  python run.py review                                         watch and approve / reject
  python run.py upload                                         upload approved videos (scheduled)
  python run.py auth --channel storytime_cuts                  connect a YouTube channel (once)
  python run.py test-video                                     offline test: no API keys needed
  python run.py generate --channel X --script my_script.json   use your own script (skips Claude)

Story mode (Candlelit Tales, README section 7):
  python run.py story-check the-monkeys-paw                     validate rights + show length / cost
  python run.py story-prep  the-monkeys-paw                     clean + translate + scripts (no audio cost)
  python run.py story-build the-monkeys-paw --lang en           narrate + render (asks before paying)
  python run.py story-build the-monkeys-paw --lang en --trailers-only   just the 3 Shorts trailers
  python run.py fonts                                           download all fonts into fonts/
"""
import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from pipeline.common import load_config, slugify, load_history, save_history, ROOT


def dirs(cfg):
    base = Path(cfg["output_dir"])
    d = {k: base / k for k in ("review", "approved", "uploaded", "rejected", "work")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def check_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("FFmpeg is not installed or not on PATH - see README step 1.")


def channel_keys(cfg, only=None, shorts_only=False):
    keys = list(cfg["channels"])
    if shorts_only:  # story channels are made with story-build, not the Shorts generator
        keys = [k for k in keys if cfg["channels"][k].get("type") != "story"]
    if only:
        if only not in keys:
            sys.exit(f"Unknown channel '{only}'. Options: {', '.join(keys)}")
        return [only]
    return keys


def generate_one(cfg, key, script_file=None, script=None, folder=None, extra_meta=None,
                 voice_cache=None, footage_exclude=(), strict_footage=False):
    """Make one Short. script / folder / extra_meta / voice_cache / footage_exclude let story
    mode reuse this for trailers (a cached voice makes re-doing the visuals free)."""
    from pipeline.script import write_script
    from pipeline.voice import make_voice
    from pipeline.footage import get_footage
    from pipeline.assemble import build_video

    ch = cfg["channels"][key]
    hist = load_history()
    h = hist.setdefault(key, {"topics": [], "pexels_ids": []})
    if script:
        s = script
    else:
        print(f"[{ch['name']}] writing script...")
        s = load_script(script_file) if script_file else write_script(cfg, ch, h["topics"])
    print(f"[{ch['name']}] '{s['title']}'")

    if folder is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = dirs(cfg)["review"] / key / f"{stamp}_{slugify(s.get('topic') or s['title'])}"
    folder = Path(folder)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    work = dirs(cfg)["work"] / folder.name
    work.mkdir(parents=True, exist_ok=True)
    try:
        words = cached_voice(cfg, ch, s["script"], folder / "voice.mp3", voice_cache)
        from pipeline.assemble import duration
        secs = duration(folder / "voice.mp3")
        print(f"[{ch['name']}] finding footage for {secs:.0f}s...")
        clips, ids = get_footage(s["keywords"], ch.get("footage_hint", ""), secs,
                                 float(cfg["video"]["clip_seconds"]), work, h["pexels_ids"][-300:],
                                 exclude=footage_exclude,
                                 strict=strict_footage or cfg["video"].get("strict_footage", False))
        print(f"[{ch['name']}] building video...")
        total = build_video(cfg, ch, clips, folder / "voice.mp3", words, work, folder / "video.mp4")
        meta = {**s, "channel": key, "channel_name": ch["name"], "length_sec": round(total, 1),
                "created": datetime.now().isoformat(timespec="seconds"), "pexels_ids": ids,
                **(extra_meta or {})}
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "script.txt").write_text(s["title"] + "\n\n" + s["script"] + "\n", encoding="utf-8")
        h["topics"].append(s.get("topic") or s["title"])
        h["pexels_ids"].extend(ids)
        save_history(hist)
        print(f"[{ch['name']}] done -> {folder}")
        return folder
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # don't leave half-made videos in review/
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cached_voice(cfg, ch, text, out_mp3, cache_dir=None):
    """make_voice, optionally cached on disk (mp3 + word timings) so the same text is never
    paid for twice."""
    from pipeline.voice import make_voice
    name = ch["name"]
    if not cache_dir:
        print(f"[{name}] recording voice...")
        return make_voice(cfg, ch, text, out_mp3)
    from pipeline.narrate import chunk_key
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    k = chunk_key(ch.get("voice_id"), cfg["elevenlabs"]["model_id"], "short", text)
    mp3, wj = cache_dir / f"{k}.mp3", cache_dir / f"{k}.words.json"
    if mp3.exists() and wj.exists():
        print(f"[{name}] voice from cache (no credits used)")
    else:
        print(f"[{name}] recording voice...")
        words = make_voice(cfg, ch, text, cache_dir / f"{k}.part.mp3")
        wj.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        (cache_dir / f"{k}.part.mp3").replace(mp3)
    shutil.copyfile(mp3, out_mp3)
    return json.loads(wj.read_text(encoding="utf-8"))


def load_script(path):
    """Read a hand-written script: JSON with title, script, keywords (+ optional topic,
    description, hashtags) - same shape Claude returns."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("title", "script", "keywords"):
        if not data.get(k):
            raise RuntimeError(f"{path} is missing '{k}'")
    return data


def cmd_generate(cfg, channel, count, script_file=None):
    check_ffmpeg()
    failures = 0
    for key in channel_keys(cfg, channel, shorts_only=True):
        for _ in range(count):
            try:
                generate_one(cfg, key, script_file)
            except SystemExit:
                raise
            except Exception as e:
                failures += 1
                print(f"[{key}] FAILED: {e}")
                log = ROOT / "data" / "errors.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"\n--- {datetime.now()} {key}\n{traceback.format_exc()}")
    print(f"\nFinished. {failures} failure(s). Next: python run.py review")


def open_file(path):
    try:
        if platform.system() == "Windows":
            os.startfile(path)  # noqa
        elif platform.system() == "Darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception:
        pass


def cmd_review(cfg):
    d = dirs(cfg)
    items = sorted(p for p in d["review"].glob("*/*") if (p / "video.mp4").exists())
    if not items:
        print("Nothing to review.")
        return
    for p in items:
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        print("\n" + "=" * 60)
        print(f"{meta['channel_name']}  |  {meta['title']}  ({meta['length_sec']}s)")
        print("-" * 60)
        print(meta["script"])
        open_file(p / "video.mp4")
        ans = input("\n[a]pprove  [r]eject  [s]kip  [q]uit > ").strip().lower()
        if ans == "q":
            break
        if ans in ("a", "r"):
            dest = d["approved" if ans == "a" else "rejected"] / p.parent.name / p.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            print("Approved." if ans == "a" else "Rejected.")


def decide(cfg, folder, approve):
    """Move a reviewed video to approved/ or rejected/. Returns the new path."""
    d = dirs(cfg)
    folder = Path(folder)
    dest = d["approved" if approve else "rejected"] / folder.parent.name / folder.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(dest))
    return dest


def upload_one(cfg, key, folder):
    """Upload one approved video, log it, move to uploaded/. Returns (url, slot)."""
    from pipeline.youtube import upload
    folder = Path(folder)
    ch = cfg["channels"][key]
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    vid, slot = upload(cfg, key, ch, folder)
    url = f"https://youtube.com/shorts/{vid}"
    log = ROOT / "uploads.csv"
    new = not log.exists()
    with open(log, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Publish Date", "Channel", "Title", "Video URL", "Length (sec)", "Topic"])
        w.writerow([slot.strftime("%Y-%m-%d %H:%M"), ch["name"], meta["title"], url,
                    meta["length_sec"], meta.get("topic", "")])
    dest = dirs(cfg)["uploaded"] / key / folder.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(dest))
    return url, slot


def cmd_upload(cfg, channel):
    d = dirs(cfg)
    for key in channel_keys(cfg, channel):
        ch = cfg["channels"][key]
        for p in sorted((d["approved"] / key).glob("*")):
            if not (p / "video.mp4").exists():
                continue
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
            try:
                url, slot = upload_one(cfg, key, p)
            except SystemExit:
                raise
            except Exception as e:
                print(f"[{ch['name']}] upload failed for '{meta['title']}': {e}")
                if "quota" in str(e).lower():
                    print("YouTube daily upload quota reached - try again tomorrow.")
                    return
                continue
            print(f"[{ch['name']}] uploaded '{meta['title']}' (slot {slot:%Y-%m-%d %H:%M})  {url}")


def cmd_test(cfg, channel):
    """Build a sample video with fake voice + footage, to check FFmpeg and fonts."""
    from pipeline.assemble import build_video, run
    check_ffmpeg()
    key = channel_keys(cfg, channel)[0]
    ch = cfg["channels"][key]
    work = dirs(cfg)["work"] / "test"
    work.mkdir(parents=True, exist_ok=True)
    text = ("You're not broke because you earn too little. Most people wait for a raise "
            "to start saving. So flip it. Pay yourself first.")
    words, t = [], 0.3
    for w in text.split():
        dur = 0.18 + 0.045 * len(w)
        words.append({"word": w, "start": t, "end": t + dur})
        t += dur + 0.05
    voice = work / "voice.mp3"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={t + 0.3:.2f}",
         "-af", "volume=0.05", str(voice)])
    clips = []
    for i, src in enumerate(["testsrc2=size=1280x720:rate=30", "mandelbrot=size=720x1280:rate=30",
                             "cellauto=size=720x1280:rate=30"]):
        c = work / f"clip{i}.mp4"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", "6", "-pix_fmt", "yuv420p", str(c)])
        clips.append(str(c))
    out = ROOT / "test_video.mp4"
    build_video(cfg, ch, clips, voice, words, work, out)
    shutil.rmtree(work, ignore_errors=True)
    print(f"Test video written: {out}")
    open_file(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["generate", "daily", "review", "upload", "auth", "test-video",
                                        "story-check", "story-prep", "story-build", "fonts"])
    ap.add_argument("slug", nargs="?", help="story folder name (story-* commands)")
    ap.add_argument("--lang", help="story languages, e.g. en or en,th,zh (default: all in config)")
    ap.add_argument("--yes", action="store_true", help="story-build: don't ask before paying for audio")
    ap.add_argument("--force", action="store_true",
                    help="story-prep: redo cleaning/translation/scripts; story-build: rebuild approved")
    ap.add_argument("--no-trailers", action="store_true", help="story-build: long video only")
    ap.add_argument("--trailers-only", action="store_true",
                    help="story-build: only the 3 Shorts trailers per language (cheap test)")
    ap.add_argument("--trailer", help="story-build: only these trailer numbers, e.g. 2,3")
    ap.add_argument("--channel", help="channel key from config.yaml (default: all)")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--script", help="JSON file with your own script (generate only, skips Claude)")
    a = ap.parse_args()
    cfg = load_config()
    if a.command == "generate":
        if a.script and (not a.channel or a.count != 1):
            sys.exit("--script needs --channel and makes one video")
        cmd_generate(cfg, a.channel, a.count, a.script)
    elif a.command == "daily":
        cmd_generate(cfg, a.channel, int(cfg.get("daily_count", 1)))
    elif a.command == "review":
        cmd_review(cfg)
    elif a.command == "upload":
        cmd_upload(cfg, a.channel)
    elif a.command == "auth":
        if not a.channel:
            sys.exit("Use --channel, e.g. python run.py auth --channel mindset_minute")
        from pipeline.youtube import auth
        auth(cfg["channels"][a.channel])
    elif a.command == "test-video":
        cmd_test(cfg, a.channel)
    elif a.command == "fonts":
        from pipeline.fonts import download_fonts
        download_fonts(cfg["fonts_dir"], force=a.force)
    elif a.command.startswith("story-"):
        if not a.slug:
            sys.exit(f"Usage: python run.py {a.command} <story-folder>")
        from pipeline import story
        if a.command == "story-check":
            story.check(cfg, a.slug)
        elif a.command == "story-prep":
            story.prep(cfg, a.slug, a.lang, force=a.force)
        else:
            check_ffmpeg()
            from pipeline.storybuild import build
            which = [int(x) for x in a.trailer.split(",")] if a.trailer else None
            made = build(cfg, a.slug, a.lang, yes=a.yes, force=a.force, trailers=not a.no_trailers,
                         long=not (a.trailers_only or which), which=which)
            if made:
                print("\nReady for review:\n  " + "\n  ".join(str(p) for group in made for p in group))


if __name__ == "__main__":
    main()
