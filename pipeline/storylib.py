"""Story mode - pure helpers (no network, no FFmpeg) so they can be unit-tested.

Rights validation, text cleaning, narration chunking, timestamp maths, subtitle line
breaking (English / Thai / Chinese), SRT + ASS output, credits and chapter text.
"""
import difflib
import re
import unicodedata
from datetime import date
from pathlib import Path

LANG_NAMES = {"en": "English", "th": "Thai", "zh": "Simplified Chinese"}


# ---------------------------------------------------------------- rights

class RightsError(Exception):
    pass


def us_public_domain_cutoff(year=None):
    """Works published before this year are public domain in the US (95-year term).
    2026 -> 1931, i.e. only works published in 1930 or earlier."""
    return (year or date.today().year) - 95


def validate_rights(meta, story_dir, year=None):
    """Raise RightsError (with every problem listed) unless the story may be used.

    public_domain: published before the US cutoff AND author died more than 50 years ago
                   (Thailand: life + 50).
    permission:    permission_proof must point to an existing file in the story folder.
    """
    year = year or date.today().year
    meta = meta or {}
    problems = []
    for k in ("title", "author", "source_url"):
        if not str(meta.get(k) or "").strip():
            problems.append(f"'{k}' is missing")
    rights = str(meta.get("rights") or "").strip()
    if rights == "public_domain":
        pub, death = meta.get("year_published"), meta.get("author_death_year")
        cutoff = us_public_domain_cutoff(year)
        if not isinstance(pub, int):
            problems.append("'year_published' must be a year, e.g. 1902")
        elif pub >= cutoff:
            problems.append(f"published {pub}: only works published before {cutoff} are public "
                            "domain in the US")
        if not isinstance(death, int):
            problems.append("'author_death_year' must be a year - public domain in Thailand needs "
                            "an author who died more than 50 years ago")
        elif year - death <= 50:
            problems.append(f"author died {death}: Thailand protects works for 50 years after the "
                            f"author's death (free from {death + 51})")
    elif rights == "permission":
        proof = str(meta.get("permission_proof") or "").strip()
        if not proof:
            problems.append("'permission_proof' is missing (path to the email/screenshot)")
        elif not (Path(story_dir) / proof).is_file():
            problems.append(f"permission_proof file not found: {proof}")
    else:
        problems.append("'rights' must be public_domain or permission")
    if problems:
        raise RightsError("Story rights check failed:\n  - " + "\n  - ".join(problems))
    return True


# ---------------------------------------------------------------- cleaning

