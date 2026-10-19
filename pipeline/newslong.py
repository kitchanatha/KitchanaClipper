"""สรุปให้ฟัง long recap - 16:9, 5-10 minutes, from the SAME approved research.json as the Short.

Structure: hook -> background ("เรื่องนี้เริ่มจาก...") -> 8-15 timeline beats -> each side's
position -> what happens next -> subscribe line, with the same safety rules, the same code
checks (only confirmed events, card sizes, no monarchy words) and the same Claude safety review.

Layout (1920x1080, channel colours, Kanit): logo top-left; the whole timeline as a column on the
left (current beat highlighted); a card on the right (date / section pill, 2-line summary,
sources); sentence subtitles at the bottom (also uploaded as .srt); slow darkened stock footage
behind. Intro title card during the hook; end screen for the last 20 s with empty space for
YouTube's end-screen elements. Thumbnail 1280x720: headline + date pill + timeline motif, no
photos. Narration (chunked, cached) and the windowed renderer come from story mode.
"""
import json
import random
import shutil
from datetime import datetime
from pathlib import Path

from . import longvideo as lv
from . import narrate
from .assemble import _filter_path, duration, run
from .news import (BKK, CHARS_PER_S, REVIEW_PROMPT, RULES, _facts_text, _json_in, news_dir,
                   read_json, write_json)
from .newslib import (card_fits, card_width, confirmed_facts, monarchy_hits, safety_problems,
                      sources_block, speakable, strip_date, thai_date, wrap_card)
from .newsvideo import _draw, _text, circle, logo, rrect
from .storylib import ass_color, ass_escape, ass_time, build_cues, chapters_text, to_srt, wrap_text

W, H = 1920, 1080
END_SCREEN = 20.0  # seconds kept for YouTube end-screen elements

LONG_PROMPT = """Write a Thai LONG-FORM YouTube recap script (16:9, about {s_lo}-{s_hi} seconds, i.e. \
{c_lo}-{c_hi} Thai characters of narration in total) for the channel "{channel}" (neutral news \
timeline recaps).

Use ONLY these confirmed facts (each event has an id; each was reported by 2+ outlets):
{facts}

{rules}
Structure and rules:
- hook: 2-3 neutral sentences that open with the key fact and end on a question. Nothing implying guilt.
- background: {{"event_ids": [...], "card_text": "...", "narration": "..."}} - how it started; the
  narration MUST begin with "เรื่องนี้เริ่มจาก". Use the earliest events.
- beats: {b_lo}-{b_hi} beats in time order (one or more event ids each). card_text: on-screen
  summary, max 40 Thai characters, NO date (a pill shows it), no names of minors/victims.
  narration: 3-5 sentences with the key numbers and who reported them; allegations as allegations.
- sides: one per party in the facts' "sides": {{"party": "...", "card_text": "max 40 chars",
  "narration": "their position, attributed (ระบุว่า / ชี้แจงว่า)"}}.
- next: {{"card_text": "max 40 chars", "narration": "what happens next - ONLY dates / steps that
  were announced in the facts; if nothing is announced, say that plainly"}}.
- closing: exactly "{closing}"
- title: max 90 characters, must contain "สรุป", no clickbait. thumb_headline: the thumbnail
  headline, max 6 Thai words / 22 characters. description: 2-3 neutral Thai sentences.
- hashtags: 4 Thai hashtags. keywords: 6 English search terms for NEUTRAL landscape stock video
  (documents, city lights at night, rain, charts, empty roads ...) - never people, faces, logos,
  events or recognisable places.
- LENGTH BUDGET (characters): hook 80-160, background 250-450, each beat 200-350, each side
  150-300, next 120-300. The code measures the total; outside {c_lo}-{c_hi} is rejected.
{fix}
Return ONLY JSON: {{"title": "...", "thumb_headline": "...", "hook": "...", "background": {{...}},
"beats": [{{"event_ids": ["e1"], "card_text": "...", "narration": "..."}}], "sides": [{{...}}],
"next": {{...}}, "closing": "...", "description": "...", "hashtags": ["#..."], "keywords": ["..."]}}"""


