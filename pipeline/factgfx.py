"""Fact graphics for Wonder Minute: animated "when / where" overlays drawn as ASS vector drawings
(the same approach as the news timeline and maps - no extra libraries), burned onto a finished
Short with ffmpeg.

  timeline_bar - "years ago" bar with a marker sliding to the moment and a big counter
  world_map    - land outline (Natural Earth 1:110m, public domain, data/geo/land-110m.json)
                 zoomed to a region, with a region shape that changes colour and a pin
Timings come from the narration's word timings, so graphics appear when the voice says it.
"""
import json
import subprocess
from pathlib import Path

from .common import ROOT

W, H = 1080, 1920
LAND = ROOT / "data" / "geo" / "land-110m.json"


def c(hex_rgb):
    """'#3FA34D' -> ASS '&H4DA33F&' (BGR)."""
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&"


def t(sec):
    sec = max(0.0, sec)
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def ev(layer, a, b, body):
    return f"Dialogue: {layer},{t(a)},{t(b)},G,,0,0,0,,{body}"


def rect(x, y, w, h):
    return f"m {x} {y} l {x + w} {y} l {x + w} {y + h} l {x} {y + h}"


def rrect(x, y, w, h, r):
    k = r * 0.45
    return (f"m {x + r} {y} l {x + w - r} {y} b {x + w - k} {y} {x + w} {y + k} {x + w} {y + r} "
            f"l {x + w} {y + h - r} b {x + w} {y + h - k} {x + w - k} {y + h} {x + w - r} {y + h} "
            f"l {x + r} {y + h} b {x + k} {y + h} {x} {y + h - k} {x} {y + h - r} "
            f"l {x} {y + r} b {x} {y + k} {x + k} {y} {x + r} {y}")


def circle(cx, cy, r):
    k = r * 0.5523
    return (f"m {cx} {cy - r} b {cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} "
            f"b {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} "
            f"b {cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} "
            f"b {cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r}")


def draw(path, color, extra=""):
    return f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c{color}{extra}}}{path}{{\\p0}}"


def label(x, y, s, size, color="#FFFFFF", extra="", an=5):
    return f"{{\\an{an}\\pos({x},{y})\\fs{size}\\1c{c(color)}\\bord4\\3c&H000000&{extra}}}{s}"


def when(words, phrase, default=None, end=False):
    """Start (or end) time of the first occurrence of `phrase` in the narration."""
    toks = [w["word"].lower().strip(".,!?;:'\"") for w in words]
    want = [p.lower() for p in phrase.split()]
    for i in range(len(toks) - len(want) + 1):
        if toks[i:i + len(want)] == want:
            return words[i + len(want) - 1]["end"] if end else words[i]["start"]
    return default


# ---------------------------------------------------------------- world map

def land_rings():
    """[[(lon, lat), ...], ...] from the TopoJSON land outline."""
    topo = json.loads(LAND.read_text(encoding="utf-8"))
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    rings = []
    for geom in topo["objects"]["land"]["geometries"]:
        polys = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
        for poly in polys:
            for ring in poly:
                pts = []
                for i in ring:
                    seg = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
                    pts += seg if not pts else seg[1:]
                rings.append(pts)
    return rings


class MapBox:
    """Equirectangular projection of a lon/lat window onto a screen box."""

    def __init__(self, lon0, lon1, lat0, lat1, x, y, w):
        self.lon0, self.lon1, self.lat0, self.lat1 = lon0, lon1, lat0, lat1
        self.x, self.y, self.w = x, y, w
        self.h = round(w * (lat1 - lat0) / (lon1 - lon0))

    def xy(self, lon, lat):
        return (round(self.x + (lon - self.lon0) / (self.lon1 - self.lon0) * self.w),
                round(self.y + (self.lat1 - lat) / (self.lat1 - self.lat0) * self.h))

    def path(self, ring):
        return "m " + " l ".join("{} {}".format(*self.xy(lon, lat)) for lon, lat in ring)

    def land_paths(self):
        out = []
        for ring in land_rings():
            lons, lats = [p[0] for p in ring], [p[1] for p in ring]
            if max(lons) < self.lon0 or min(lons) > self.lon1 or max(lats) < self.lat0 or min(lats) > self.lat1:
                continue
            out.append(self.path(ring))
        return out