_GB_START = re.compile(r"^\*{3}\s*START OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
_GB_END = re.compile(r"^\*{3}\s*END OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
_GB_END_OLD = re.compile(r"^\s*End of (the )?Project Gutenberg.*$", re.I | re.M)
_BOILER = re.compile(r"gutenberg|^produced by|^transcriber'?s? note|^\[illustration", re.I)
_HEADING = re.compile(
    r"^((chapter|part|book)\s+)?([IVXLC]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\.?$|^[一二三四五六七八九十]+[、.．]?$|^(第.{1,4}[章部节])$|^(ตอนที่|บทที่)\s*\S+$", re.I)


def is_heading(par):
    return bool(_HEADING.match(par.strip()))


def clean_text(raw, starts_with=None, ends_with=None):
    """Strip Project Gutenberg boilerplate, unwrap hard-wrapped lines into paragraphs,
    keep chapter headings as their own paragraphs. Returns the text with paragraphs
    separated by one blank line.

    starts_with / ends_with (from story.yaml) drop front matter such as a collection's title
    page: everything before the first paragraph starting with starts_with, and after the
    first paragraph (from there on) ending with ends_with."""
    t = raw.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    m = _GB_START.search(t)
    if m:
        t = t[m.end():]
    m = _GB_END.search(t) or _GB_END_OLD.search(t)
    if m:
        t = t[:m.start()]
    paras = []
    for block in re.split(r"\n\s*\n", t):
        p = " ".join(line.strip() for line in block.split("\n") if line.strip())
        p = re.sub(r"\s+", " ", p).strip()
        p = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", p)  # _italics_
        p = p.replace("--", "—")
        if not p or _BOILER.search(p):
            continue
        paras.append(p)
    if starts_with:
        i = next((i for i, p in enumerate(paras) if p.startswith(starts_with.strip())), None)
        if i is None:
            raise ValueError(f"starts_with {starts_with!r} not found in the text")
        paras = paras[i:]
    if ends_with:
        i = next((i for i, p in enumerate(paras) if p.endswith(ends_with.strip())), None)
        if i is None:
            raise ValueError(f"ends_with {ends_with!r} not found in the text")
        paras = paras[:i + 1]
    return "\n\n".join(paras)


_CJK = r"[　-〿一-鿿＀-￯“”‘’]"
_ZH_PUNCT = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！", "(": "（", ")": "）"}


def fix_zh_punct(text):
    """Half-width , ; : ? ! ( ) next to Chinese characters -> full-width (，；：？！（）)."""
    pat = re.compile(rf"(?<={_CJK})\s*([,;:?!()])|([,;:?!()])\s*(?={_CJK})")
    return pat.sub(lambda m: _ZH_PUNCT[m.group(1) or m.group(2)], text)


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def word_count(text, lang):
    if lang == "en":
        return len(re.findall(r"\S+", text))
    return sum(1 for c in text if not c.isspace())


# ---------------------------------------------------------------- sentences & chunks

_CLOSERS = "\"'”’)」』》】"


def sentence_spans(text, lang):
    """(start, end) spans of sentences in text, whitespace excluded. Paragraph breaks always
    end a sentence. Thai separates sentences/clauses with spaces, so spaces split there."""
    spans = []
    if lang == "th":
        pattern = re.compile(r"\S+")
    elif lang == "zh":
        pattern = re.compile(r"[^\s。！？!?…]*(?:[。！？!?]+|…+|$)[" + re.escape(_CLOSERS) + r"]*")
    else:  # don't end a sentence at Mr. / Mrs. / Dr. / St. / Ms.
        pattern = re.compile(r"\S.*?(?:(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bSt)(?<!\bMs)[.!?…]+["
                             + re.escape(_CLOSERS) + r"]*(?=\s|$)|(?=\n\s*\n)|$)", re.S)
    for m in pattern.finditer(text):
        s, e = m.span()
        seg = text[s:e]
        # never let a sentence run across a paragraph break
        for part in re.finditer(r"(?:(?!\n\s*\n).)+", seg, re.S):
            ps, pe = s + part.start(), s + part.end()
            while ps < pe and text[ps].isspace():
                ps += 1
            while pe > ps and text[pe - 1].isspace():
                pe -= 1
            if pe > ps:
                spans.append((ps, pe))
    return spans


def split_for_narration(text, lang, max_chars):
    """Group paragraphs into chunks of at most max_chars, breaking only between paragraphs,
    or between sentences when one paragraph is too long. Returns a list of strings
    (paragraphs inside a chunk are separated by a blank line)."""
    pieces = []  # (text, joiner_before)
    for par in paragraphs(text):
        if len(par) <= max_chars:
            pieces.append((par, "\n\n"))
            continue
        first = True
        for s, e in sentence_spans(par, lang):
            sent = par[s:e]
            while len(sent) > max_chars:  # pathological sentence: hard split at a space
                cut = sent.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                pieces.append((sent[:cut].strip(), "\n\n" if first else " "))
                sent, first = sent[cut:].strip(), False
            pieces.append((sent, "\n\n" if first else " "))
            first = False
    chunks, cur = [], ""
    for piece, joiner in pieces:
        cand = piece if not cur else cur + joiner + piece
        if cur and len(cand) > max_chars:
            chunks.append(cur)
            cur = piece
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    return chunks


def group_for_translation(text, max_words):
    """Group paragraphs into translation chunks of about max_words words."""
    groups, cur, n = [], [], 0
    for p in paragraphs(text):
        w = len(p.split())
        if cur and n + w > max_words:
            groups.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        groups.append(cur)
    return groups


# ---------------------------------------------------------------- timing

def chunk_offsets(durations, pauses):
    """Start time of each audio chunk when chunks are joined with pauses[i] seconds of
    silence after chunk i. Returns (offsets, total_length)."""
    offsets, t = [], 0.0
    for i, d in enumerate(durations):
        offsets.append(round(t, 6))
        t += d + (pauses[i] if i < len(pauses) else 0.0)
    return offsets, t


def char_times(text, alignment):
    """Per-character (start, end) times for text, from ElevenLabs alignment. When the aligned
    characters differ from text (normalisation), match them with difflib and interpolate."""
    chars = (alignment or {}).get("characters") or []
    starts = (alignment or {}).get("character_start_times_seconds") or []
    ends = (alignment or {}).get("character_end_times_seconds") or []
    n = len(text)
    if not chars:
        return [(0.0, 0.0)] * n
    if "".join(chars) == text:
        return [(float(starts[i]), float(ends[i])) for i in range(n)]
    out = [None] * n
    sm = difflib.SequenceMatcher(None, text, "".join(chars), autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            out[a + k] = (float(starts[b + k]), float(ends[b + k]))
    # fill gaps by interpolating between known neighbours
    last_end, total = 0.0, float(ends[-1])
    i = 0
    while i < n:
        if out[i] is not None:
            last_end = out[i][1]
            i += 1
            continue
        j = i
        while j < n and out[j] is None:
            j += 1
        nxt = out[j][0] if j < n else total
        step = (nxt - last_end) / (j - i + 1)
        for k in range(i, j):
            s = last_end + step * (k - i)
            out[k] = (s, s + step)
        i = j
    return out


# ---------------------------------------------------------------- line breaking

_NO_LINE_START = set("，。！？、；：,.!?;:)）」』》】”’…—")
_TH_NO_LINE_START = ("ๆ", "ฯ")  # Thai repetition / abbreviation marks stay with the word before


def display_width(s):
    """Visible width: Thai combining vowels/tone marks take no space; CJK counts 1 each."""
    return sum(0 if unicodedata.category(c) in ("Mn", "Me") else 1 for c in s)


def _tokens(text, lang):
    """(start, end) of breakable tokens. Breaks may happen between tokens."""
    if lang == "zh":
        return [(i, i + 1) for i in range(len(text))]
    if lang == "th":
        try:
            from pythainlp.tokenize import word_tokenize
            toks = word_tokenize(text, engine="newmm", keep_whitespace=True)
        except ImportError:
            toks = re.findall(r"\s+|\S+", text)
        out, pos = [], 0
        for tok in toks:
            out.append((pos, pos + len(tok)))
            pos += len(tok)
        return out
    return [m.span() for m in re.finditer(r"\s+|\S+", text)]


def wrap_spans(text, lang, max_chars):
    """Greedy line wrap. Returns (start, end) spans of each line in text (whitespace at the
    edges excluded). Thai lines break only between pythainlp words, Chinese between
    characters (never before closing punctuation), English between words."""
    toks = _tokens(text, lang)
    lines, ls, le, width = [], None, None, 0
    for s, e in toks:
        tok = text[s:e]
        if tok.isspace():  # counted once, as the gap before the next word (below)
            continue
        w = display_width(tok)
        if ls is None:
            ls, le, width = s, e, w
            continue
        sep = text[le:s]
        new_width = width + (display_width(" ") if sep and lang != "zh" else 0) + w
        keep = ((lang == "zh" and tok in _NO_LINE_START)  # kinsoku: punctuation stays on this line
                or (lang == "th" and tok.startswith(_TH_NO_LINE_START)))
        if new_width > max_chars and not keep:
            if lang == "zh":  # prefer breaking just after punctuation in the last few chars
                for back in range(le - 1, max(ls, le - 6), -1):
                    if text[back] in "，。！？、；：,.!?;:" and back + 1 < le:
                        lines.append((ls, back + 1))
                        ls = back + 1
                        break
            lines.append((ls, le))
            ls, le, width = s, e, w
        else:
            le, width = e, new_width
    if ls is not None:
        lines.append((ls, le))
    # strip whitespace at line edges
    out = []
    for s, e in lines:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def wrap_text(text, lang, max_chars):
    return [text[s:e] for s, e in wrap_spans(text, lang, max_chars)]


# ---------------------------------------------------------------- cues / subtitles

def build_cues(text, times, lang, max_chars, max_lines=2, min_dur=1.0, gap=0.05):
    """Sentence-level subtitle cues for one narration chunk.

    text  : the chunk text
    times : per-character (start, end) for text (see char_times), already shifted to
            the chunk's position in the full audio
    Returns [{'start', 'end', 'lines': [...]}]."""
    spans = sentence_spans(text, lang)
    if lang == "th":  # Thai "sentences" are short clauses: merge them into cue-sized groups
        merged, limit = [], max_chars * max_lines
        for s, e in spans:
            if (merged and "\n\n" not in text[merged[-1][1]:s]
                    and display_width(text[merged[-1][0]:e]) <= limit):
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        spans = merged
    cues = []
    for s, e in spans:
        line_spans = [(s + a, s + b) for a, b in wrap_spans(text[s:e], lang, max_chars)]
        for i in range(0, len(line_spans), max_lines):
            group = line_spans[i:i + max_lines]
            cs, ce = group[0][0], group[-1][1]
            cues.append({"start": times[cs][0], "end": times[ce - 1][1],
                         "lines": [text[a:b] for a, b in group]})
    return fix_cue_times(cues, min_dur, gap)


def fix_cue_times(cues, min_dur=1.0, gap=0.05):
    cues = sorted(cues, key=lambda c: c["start"])
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        if c["end"] - c["start"] < min_dur:
            c["end"] = c["start"] + min_dur
        if nxt is not None and c["end"] > nxt - gap:
            c["end"] = max(c["start"] + 0.2, nxt - gap)
    return cues


def srt_time(t):
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues):
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n" + "\n".join(c["lines"]))
    return "\n\n".join(out) + "\n"


def ass_time(t):
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_color, alpha=0):
    h = hex_color.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_escape(s):
    return s.replace("\\", "/").replace("{", "(").replace("}", ")")


# ---------------------------------------------------------------- chapters & credits

def fmt_timestamp(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters_text(chapters):
    """chapters: [(seconds, title)]. YouTube needs the first at 0:00, at least 3, each >= 10s."""
    chapters = sorted(chapters)
    clean = []
    for t, title in chapters:
        if clean and t - clean[-1][0] < 10:
            continue
        clean.append((t, title))
    if not clean or len(clean) < 3:
        return ""
    clean[0] = (0, clean[0][1])
    return "\n".join(f"{fmt_timestamp(t)} {title}" for t, title in clean)


def credits_text(story, lang, translator="Candlelit Tales"):
    lines = [f"Story: {story['title']}", f"Author: {story['author']}"]
    if story.get("year_published"):
        lines.append(f"First published: {story['year_published']}")
    if story.get("rights") == "permission":
        link = story.get("permission_link") or story.get("source_url")
        lines.append("Narrated with the author's permission" + (f": {link}" if link else ""))
    else:
        lines.append("Public domain")
    if lang != "en":
        lines.append(f"Translated by {translator}")
    lines.append(f"Original text: {story['source_url']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- spoken headings

_EN_NUM = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
           "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
           "eighteen", "nineteen", "twenty"]
_ZH_NUM = "零一二三四五六七八九十"


def roman_to_int(s):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = vals.get(ch, 0)
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def _zh_number(n):
    if n <= 10:
        return _ZH_NUM[n]
    if n < 20:
        return "十" + _ZH_NUM[n - 10]
    return _ZH_NUM[n // 10] + "十" + (_ZH_NUM[n % 10] if n % 10 else "")


def spoken_heading(par, lang):
    """'II.' -> 'Part two.' / 'ตอนที่ 2' / '第二部分'. Other text is returned unchanged."""
    m = re.match(r"^\s*(?:(?:chapter|part)\s+)?([IVXLC]+|\d+)\.?\s*$", par, re.I)
    if not m:
        return par
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else roman_to_int(raw)
    if lang == "th":
        return f"ตอนที่ {n}"
    if lang == "zh":
        return f"第{_zh_number(n)}部分。"
    word = _EN_NUM[n] if n < len(_EN_NUM) else str(n)
    return f"Part {word}."