# ---------------------------------------------------------------- script

def long_parts(script):
    """[(part_id, label, text)] in speaking order."""
    parts = [("hook", "บทนำ", script["hook"].strip()),
             ("bg", "ที่มา", script["background"]["narration"].strip())]
    parts += [(f"b{i}", b["card_text"], b["narration"].strip()) for i, b in enumerate(script["beats"])]
    parts += [(f"s{i}", s.get("party", ""), s["narration"].strip()) for i, s in enumerate(script.get("sides") or [])]
    parts += [("next", "ต่อจากนี้", script["next"]["narration"].strip()),
              ("closing", "", script["closing"].strip())]
    return parts


def long_problems(script, data, b_lo, b_hi, closing):
    """Code checks on the long script (empty = OK)."""
    p = []
    ids = {e["id"] for e in confirmed_facts(data)["events"]}
    beats = script.get("beats") or []
    if not b_lo <= len(beats) <= b_hi:
        p.append(f"needs {b_lo}-{b_hi} timeline beats, got {len(beats)}")
    for part in beats + [script.get("background") or {}]:
        used = set(part.get("event_ids") or [])
        if not used:
            p.append(f"'{part.get('card_text', '')[:20]}' cites no event")
        elif not used <= ids:
            p.append(f"uses unconfirmed or unknown events: {sorted(used - ids)}")
    for part in beats + [script.get("background") or {}, script.get("next") or {}] + list(script.get("sides") or []):
        if not part.get("narration"):
            p.append("a section has no narration")
        if not card_fits(part.get("card_text", "")):
            p.append(f"card text too long ({card_width(part.get('card_text', ''))} chars): {part.get('card_text')}")
    if not str((script.get("background") or {}).get("narration", "")).strip().startswith("เรื่องนี้เริ่มจาก"):
        p.append('background narration must start with "เรื่องนี้เริ่มจาก"')
    title = script.get("title", "")
    if "สรุป" not in title or len(title) > 100:
        p.append("title must contain 'สรุป' and be at most 90 characters")
    if card_width(script.get("thumb_headline", "")) > 24 or not script.get("thumb_headline"):
        p.append("thumb_headline must be short (max ~22 characters)")
    if script.get("closing", "").strip() != closing:
        p.append("closing must be exactly: " + closing)
    everything = json.dumps({k: v for k, v in script.items() if k != "keywords"}, ensure_ascii=False)
    hits = monarchy_hits(everything)
    if hits:
        p.append("monarchy words in the script: " + ", ".join(hits))
    return p