def world_map(box, a, b, region=None, colors=("#3FA34D", "#E2B865"), fade=None, pin=None, title=None):
    """Panel + land + optional colour-changing region (clipped to the land) + pin. Returns events."""
    evs, pad, top = [], 26, (80 if title else 0)
    fd = "\\fad(400,400)"
    evs.append(ev(10, a, b, draw(rrect(box.x - pad, box.y - pad - top, box.w + 2 * pad, box.h + 2 * pad + top, 30),
                                 c("#0B1220"), "\\alpha&H28&" + fd)))
    clip = f"\\clip({box.x},{box.y},{box.x + box.w},{box.y + box.h})"
    land = box.land_paths()
    for p in land:
        evs.append(ev(11, a, b, draw(p, c("#3A4556"), fd + clip)))
    if region:
        anim = ""
        if fade:
            anim = f"\\t({int((fade[0] - a) * 1000)},{int((fade[1] - a) * 1000)},\\1c{c(colors[1])})"
        rp = box.path(region)
        for p in land:  # the region painted only where there is land
            evs.append(ev(12, a, b, f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c{c(colors[0])}{fd}{anim}"
                                    f"\\clip({p})}}{rp}{{\\p0}}"))
    if pin:
        lon, lat, name = pin
        px, py = box.xy(lon, lat)
        evs.append(ev(14, a, b, draw(circle(px, py, 30), c("#3DDCFF"), "\\alpha&H90&" + fd)))
        evs.append(ev(15, a, b, draw(circle(px, py, 12), c("#3DDCFF"), fd)))
        evs.append(ev(15, a, b, label(px, py - 55, name, 50, "#FFFFFF", fd)))
    if title:
        evs.append(ev(15, a, b, label(box.x + box.w // 2, box.y - pad - top // 2, title, 52, "#3DDCFF", fd)))
    return evs


# ---------------------------------------------------------------- timeline

def timeline_bar(a, b, oldest, marks, span=None, y=1500, fmt="{:,} YEARS AGO"):
    """Bar from `oldest` years ago (left) to TODAY (right). marks: [(time, years_ago), ...] - the
    marker and the big counter move between them. span: (older, younger) highlighted in green."""
    x0, x1, fd = 120, 960, "\\fad(400,400)"

    def px(years):
        return round(x1 - (years / oldest) * (x1 - x0))
    evs = [ev(20, a, b, draw(rrect(x0 - 40, y - 110, x1 - x0 + 80, 200, 30), c("#0B1220"), "\\alpha&H28&" + fd)),
           ev(21, a, b, draw(rect(x0, y - 4, x1 - x0, 8), c("#5A6475"), fd)),
           ev(23, a, b, label(x0, y + 48, fmt.format(oldest), 32, "#9AA4B5", fd, an=4)),
           ev(23, a, b, label(x1, y + 48, "TODAY", 32, "#9AA4B5", fd, an=6))]
    if span:
        s0, s1 = px(span[0]), px(span[1])
        evs.append(ev(22, a, b, draw(rect(s0, y - 8, s1 - s0, 16), c("#3FA34D"), fd)))
    nxt = marks[1:] + [(b, marks[-1][1])]
    for (ta, ya), (tb, yb) in zip(marks, nxt):
        steps = max(1, int((tb - ta) / 0.08))
        for i in range(steps):
            s, e = ta + i * (tb - ta) / steps, ta + (i + 1) * (tb - ta) / steps
            yrs = ya + (yb - ya) * ((i + 1) / steps if ya != yb else 1)
            shown = int(round(yrs / 100.0) * 100)
            evs.append(ev(24, s, e, draw(circle(px(yrs), y, 17), c("#3DDCFF"))))
            evs.append(ev(25, s, e, label(540, y - 58, "TODAY" if shown <= 0 else fmt.format(shown), 60)))
    return evs


# ---------------------------------------------------------------- deep ocean

ZONES = [(0, 200, "SUNLIGHT ZONE", "#F5C842"), (200, 1000, "TWILIGHT ZONE", "#4D7BFF"),
         (1000, 4000, "MIDNIGHT ZONE", "#9A5BFF")]
CAPTION_BAND = (1060, 1320)  # the burned-in captions live here: never darkened


def depth_gauge(a, b, stops, glow_from=None, max_depth=1500, darken_to="&H50&"):
    """Descent: the picture darkens (outside the caption band), a depth scale with the ocean zones,
    a marker + big counter moving through stops [(time, depth_m), ...], glowing dots after
    glow_from. Returns events."""
    import random
    x, top, bot, fd = 950, 300, 1650, "\\fad(400,400)"
    evs = []

    def py(d):
        return round(top + min(d, max_depth) / max_depth * (bot - top))
    dark_ms = int((stops[-1][0] - a) * 1000)
    for y0, y1 in ((-60, CAPTION_BAND[0]), (CAPTION_BAND[1], H + 60)):  # soft edges (blur) at the band
        evs.append(ev(30, a, b, draw(rect(-60, y0, W + 120, y1 - y0), "&H000000&",
                                     f"\\blur40\\alpha&HF0&\\t(0,{dark_ms},\\alpha{darken_to}){fd}")))
    evs.append(ev(31, a, b, draw(rect(x, top, 8, bot - top), c("#5A6475"), fd)))
    for d0, d1, name, col in ZONES:
        y0, y1 = py(d0), py(d1)
        evs.append(ev(32, a, b, draw(rect(x - 6, y0, 20, y1 - y0), c(col), "\\alpha&H30&" + fd)))
        evs.append(ev(33, a, b, label(x - 28, (y0 + y1) // 2, f"{name}\\N{d0:,}-{d1:,} m", 44, col, fd, an=6)))
    nxt = stops[1:] + [(b, stops[-1][1])]
    for (ta, da), (tb, db) in zip(stops, nxt):
        steps = max(1, int((tb - ta) / 0.08))
        for i in range(steps):
            s, e = ta + i * (tb - ta) / steps, ta + (i + 1) * (tb - ta) / steps
            d = da + (db - da) * (i + 1) / steps
            evs.append(ev(34, s, e, draw(circle(x + 4, py(d), 17), c("#3DDCFF"))))
            evs.append(ev(35, s, e, label(470, 360, f"-{int(round(d / 10.0) * 10):,} m", 100)))
    if glow_from:
        rnd = random.Random(7)
        for _ in range(30):
            gx = rnd.randint(70, 760)
            gy = rnd.choice([rnd.randint(470, 1020), rnd.randint(1360, 1820)])  # not over the captions
            s = glow_from + rnd.uniform(0, max(0.1, b - glow_from - 1.5))
            e = min(b, s + rnd.uniform(1.2, 2.8))
            col = rnd.choice(["#3DDCFF", "#4DFFB8", "#7AB8FF"])
            r = rnd.randint(5, 11)
            evs.append(ev(36, s, e, draw(circle(gx, gy, r), c(col), "\\blur5\\fad(400,600)")))
            evs.append(ev(36, s, e, draw(circle(gx, gy, r * 3), c(col), "\\alpha&HB4&\\blur14\\fad(400,600)")))
    return evs


def ratio_dots(a, b, lit, total, caption, y=640, color="#3DDCFF"):
    """'3 OUT OF 4' - a row of dots, `lit` of them glowing. Returns events."""
    fd, gap = "\\fad(300,400)", 150
    x0 = 540 - gap * (total - 1) // 2
    evs = [ev(40, a, b, draw(rrect(x0 - 110, y - 140, gap * (total - 1) + 220, 270, 30), c("#0B1220"),
                             "\\alpha&H28&" + fd))]
    for i in range(total):
        cx = x0 + i * gap
        on = i < lit
        col = c(color) if on else c("#3A4556")
        start = a + 0.25 * i
        if on:
            evs.append(ev(41, start, b, draw(circle(cx, y + 20, 52), col, "\\alpha&HA0&\\blur16" + fd)))
        evs.append(ev(42, start, b, draw(circle(cx, y + 20, 34), col, ("\\blur3" if on else "") + fd)))
    evs.append(ev(43, a, b, label(540, y - 85, caption, 54, color, fd)))
    return evs


# ---------------------------------------------------------------- comparisons

def bar_compare(a, b, items, title, y=520, unit="%"):
    """Horizontal bars that grow in: items [(name, value, '#color'), ...] scaled to the largest."""
    fd, x0, maxw, gap = "\\fad(300,400)", 150, 760, 150
    top = y - 120
    evs = [ev(50, a, b, draw(rrect(x0 - 50, top, maxw + 140, 120 + gap * len(items) + 30, 30), c("#0B1220"),
                             "\\alpha&H28&" + fd)),
           ev(53, a, b, label(540, top + 60, title, 50, "#3DDCFF", fd))]
    vmax = max(v for _, v, _ in items)
    for i, (name, val, col) in enumerate(items):
        by = top + 120 + i * gap
        w = max(14, round(val / vmax * maxw))
        grow = int(600 + 300 * i)
        evs.append(ev(51, a, b, label(x0, by + 10, name, 38, "#FFFFFF", fd, an=4)))
        evs.append(ev(52, a, b, f"{{\\an7\\pos({x0},{by + 40})\\p1\\bord0\\shad0\\1c{c(col)}\\fscx0"
                                f"\\t(200,{grow},\\fscx100){fd}}}{rect(0, 0, w, 50)}{{\\p0}}"))
        evs.append(ev(53, a + grow / 1000, b, label(x0 + w + 16, by + 65, f"{val:g}{unit}", 40, col, fd, an=4)))
    return evs


def level_drop(a, b, title, note, drop_px=120, x=290, y=430, w=500, h=360):
    """A tank whose water line drops by drop_px over the event - 'shrinking every year'."""
    fd = "\\fad(300,400)"
    dur = int((b - a) * 1000)
    evs = [ev(60, a, b, draw(rrect(x - 40, y - 110, w + 80, h + 190, 30), c("#0B1220"), "\\alpha&H28&" + fd)),
           ev(61, a, b, draw(rect(x, y, w, h), c("#2A3342"), fd)),  # the basin
           # the water: starts full, its top edge sinks by drop_px (moved down, clipped to the basin)
           ev(62, a, b, f"{{\\an7\\move(0,0,0,{drop_px},300,{max(400, dur - 400)})\\p1\\bord0\\shad0"
                        f"\\1c{c('#3DA5FF')}\\clip({x},{y},{x + w},{y + h}){fd}}}{rect(x, y + 20, w, h)}{{\\p0}}"),
           ev(63, a, b, draw(rect(x - 20, y + 18, w + 40, 4), c("#FFFFFF"), "\\alpha&H60&" + fd)),  # old level
           ev(64, a, b, label(x + w // 2, y - 55, title, 50, "#3DDCFF", fd)),
           ev(64, a, b, label(x + w // 2, y + h + 45, note, 40, "#FFFFFF", fd))]
    return evs


def stat(a, b, big, caption, y=560, color="#3DDCFF"):
    """A big number / word that pops in with a caption under it ('243 DAYS' / 'one spin of Venus')."""
    fd = "\\fad(250,400)"
    return [ev(70, a, b, draw(rrect(150, y - 170, 780, 330, 34), c("#0B1220"), "\\alpha&H28&" + fd)),
            ev(71, a, b, f"{{\\an5\\pos(540,{y - 30})\\fs130\\1c{c(color)}\\bord5\\3c&H000000&\\fscx60\\fscy60"
                         f"\\t(0,250,\\fscx110\\fscy110)\\t(250,400,\\fscx100\\fscy100){fd}}}{big}"),
            ev(72, a, b, label(540, y + 95, caption, 44, "#FFFFFF", fd))]


# ---------------------------------------------------------------- graphics spec (in the script JSON)

SPEC_HELP = """\
"graphics": a list of 1-3 overlays. Every phrase ("from", "until", marks...) must appear WORD FOR WORD
in the script; "until" may be "end". Types:
  {"type": "stat", "from": "phrase", "until": "phrase|end", "big": "243 DAYS", "caption": "one spin of Venus"}
  {"type": "timeline", "from": "...", "until": "end", "oldest": 12000,
   "marks": [["phrase", 11000], ["phrase", 5000, "end"], ["phrase", 0, "end"]], "span": [11000, 5000]}
     (years ago; marks = when the marker reaches that value; "end" = at the end of the phrase)
  {"type": "map", "from": "...", "until": "end", "view": [lon0, lon1, lat0, lat1], "pin": [lon, lat, "LABEL"],
   "title": "NORTH AFRICA", "region": [[lon, lat], ...], "fade": ["phrase", "phrase"]}
     (region optional: painted green, turns sand between the two fade phrases)
  {"type": "depth", "from": "...", "until": "end", "stops": [["phrase", 0], ["phrase", 1000, "end"]],
   "glow_from": "phrase"}        (deep ocean: darkening + depth gauge + zones; glow optional)
  {"type": "ratio", "from": "...", "until": "end", "lit": 3, "total": 4, "caption": "3 OUT OF 4 GLOW"}
  {"type": "bars", "from": "...", "until": "phrase", "title": "HOW SALTY?", "unit": "% salt",
   "items": [["OCEAN", 3.5, "#3DA5FF"], ["DEAD SEA", 34, "#F5C842"]]}
  {"type": "level", "from": "...", "until": "end", "title": "SHRINKING", "note": "about -1 m every year"}
Keep them over different stretches of the narration (two overlays should not cover the same area at
once); the captions in the middle of the screen are never covered."""


def spec_events(spec, words, end):
    """Events for a list of graphics specs. A graphic whose phrase isn't in the narration is skipped
    (returned in `skipped`), never fatal."""
    events, skipped = [], []

    def at(ph, use_end=False):
        if ph in (None, "end"):
            return end
        return when(words, ph, end=use_end)

    for g in spec or []:
        try:
            a = at(g["from"])
            b = at(g.get("until", "end"))
            if a is None or b is None or b <= a:
                raise ValueError(f"phrase not found: {g.get('from')!r} / {g.get('until')!r}")
            kind = g["type"]
            if kind == "stat":
                events += stat(a, b, g["big"], g.get("caption", ""))
            elif kind == "timeline":
                marks = [(at(m[0], len(m) > 2 and m[2] == "end"), m[1]) for m in g["marks"]]
                if any(t_ is None for t_, _ in marks):
                    raise ValueError("a timeline mark phrase is not in the narration")
                events += timeline_bar(a, b, g["oldest"], marks, span=g.get("span"))
            elif kind == "map":
                box = MapBox(*g["view"], 90, 360, 900) if (g["view"][1] - g["view"][0]) >= \
                    (g["view"][3] - g["view"][2]) * 1.4 else MapBox(*g["view"], 140, 360, 800)
                fade = None
                if g.get("fade"):
                    fade = (at(g["fade"][0]), at(g["fade"][1], True))
                    fade = fade if None not in fade else None
                pin = tuple(g["pin"]) if g.get("pin") else None
                events += world_map(box, a, b, region=g.get("region"), fade=fade, pin=pin, title=g.get("title"))
            elif kind == "depth":
                stops = [(at(s[0], len(s) > 2 and s[2] == "end"), s[1]) for s in g["stops"]]
                if any(t_ is None for t_, _ in stops):
                    raise ValueError("a depth stop phrase is not in the narration")
                events += depth_gauge(a, b, stops, glow_from=at(g["glow_from"]) if g.get("glow_from") else None,
                                      max_depth=g.get("max_depth", 1500))
            elif kind == "ratio":
                events += ratio_dots(a, b, g["lit"], g["total"], g.get("caption", ""))
            elif kind == "bars":
                events += bar_compare(a, b, [tuple(i) for i in g["items"]], g.get("title", ""), unit=g.get("unit", ""))
            elif kind == "level":
                events += level_drop(a, b, g.get("title", ""), g.get("note", ""))
            else:
                raise ValueError(f"unknown type {kind!r}")
        except Exception as e:  # a bad spec costs one overlay, not the video
            skipped.append(f"{g.get('type')}: {e}")
    return events, skipped


def apply_graphics(cfg, spec, words, video, workdir):
    """Burn the spec's overlays onto `video` in place. Returns the list of skipped graphics."""
    from .assemble import duration
    end = duration(video)
    events, skipped = spec_events(spec, words, end)
    if not events:
        return skipped
    ass = Path(workdir) / "graphics.ass"
    write_ass(events, ass)
    tmp = Path(workdir) / "video_gfx.mp4"
    burn(video, ass, tmp, ROOT / cfg["fonts_dir"])
    tmp.replace(video)
    return skipped


# ---------------------------------------------------------------- render

def write_ass(events, path, font="Anton"):
    head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: G,{font},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    Path(path).write_text("\n".join(head + events) + "\n", encoding="utf-8-sig")


def burn(video, ass, out, fonts_dir):
    """Burn the overlay onto the video (audio copied)."""
    from .assemble import _filter_path
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
                    f"subtitles='{_filter_path(str(Path(ass).resolve()))}':fontsdir='{_filter_path(str(fonts_dir))}'",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "copy", str(out)], check=True)
    return out
