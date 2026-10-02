"""Story mode - narration: ElevenLabs per chunk (cached on disk), joined with pauses.

Every chunk's mp3 + alignment is cached under stories/<slug>/cache/<lang>/ keyed by
voice + model + settings + text, so a failed or repeated build never pays twice for the
same audio, and editing one paragraph only re-records the chunks that changed.
"""
import hashlib
import json
from pathlib import Path

from .assemble import duration, run
from .storylib import char_times, chunk_offsets
from .voice import check_voice_id, tts_with_timestamps

RATE = 44100


def chunk_key(voice, model, settings, text):
    raw = json.dumps([voice, model, settings, text], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def plan_cost(units, cache_dir, voice, model, settings, credits_per_char):
    """(chars_to_record, credits, cached_count) for units not in the cache yet."""
    chars, cached = 0, 0
    for u in units:
        k = chunk_key(voice, model, settings, u["text"])
        if (Path(cache_dir) / f"{k}.mp3").exists() and (Path(cache_dir) / f"{k}.json").exists():
            cached += 1
        else:
            chars += len(u["text"])
    return chars, chars * credits_per_char, cached


def record(ch, units, cache_dir, model, settings, language, log=print):
    """Record every unit (using the cache). Adds 'mp3' and 'alignment' to each unit."""
    voice = check_voice_id(ch)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for i, u in enumerate(units):
        k = chunk_key(voice, model, settings, u["text"])
        mp3, al = cache_dir / f"{k}.mp3", cache_dir / f"{k}.json"
        if not (mp3.exists() and al.exists()):
            log(f"  recording chunk {i + 1}/{len(units)} ({len(u['text'])} chars)")
            prev_t = units[i - 1]["text"][-300:] if i else None
            next_t = units[i + 1]["text"][:300] if i + 1 < len(units) else None
            tmp = cache_dir / f"{k}.part.mp3"
            alignment = tts_with_timestamps(voice, u["text"], tmp, model, settings,
                                            previous_text=prev_t, next_text=next_t,
                                            # only the v2.5 models accept a forced language
                                            language=language if "v2_5" in model else None)
            al.write_text(json.dumps(alignment or {}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(mp3)
        u["mp3"] = str(mp3)
        u["alignment"] = json.loads(al.read_text(encoding="utf-8"))
    return units


def _to_wav(src, dst):
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(RATE),
         "-c:a", "pcm_s16le", str(dst)])


def _silence(seconds, dst):
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={RATE}:cl=mono",
         "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(dst)])


def join(units, workdir, out_wav):
    """Join recorded units into one WAV with each unit's pause_after as silence.
    Returns the total length; each unit gets 'offset', 'duration' and global 'times'."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    files, durations, pauses = [], [], []
    for i, u in enumerate(units):
        wav = workdir / f"chunk_{i:03d}.wav"
        _to_wav(u["mp3"], wav)
        durations.append(duration(wav))
        pauses.append(float(u.get("pause_after", 0.0)))
        files.append(wav)
        if pauses[-1] > 0:
            sil = workdir / f"pause_{i:03d}.wav"
            _silence(pauses[-1], sil)
            files.append(sil)
    lst = workdir / "voice_parts.txt"
    lst.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst.name,
         "-c", "copy", str(Path(out_wav).resolve())], cwd=workdir)
    offsets, total = chunk_offsets(durations, pauses)
    for u, off, dur in zip(units, offsets, durations):
        u["offset"], u["duration"] = off, dur
        u["times"] = [(s + off, e + off) for s, e in char_times(u["text"], u["alignment"])]
    for f in files:
        f.unlink(missing_ok=True)
    return duration(out_wav)
