"""News recaps (สรุปให้ฟัง) - the 9:16 timeline overlay (.ass) drawn over darkened stock footage.

Layout (1080x1920):
  top        logo (timeline line + dots) and channel name
  0..beat1   the hook card (key number + question) pops in BIG from the first frame while the
             short hook is spoken - people decide in ~2 s whether to keep watching
  beats      a card slides in when its beat starts being spoken: date pill (yellow), up to 2
             lines of summary (Kanit), "แหล่งข่าว: ..." line. Earlier beats stay as compact rows
             on the timeline line above it.
  ล่าสุด     end card with a red dot / pill, the current status and the channel logo
  bottom     word-by-word captions in the lower third (shared Shorts caption code)
"""
from pathlib import Path

from .assemble import ASS_STYLE_FORMAT, _ts, caption_events, caption_style
from .newslib import CARD_LINE, card_fits, card_width, wrap_card
from .storylib import ass_color, ass_escape, wrap_text

W, H = 1080, 1920
SPINE_X = 120
TOP = 430           # where the timeline starts
ROW = 112           # height of a compact (earlier) row
CARD_W, CARD_H = 860, 370
MAX_ROWS = 3        # earlier beats kept visible above the current card


def rrect(x, y, w, h, r):
    """Rounded-rectangle ASS drawing path."""
    k = r * 0.45
    return (f"m {x + r} {y} l {x + w - r} {y} b {x + w - k} {y} {x + w} {y + k} {x + w} {y + r} "
            f"l {x + w} {y + h - r} b {x + w} {y + h - k} {x + w - k} {y + h} {x + w - r} {y + h} "
            f"l {x + r} {y + h} b {x + k} {y + h} {x} {y + h - k} {x} {y + h - r} "
            f"l {x} {y + r} b {x} {y + k} {x + k} {y} {x + r} {y}")


def circle(cx, cy, r):
    k = r * 0.552
    return (f"m {cx} {cy - r} b {cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} "
            f"b {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} "
            f"b {cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} "
            f"b {cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r}")


def _draw(layer, t0, t1, path, color, extra=""):
    return (layer, t0, t1, "Draw", rf"{{\an7\pos(0,0)\bord0\shad0\1c{color}{extra}\p1}}" + path)


def _text(layer, t0, t1, style, x, y, text, extra="", an=7):
    pos = "" if r"\move(" in extra else rf"\pos({x},{y})"  # a sliding element uses \move only
    return (layer, t0, t1, style, rf"{{\an{an}{pos}{extra}}}" + ass_escape(text))


def logo(t0, t1, x, y, scale, accent, latest, text_col):
    """Our mark: a short timeline line with two yellow dots and a red one."""
    s = scale
    ev = [_draw(5, t0, t1, f"m {x} {y + 18 * s} l {x + 120 * s} {y + 18 * s} "
                           f"l {x + 120 * s} {y + 24 * s} l {x} {y + 24 * s}", text_col, r"\alpha&H40&")]
    for i, col in enumerate((accent, accent, latest)):
        ev.append(_draw(6, t0, t1, circle(x + (10 + 50 * i) * s, y + 21 * s, 13 * s), col))
    return ev