def write_long_script(cfg, data, log=print):
    from .script import ask_claude
    nc = cfg["news"]
    closing = nc.get("long_closing", "กดติดตามสรุปให้ฟัง แล้วพบกันในเรื่องต่อไป")
    n_events = len(confirmed_facts(data)["events"])
    b_lo, b_hi = int(nc.get("long_min_beats", 8)), int(nc.get("long_max_beats", 15))
    if n_events < b_lo:
        raise SystemExit(f"The long recap needs at least {b_lo} confirmed events - this research has "
                         f"{n_events}. Research it again with 'Make Short + Long' (deeper search).")
    b_hi = min(b_hi, n_events)
    s_lo, s_hi = nc.get("long_seconds", [300, 540])
    lo, hi = s_lo * (CHARS_PER_S - 1), s_hi * CHARS_PER_S
    extra = nc.get("pronounce") or {}
    fix, problems = "", []
    for attempt in range(6):
        prompt = LONG_PROMPT.format(
            s_lo=s_lo, s_hi=s_hi, c_lo=lo, c_hi=hi, channel=cfg["channels"][nc["channel"]]["name"],
            facts=_facts_text(data), b_lo=b_lo, b_hi=b_hi, closing=closing, fix=fix,
            rules=RULES + "".join(f"\n- Editor's note: {n}" for n in data.get("script_notes", [])))
        script = _json_in(ask_claude(cfg, prompt, max_tokens=32000, timeout=900, effort="medium"))
        if script.get("closing", "").strip() == closing:
            script["closing"] = closing
        for part in (script.get("beats") or []) + [script.get("background") or {}, script.get("next") or {}]:
            part["card_text"] = strip_date(part.get("card_text", ""))
        problems = long_problems(script, data, b_lo, b_hi, closing)
        spoken = 0 if problems else sum(len(speakable(t, extra)) for _, _, t in long_parts(script))
        if spoken and not lo <= spoken <= hi:
            problems.append(f"narration is {spoken} characters - must be {lo}-{hi} ({s_lo}-{s_hi} s)")
        if not problems:
            notes = "".join(f"\nEditor's rule (also flag any breach): {n}" for n in data.get("script_notes", []))
            review = _json_in(ask_claude(cfg, REVIEW_PROMPT.format(
                facts=_facts_text(data) + notes, script=json.dumps(script, ensure_ascii=False, indent=1)),
                max_tokens=16000, timeout=900))
            if review.get("ok") and not review.get("issues"):
                return script
            problems = ["safety review: " + i for i in review.get("issues", [])] or ["safety review failed"]
        log(f"  long script attempt {attempt + 1} rejected: {'; '.join(problems)[:300]}")
        fix = ("Your previous draft was rejected for: " + "; ".join(problems) + ". Fix all of these. "
               "Revise THIS draft with the smallest changes that fix them:\n" + json.dumps(script, ensure_ascii=False))
    raise SystemExit("Could not write a long script that passes the checks:\n  - " + "\n  - ".join(problems))


# ---------------------------------------------------------------- overlay (.ass)

