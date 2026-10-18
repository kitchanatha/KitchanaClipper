"""Simple location maps for news recaps (our own drawing - no map tiles, no copyright issues).

A story opts in with  "map": "<country>"  in research.json. While the timeline runs, the country
outline sits under the cards; a city lights up (red) while a beat that names it is on screen
and stays marked (yellow) afterwards. City names are matched in the beat's Thai text (aliases).
"""
import math

# outline: (lon, lat) points, rough but recognisable; cities: Thai name + spelling variants
COUNTRIES = {
    "france": {
        "outline": [
            (2.54, 51.09), (4.15, 49.98), (5.8, 49.5), (8.2, 48.98), (7.6, 47.58), (6.9, 47.25),
            (6.1, 46.15), (7.0, 45.9), (6.6, 45.1), (7.0, 44.2), (7.53, 43.78), (6.6, 43.15),
            (5.4, 43.2), (4.6, 43.4), (3.1, 43.0), (3.17, 42.43), (1.7, 42.5), (-0.3, 42.8),
            (-1.78, 43.37), (-1.25, 44.6), (-1.2, 46.2), (-2.2, 47.2), (-4.78, 48.4), (-3.5, 48.8),
            (-1.6, 48.6), (-1.9, 49.7), (-1.25, 49.6), (0.1, 49.45), (1.4, 50.1), (1.6, 50.9),
        ],
        "cities": {
            "ปารีส": ((2.35, 48.86), ["ปารีส"]),
            "มาร์แซย์": ((5.37, 43.30), ["มาร์แซย์", "มาร์เซย์", "มาร์เซยย์", "มาร์กเซย"]),
            "ลียง": ((4.84, 45.76), ["ลียง", "ลิยง", "ลีออง"]),
            "ลีล": ((3.06, 50.63), ["ลีล", "ลิลล์", "ลีลล์"]),
            "ลีมอฌ": ((1.26, 45.83), ["ลีมอฌ", "ลิมอฌ", "ลิโมจส์", "ลีโมจ", "ลิโมจ"]),
            "บอร์โดซ์": ((-0.58, 44.84), ["บอร์โดซ์", "บอร์กโดซ์", "บอร์โด"]),
        },
    },
}


def cities_in(country, text):
    """Thai city names (canonical) mentioned in text."""
    c = COUNTRIES.get(country) or {}
    return [name for name, (_, aliases) in c.get("cities", {}).items()
            if any(a in (text or "") for a in aliases)]


def _project(country, box):
    """(lon, lat) -> (x, y) inside box = (x, y, w, h), keeping the shape's proportions."""
    pts = COUNTRIES[country]["outline"]
    lat0 = sum(p[1] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    xs, ys = [p[0] * k for p in pts], [-p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    bx, by, bw, bh = box
    s = min(bw / (maxx - minx), bh / (maxy - miny))
    ox = bx + (bw - (maxx - minx) * s) / 2
    oy = by + (bh - (maxy - miny) * s) / 2
    return lambda lon, lat: (round(ox + (lon * k - minx) * s), round(oy + (-lat - miny) * s))


def map_events(country, t0, t1, beats, colors, box, font_style="MapCity"):
    """ASS events: outline from t0 to t1; per beat (start, end, cities) the named cities pulse
    red with their name, earlier ones stay yellow. colors: dict fill, line, hot, done, dot."""
    from .newsvideo import _draw, _text, circle  # same drawing helpers as the timeline
    proj = _project(country, box)
    c = COUNTRIES[country]
    path = "m " + " l ".join(f"{x} {y}" for x, y in (proj(*p) for p in c["outline"]))
    ev = [
        _draw(1, t0, t1, path, colors["fill"], r"\alpha&H50&\fad(400,300)"),
        (2, t0, t1, "Draw", r"{\an7\pos(0,0)\bord3\shad0\1a&HFF&\3c" + colors["line"]
         + r"&\fad(400,300)\p1}" + path),
    ]
    for name, ((lon, lat), _) in c["cities"].items():  # every city: a small dot
        x, y = proj(lon, lat)
        ev.append(_draw(3, t0, t1, circle(x, y, 7), colors["dot"], r"\alpha&H40&\fad(400,300)"))
    seen = set()
    for (b0, b1, names) in beats:
        for name in names:
            (lon, lat), _ = c["cities"][name]
            x, y = proj(lon, lat)
            ev.append(_draw(5, b0, b1, circle(x, y, 26), colors["hot"], r"\alpha&H90&\blur8\fad(200,0)"))
            ev.append(_draw(6, b0, b1, circle(x, y, 13), colors["hot"], r"\fad(200,0)"))
            ev.append(_text(7, b0, b1, font_style, x + 22, y - 26, name, r"\fad(200,0)"))
            if name not in seen:  # marked from here on
                seen.add(name)
                ev.append(_draw(4, b1, t1, circle(x, y, 11), colors["done"], r"\fad(200,0)"))
    return ev