def card_events(t0, t1, y, beat, ch, slide=True, latest=False):
    """One timeline card (date pill + summary + sources) appearing at t0."""
    accent = ass_color(ch["highlight_color"])
    red = ass_color(ch.get("latest_color", "#E63946"))
    bg = ass_color(ch["bg_color"], 0x30)
    white = ass_color(ch["caption_color"])
    x = SPINE_X + 50
    dx = 140 if slide else 0
    mv = (lambda ex, ey: rf"\move({ex + dx},{ey},{ex},{ey},0,350)\fad(250,0)") if slide else (
        lambda ex, ey: r"\fad(200,0)")
    pill_col = red if latest else accent
    pill_txt_col = white if latest else ass_color(ch["bg_color"])
    lines = wrap_card(beat["card_text"], CARD_LINE, 2 if not latest else 4)
    h = CARD_H if not latest else 240 + 84 * len(lines)
    ev = [
        # card panel (drawn at 0,0 and moved, so the whole card slides as one)
        (2, t0, t1, "Draw", rf"{{\an7{mv(0, 0)}\bord3\shad0\1c{bg}\3c{pill_col}\p1}}"
                            + rrect(x, y, CARD_W, h, 28)),
        (3, t0, t1, "Draw", rf"{{\an7{mv(0, 0)}\bord0\shad0\1c{pill_col}\p1}}"
                            + rrect(x + 34, y + 30, 300, 74, 37)),
        _text(4, t0, t1, "Pill", x + 34 + 150, y + 67, beat["date_label"],
              rf"\1c{pill_txt_col}" + mv(x + 34 + 150, y + 67), an=5),
    ]
    for i, line in enumerate(lines):
        ev.append(_text(4, t0, t1, "Card", x + 38, y + 126 + 84 * i, line, mv(x + 38, y + 126 + 84 * i)))
    if beat.get("outlets"):
        src = "แหล่งข่าว: " + ", ".join(beat["outlets"][:4])
        ev.append(_text(4, t0, t1, "Src", x + 40, y + h - 58, src, mv(x + 40, y + h - 58)))
    # the dot on the timeline line, level with the date pill
    dot = red if latest else accent
    ev.append(_draw(4, t0, t1, circle(SPINE_X, y + 67, 34), dot, r"\alpha&HA0&\blur6\fad(250,0)"))
    ev.append(_draw(5, t0, t1, circle(SPINE_X, y + 67, 22), dot, r"\fad(250,0)"))
    return ev


def compact_row(t0, t1, y, beat, ch):
    """An earlier beat, shrunk to a row on the timeline: dot, date, first words."""
    accent = ass_color(ch["highlight_color"])
    white = ass_color(ch["caption_color"])
    short = wrap_text(beat["card_text"], "th", 17)[0]
    if short != beat["card_text"]:
        short += "…"
    return [
        _draw(5, t0, t1, circle(SPINE_X, y + 26, 14), accent),
        _text(4, t0, t1, "Row", SPINE_X + 44, y + 4, beat["date_label"], rf"\1c{accent}"),
        _text(4, t0, t1, "Row", SPINE_X + 230, y + 4, short, rf"\1c{white}\alpha&H50&"),
    ]


