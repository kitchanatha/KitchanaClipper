"""Step 2 - voiceover with ElevenLabs, plus word timings for captions."""
import base64
import re

import requests

from .common import env

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"


def check_voice_id(ch):
    voice = ch.get("voice_id", "")
    if not voice or voice.startswith("PASTE"):
        raise SystemExit(f"Set voice_id for {ch['name']} in config.yaml")
    return voice


def tts_with_timestamps(voice, text, out_mp3, model_id, voice_settings,
                        previous_text=None, next_text=None, language=None):
    """One ElevenLabs request. Writes the mp3 and returns the character alignment."""
    body = {"text": text, "model_id": model_id, "voice_settings": voice_settings}
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text
    if language:
        body["language_code"] = language
    r = requests.post(
        API.format(voice=voice),
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": env("ELEVENLABS_API_KEY"), "content-type": "application/json"},
        json=body,
        timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:500]}")
    data = r.json()
    with open(out_mp3, "wb") as f:
        f.write(base64.b64decode(data["audio_base64"]))
    return data.get("alignment") or data.get("normalized_alignment")


def make_voice(cfg, ch, text, out_mp3):
    voice = check_voice_id(ch)
    al = tts_with_timestamps(voice, text, out_mp3, cfg["elevenlabs"]["model_id"],
                             {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3})
    return word_timings(text, al, ch.get("language", "en"))


def _split_words(text, language):
    """Return (word, start_char, end_char) for caption words."""
    spans = []
    if language == "th":
        try:
            from pythainlp.tokenize import word_tokenize
        except ImportError:
            raise SystemExit("Thai captions need pythainlp: pip install pythainlp")
        pos = 0
        for tok in word_tokenize(text, engine="newmm", keep_whitespace=True):
            if tok.strip():
                spans.append((tok, pos, pos + len(tok)))
            pos += len(tok)
        # merge tiny Thai tokens so captions show readable chunks (~6+ chars); punctuation always
        # stays with the word before it; merged chunks keep the original spaces ("2 ต.ค.")
        merged = []
        for w, s, e in spans:
            if merged and re.fullmatch(r"[.,!?…:;)\]\"'”’]+", w):
                ps = merged[-1][1]
                merged[-1] = (text[ps:e], ps, e)
            elif merged and len(merged[-1][0]) < 6 and not re.match(r"[\s.,!?]", w):
                ps = merged[-1][1]
                merged[-1] = (text[ps:e], ps, e)
            else:
                merged.append((w, s, e))
        return merged
    if language == "zh":
        # one caption "word" per Han character (punctuation stays with the character before it);
        # runs of latin letters / digits stay together
        for m in re.finditer(r"[A-Za-z0-9]+[^\sA-Za-z0-9一-鿿]*|[一-鿿][^\sA-Za-z0-9"
                             r"一-鿿]*", text):
            spans.append((m.group(0), m.start(), m.end()))
        return spans
    for m in re.finditer(r"\S+", text):
        spans.append((m.group(0), m.start(), m.end()))
    return spans


def word_timings(text, alignment, language="en"):
    words = _split_words(text, language)
    chars = alignment["characters"] if alignment else []
    starts = alignment["character_start_times_seconds"] if alignment else []
    ends = alignment["character_end_times_seconds"] if alignment else []
    if "".join(chars) != text:
        # Fallback: spread words evenly over the audio length.
        total = ends[-1] if ends else len(words) * 0.4
        n = max(len(words), 1)
        return [{"word": w, "start": i * total / n, "end": (i + 1) * total / n}
                for i, (w, _, _) in enumerate(words)]
    out, prev_end = [], None
    for w, s, e in words:
        out.append({"word": w, "start": float(starts[s]), "end": float(ends[e - 1]),
                    # Thai / Chinese captions join words without spaces - except where the
                    # script had one (e.g. between Thai words and a date or number)
                    "space_before": prev_end is not None and bool(text[prev_end:s].strip() == ""
                                                                    and text[prev_end:s])})
        prev_end = e
    return out