def _pill(layer, t0, t1, x, y, text, fill, txt_col, extra=""):
    w = max(220, 56 + int(card_width(text) * 23))
    return [_draw(layer, t0, t1, rrect(x, y, w, 64, 32), fill, extra),
            _text(layer + 1, t0, t1, "Pill", x + w // 2, y + 32, text, rf"\1c{txt_col}" + extra, an=5)]


def write_long_ass(path, ch, title, sections, cues, total, end_t, today_label):
    """sections: [{id, kind (hook|bg|beat|side|next|closing), start, end, pill, text, outlets, row}]"""
    accent, red = ass_color(ch["highlight_color"]), ass_color(ch.get("latest_color", "#E63946"))
    white, navy = ass_color(ch["caption_color"]), ass_color(ch["bg_color"])
    panel = ass_color(ch["bg_color"], 0x30)
    font, head = ch.get("font", "Kanit"), ch.get("title_font", "Kanit ExtraBold")
    styles = [
        f"Style: Sub,{font},50,{white},{white},&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,3,1,2,160,160,46,1",
        f"Style: Draw,{font},20,{white},{white},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Pill,{head},34,{navy},{navy},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        f"Style: Row,{font},30,{white},{white},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,4,0,0,0,1",
        f"Style: Card,{font},62,{white},{white},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Src,{font},30,{white},{white},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Head,{head},40,{white},{white},&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,2,7,0,0,0,1",
        f"Style: Title,{head},84,{white},{white},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,3,5,0,0,0,1",
        f"Style: Big,{head},60,{accent},{accent},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,2,5,0,0,0,1",
    ]
    ev = []
    intro_end = next(s["end"] for s in sections if s["kind"] == "hook")
    body_t0, body_t1 = intro_end, end_t
    # header the whole time
    ev += logo(0, total, 60, 52, 0.8, accent, red, white)
    ev.append(_text(6, 0, total, "Head", 190, 40, ch["name"]))

    # intro title card during the hook
    ev.append(_draw(0, 0, intro_end, f"m 0 0 l {W} 0 l {W} {H} l 0 {H}", panel, r"\fad(0,500)"))
    ev += logo(0, intro_end, W // 2 - 100, 250, 1.7, accent, red, white)
    for i, line in enumerate(wrap_text(title, "th", 26)[:3]):
        ev.append(_text(4, 0, intro_end, "Title", W // 2, 470 + 110 * i, line, r"\fad(400,400)", an=5))
    ev += _pill(4, 0, intro_end, W // 2 - 170, 760, today_label, accent, navy, r"\fad(400,400)")

    # timeline column on the left (every beat; current one highlighted)
    beats = [s for s in sections if s["kind"] == "beat"]
    if beats:
        top, bottom = 160, 880
        step = min(64, (bottom - top) // max(len(beats), 1))
        x_spine = 104
        ev.append(_draw(1, body_t0, body_t1, f"m {x_spine - 3} {top} l {x_spine + 3} {top} "
                                             f"l {x_spine + 3} {top + step * (len(beats) - 1) + 20} "
                                             f"l {x_spine - 3} {top + step * (len(beats) - 1) + 20}",
                        accent, r"\alpha&H70&\fad(400,300)"))
        for k, b in enumerate(beats):
            y = top + step * k
            short = wrap_text(b["text"], "th", 13)[0]
            short += "…" if short != b["text"] else ""
            phases = [(body_t0, b["start"], r"\alpha&HA8&"), (b["end"], body_t1, r"\alpha&H30&")]
            for a, z, al in phases:  # upcoming (dim) / done (normal)
                if z - a > 0.05:
                    ev.append(_draw(3, a, z, circle(x_spine, y + 10, 11), accent, al))
                    ev.append(_text(4, a, z, "Row", x_spine + 26, y + 10, b["pill"], rf"\1c{accent}" + al, an=4))
                    ev.append(_text(4, a, z, "Row", x_spine + 160, y + 10, short, al, an=4))
            ev.append(_draw(2, b["start"], b["end"], rrect(x_spine - 30, y - 14, 520, 48, 24), panel))
            ev.append(_draw(3, b["start"], b["end"], circle(x_spine, y + 10, 18), red, r"\blur3"))
            ev.append(_text(4, b["start"], b["end"], "Row", x_spine + 26, y + 10, b["pill"], rf"\1c{accent}", an=4))
            ev.append(_text(4, b["start"], b["end"], "Row", x_spine + 160, y + 10, short, "", an=4))

    # the card on the right for every section
    cx, cy, cw = 680, 230, 1150
    for s in sections:
        if s["kind"] in ("hook", "closing"):
            continue
        lines = wrap_card(s["text"], 30, 3)
        ch_h = 150 + 82 * len(lines) + (60 if s.get("outlets") else 0)
        ev.append((2, s["start"], s["end"], "Draw", rf"{{\an7\pos(0,0)\bord3\shad0\1c{panel}\3c{accent}"
                   rf"\fad(250,200)\p1}}" + rrect(cx, cy, cw, ch_h, 30)))
        pill_col = red if s["kind"] == "next" else accent
        pill_txt = white if s["kind"] == "next" else navy
        ev += _pill(3, s["start"], s["end"], cx + 40, cy + 34, s["pill"], pill_col, pill_txt, r"\fad(250,200)")
        for i, line in enumerate(lines):
            ev.append(_text(4, s["start"], s["end"], "Card", cx + 44, cy + 126 + 82 * i, line, r"\fad(250,200)"))
        if s.get("outlets"):
            ev.append(_text(4, s["start"], s["end"], "Src", cx + 46, cy + ch_h - 56,
                            "แหล่งข่าว: " + ", ".join(s["outlets"][:4]), r"\fad(250,200)"))

    # end screen: last 20 s, empty boxes where YouTube's end-screen elements go
    ev.append(_draw(0, end_t, total, f"m 0 0 l {W} 0 l {W} {H} l 0 {H}", panel, r"\fad(600,0)"))
    ev += logo(end_t, total, W // 2 - 90, 120, 1.5, accent, red, white)
    ev.append(_text(5, end_t, total, "Big", W // 2, 250, "กดติดตามสรุปให้ฟัง", r"\fad(600,0)", an=5))
    for x0 in (150, 1010):  # faint outlines only - YouTube draws its own elements here
        ev.append((1, end_t, total, "Draw", rf"{{\an7\pos(0,0)\bord2\shad0\1a&HFF&\3c{white}\3a&HB0&"
                   rf"\fad(600,0)\p1}}" + rrect(x0, 380, 760, 428, 24)))

    head_lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
                  "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
                  "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                  "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                  "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                  *styles, "", "[Events]",
                  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for c in cues:
        if c["start"] < end_t:
            ev.append((8, c["start"], min(c["end"], end_t), "Sub", r"\N".join(ass_escape(x) for x in c["lines"])))
    ev.sort(key=lambda e: (e[1], e[0]))
    body = [f"Dialogue: {L},{ass_time(a)},{ass_time(b)},{st},,0,0,0,,{tx}" for L, a, b, st, tx in ev]
    Path(path).write_text("\n".join(head_lines + body) + "\n", encoding="utf-8-sig")


# ---------------------------------------------------------------- thumbnail

def thumbnail(cfg, ch, headline, date_label, workdir, out_jpg):
    """1280x720: channel navy, timeline motif, date pill, big Thai headline. No photos."""
    workdir = Path(workdir)
    tw, th = 1280, 720
    accent, red = ass_color(ch["highlight_color"]), ass_color(ch.get("latest_color", "#E63946"))
    white, navy = ass_color(ch["caption_color"]), ass_color(ch["bg_color"])
    head = ch.get("title_font", "Kanit ExtraBold")
    lines = wrap_text(headline, "th", 11)[:3]
    size = 132 if len(lines) <= 2 else 112
    ev = []
    # timeline motif: spine + dots on the right
    ev.append(_draw(1, 0, 5, f"m 1080 90 l 1090 90 l 1090 630 l 1080 630", accent, r"\alpha&H50&"))
    for i, col in enumerate((accent, accent, accent, red)):
        ev.append(_draw(2, 0, 5, circle(1085, 130 + 150 * i, 30 if col == red else 22), col))
    ev += logo(0, 5, 70, 60, 0.9, accent, red, white)
    ev.append(_text(3, 0, 5, "Small", 200, 52, ch["name"]))
    ev += _pill(3, 0, 5, 70, 170, date_label, accent, navy)
    for i, line in enumerate(lines):
        col = accent if i == 0 else white
        ev.append(_text(4, 0, 5, "Head", 70, 280 + int(size * 1.05) * i, line, rf"\1c{col}"))
    styles = [
        f"Style: Head,{head},{size},{white},{white},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,5,4,7,0,0,0,1",
        f"Style: Small,{head},40,{white},{white},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Pill,{head},36,{navy},{navy},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        f"Style: Draw,{head},20,{white},{white},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"]
    lines_ass = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {tw}", f"PlayResY: {th}",
                 "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
                 "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                 "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                 "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                 *styles, "", "[Events]",
                 "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    lines_ass += [f"Dialogue: {L},{ass_time(a)},{ass_time(b)},{s},,0,0,0,,{t}" for L, a, b, s, t in ev]
    (workdir / "thumb.ass").write_text("\n".join(lines_ass) + "\n", encoding="utf-8-sig")
    bg = ch["bg_color"].lstrip("#")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x{bg}:s={tw}x{th}:d=1",
         "-vf", f"vignette=angle=PI/4,subtitles=thumb.ass:fontsdir='{_filter_path(cfg['fonts_dir'])}'",
         "-frames:v", "1", "-q:v", "2", str(Path(out_jpg).resolve())], cwd=workdir)


# ---------------------------------------------------------------- build

def build_long(cfg, slug, force_script=False, log=print):
    """Long recap -> output/review/<channel>/<slug>-long (video.mp4, thumbnail.jpg,
    subtitles.srt, meta.json). Needs approved facts, like the Short."""
    import run as core
    from .footage import get_footage
    from .social import add_social
    nc = cfg["news"]
    key = nc["channel"]
    ch = cfg["channels"][key]
    d = news_dir(cfg, slug)
    data = read_json(d / "research.json")
    problems = safety_problems(data)
    if problems:
        raise SystemExit("Blocked:\n  - " + "\n  - ".join(problems))
    if not data.get("facts_approved"):
        raise SystemExit("The facts are not approved yet (Discord 'Approve facts' or news-approve).")

    sf = d / "script_long.json"
    if force_script or not sf.exists():
        log("Writing the long script + safety review...")
        write_json(sf, write_long_script(cfg, data, log))
    script = read_json(sf)
    closing = nc.get("long_closing", "กดติดตามสรุปให้ฟัง แล้วพบกันในเรื่องต่อไป")
    if long_problems(script, data, 1, 99, closing):
        raise SystemExit("script_long.json no longer passes the checks - rebuild with --force")

    # ---- narration: one cached chunk per part (story-mode recorder)
    extra = nc.get("pronounce") or {}
    parts = long_parts(script)
    units = []
    for pid, label, text in parts:
        units.append({"id": pid, "label": label, "text": speakable(text, extra),
                      "kind": "story", "pause_after": 0.7})
    units[0]["pause_after"] = 1.0
    units[-1]["pause_after"] = max(2.0, END_SCREEN - len(units[-1]["text"]) / CHARS_PER_S)
    model = nc.get("model_id") or cfg["elevenlabs"]["model_id"]
    settings = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.2}
    name = f"{slug}-long"
    out_dir = core.dirs(cfg)["review"] / key / name
    work = core.dirs(cfg)["work"] / f"news-{name}"
    work.mkdir(parents=True, exist_ok=True)
    log(f"Narrating {len(units)} parts ({sum(len(u['text']) for u in units)} characters, cached)...")
    narrate.record(ch, units, d / "cache_long", model, settings, "th", log)
    voice_wav = work / "voice.wav"
    total = narrate.join(units, work, voice_wav)
    end_t = max(units[-1]["offset"], total - END_SCREEN)

    # ---- sections + subtitles
    events = {e["id"]: e for e in data["timeline"]}
    sections = []
    for u, (pid, label, _) in zip(units, parts):
        start = u["offset"]
        sec = {"id": pid, "start": start, "end": start + u["duration"] + u["pause_after"]}
        if pid == "hook":
            sec.update(kind="hook", pill="", text="")
        elif pid == "bg":
            ids = script["background"].get("event_ids", [])
            sec.update(kind="bg", pill="ที่มา", text=script["background"]["card_text"],
                       outlets=sorted({o for x in ids if x in events for o in events[x].get("outlets", [])}))
        elif pid.startswith("b"):
            b = script["beats"][int(pid[1:])]
            evs = [events[x] for x in b["event_ids"] if x in events]
            sec.update(kind="beat", pill=thai_date(evs[0].get("date")) if evs else "",
                       text=b["card_text"], outlets=sorted({o for e in evs for o in e.get("outlets", [])}))
        elif pid.startswith("s"):
            s = script["sides"][int(pid[1:])]
            sec.update(kind="side", pill=s.get("party", "")[:30], text=s.get("card_text", ""))
        elif pid == "next":
            sec.update(kind="next", pill="ต่อจากนี้", text=script["next"]["card_text"])
        else:
            sec.update(kind="closing", pill="", text="")
        sections.append(sec)
    for a, b in zip(sections, sections[1:]):  # cards hand over without gaps
        a["end"] = b["start"]
    sections[-1]["end"] = total
    cues = sorted((c for u in units for c in build_cues(u["text"], u["times"], "th", 46)),
                  key=lambda c: c["start"])

    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    try:
        (out_dir / "subtitles.srt").write_text(to_srt([c for c in cues if c["start"] < end_t]), encoding="utf-8")
        today = thai_date(datetime.now(BKK).date().isoformat())
        write_long_ass(work / "long.ass", ch, script["title"], sections, cues, total, end_t,
                       f"อัปเดต {today}")
        # ---- footage: a few neutral landscape clips, slow crossfades (story renderer)
        log("Footage...")
        st = cfg["story"]
        clip_len = float(st["clip_seconds"]) + 2
        clips, ids = get_footage(script.get("keywords", []), ch.get("footage_hint", ""),
                                 int(nc.get("long_clips", 16)) * float(cfg["video"]["clip_seconds"]),
                                 float(cfg["video"]["clip_seconds"]), work, [], orientation="landscape",
                                 strict=True, exclude=nc.get("footage_exclude", []))
        pool = [(p, duration(p)) for p in clips]
        shots = lv.plan_shots(total, [0.0], [pool], clip_len, float(st["crossfade_seconds"]),
                              random.Random(slug))
        rcfg = {**cfg, "story": {**st, "darken": float(nc.get("long_darken", 0.6))}}
        log(f"Rendering {lv.fmt(total)} of 16:9 video...")
        lv.render(rcfg, ch, shots, work / "long.ass", voice_wav, total, work, out_dir / "video.mp4", log=log)
        log("Thumbnail...")
        last = max((events[x].get("date") or "" for b in script["beats"] for x in b["event_ids"] if x in events),
                   default="")
        thumbnail(cfg, ch, script["thumb_headline"], thai_date(last) if last else today, work,
                  out_dir / "thumbnail.jpg")
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    chapter_list = [(0, "บทนำ")] + [(s["start"], ("ที่มา" if s["kind"] == "bg" else
                                                    f"{s['pill']} {s['text']}" if s["kind"] == "beat" else
                                                    f"จุดยืน: {s['pill']}" if s["kind"] == "side" else
                                                    "ต่อจากนี้"))
                                     for s in sections if s["kind"] in ("bg", "beat", "side", "next")]
    chapters = chapters_text(chapter_list)
    used = sorted({u for b in script["beats"] + [script["background"]] for x in b.get("event_ids", [])
                   if x in events for u in events[x].get("source_urls", [])}
                  | set(data.get("status_source_urls", [])))
    desc = "\n\n".join(x for x in [
        script.get("description", ""), ("ช่วงเวลาในคลิป:\n" + chapters) if chapters else "",
        "แหล่งข่าว:\n" + sources_block(data, set(used)), nc.get("disclaimer", ""),
        ch.get("description_footer", ""),
        " ".join(dict.fromkeys([h for h in ch.get("hashtags", []) if h != "#shorts"] + script.get("hashtags", [])))]
        if x)
    meta = {"kind": "news", "news": slug, "variant": "long", "channel": key, "channel_name": ch["name"],
            "title": script["title"][:100], "script": " ".join(t for _, _, t in parts),
            "description": desc, "tags": [h.lstrip("#") for h in ch.get("hashtags", []) + script.get("hashtags", [])
                                          if h != "#shorts"],
            "length_sec": round(total, 1), "sources": used, "pexels_ids": ids, "chapters": chapter_list,
            "thumbnail": "thumbnail.jpg", "captions": "subtitles.srt",
            "created": datetime.now(BKK).isoformat(timespec="seconds")}
    write_json(out_dir / "meta.json", meta)
    (out_dir / "script.txt").write_text(script["title"] + "\n\n" + "\n\n".join(t for _, _, t in parts) + "\n",
                                        encoding="utf-8")
    add_social(cfg, key, out_dir, log)
    log(f"Long recap ready for review -> {out_dir}")
    return out_dir