def write_news_ass(path, ch, cfg, title, beats, latest, words, total, hook_card=None, map_country=None):
    """beats: [{start, date_label, card_text, outlets, places}], latest: {start, card_text}.
    hook_card: 1-2 headline lines for the opening (older scripts without it show the title).
    map_country: draw that country under the timeline cards, lighting up each beat's places."""
    accent = ass_color(ch["highlight_color"])
    red = ass_color(ch.get("latest_color", "#E63946"))
    white = ass_color(ch["caption_color"])
    navy = ass_color(ch["bg_color"])
    font, head = ch.get("font", "Kanit"), ch.get("title_font", "Kanit ExtraBold")
    styles = [
        caption_style(ch, size=78),
        f"Style: Draw,{font},20,{white},{white},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Card,{font},66,{white},{white},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Pill,{head},46,{navy},{navy},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        f"Style: Src,{font},34,{white},{white},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Row,{font},40,{white},{white},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1",
        f"Style: Head,{head},54,{white},{white},&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,2,7,0,0,0,1",
        f"Style: Title,{head},88,{white},{white},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,3,8,0,0,0,1",
        f"Style: Hook,{head},150,{white},{white},{navy},&H80000000,0,0,0,0,100,100,0,0,1,8,4,8,0,0,0,1",
        f"Style: MapCity,{head},44,{white},{white},{navy},&H80000000,0,0,0,0,100,100,0,0,1,4,0,7,0,0,0,1",
    ]
    ev = []
    end = total
    # header: logo + channel name, the whole video
    ev += logo(0, end, 70, 110, 1.0, accent, red, white)
    ev.append(_text(6, 0, end, "Head", 230, 96, ch["name"]))
    first = beats[0]["start"] if beats else latest["start"]
    if hook_card:  # big headline from frame 1: line 1 (the number) yellow, line 2 white
        pop = r"\fscx70\fscy70\t(0,180,\fscx106\fscy106)\t(180,300,\fscx100\fscy100)\fad(0,200)"
        for i, line in enumerate(hook_card[:2]):
            col = accent if i == 0 else white
            ev.append(_text(4, 0, first, "Hook", W // 2, 560 + 190 * i, str(line).strip(),
                            rf"\1c{col}" + pop, an=8))
        ev.append(_draw(4, 0, first, rrect(W // 2 - 140, 560 + 190 * len(hook_card[:2]) + 30, 280, 14, 7),
                        accent, r"\fad(0,200)"))
    else:  # title while the hook is spoken
        tl = wrap_text(title, "th", 15)[:4]
        for i, line in enumerate(tl):
            ev.append(_text(4, 0, first, "Title", W // 2, 470 + 112 * i, line, r"\fad(300,200)", an=8))
        ev.append(_draw(4, 0, first, rrect(W // 2 - 110, 470 + 112 * len(tl) + 26, 220, 12, 6), accent,
                        r"\fad(300,200)"))
    # beats: the timeline grows
    for k, b in enumerate(beats):
        t0, t1 = b["start"], (beats[k + 1]["start"] if k + 1 < len(beats) else latest["start"])
        prev = beats[max(0, k - MAX_ROWS):k]
        card_y = TOP + ROW * len(prev)
        ev.append(_draw(1, t0, t1, f"m {SPINE_X - 3} {TOP - 20} l {SPINE_X + 3} {TOP - 20} "
                                   f"l {SPINE_X + 3} {card_y + 67} l {SPINE_X - 3} {card_y + 67}",
                        accent, r"\alpha&H60&"))
        for j, p in enumerate(prev):
            ev += compact_row(t0, t1, TOP + ROW * j, p, ch)
        ev += card_events(t0, t1, card_y, b, ch)
    if map_country and beats:  # where it happened: below the cards while the timeline runs
        from .maps import map_events
        spans = [(b["start"], beats[k + 1]["start"] if k + 1 < len(beats) else latest["start"],
                  b.get("places") or []) for k, b in enumerate(beats)]
        ev += map_events(map_country, beats[0]["start"], latest["start"], spans,
                         {"fill": navy, "line": accent, "hot": red, "done": accent, "dot": white},
                         (W // 2 - 300, 1155, 600, 410))
    # ล่าสุด end card
    lt = {"date_label": "ล่าสุด", "card_text": latest["card_text"], "outlets": latest.get("outlets")}
    ev += card_events(latest["start"], end, 520, lt, ch, latest=True)
    ev += logo(latest["start"], end, W // 2 - 80, 1300, 1.4, accent, red, white)
    ev.append(_text(6, latest["start"], end, "Head", W // 2, 1370, ch["name"], r"\fad(300,0)", an=8))
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
             "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", ASS_STYLE_FORMAT]
    lines += styles + ["", "[Events]",
                       "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    lines += [f"Dialogue: {L},{_ts(a)},{_ts(b)},{st},,0,0,0,,{tx}" for L, a, b, st, tx in ev]
    lines += caption_events(words, ch, cfg, total, y_frac=0.83, layer=8)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def check_card_texts(beats, latest):
    """Card texts that are too long for the layout (empty = fine)."""
    bad = [b["card_text"] for b in beats if not card_fits(b["card_text"])]
    if card_width(latest.get("card_text", "")) > 90:
        bad.append(latest["card_text"])
    return bad
