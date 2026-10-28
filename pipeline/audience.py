"""Audience overview per channel (YouTube Analytics API, read-only sign-in from `auth-analytics`):
last N days vs the N days before - views, watch time, subscribers, average % viewed - plus top
countries, age / gender, devices, traffic sources and subscribed vs not.

Used by /audience in Discord and the Monday report. YouTube's numbers lag about 2 days, so the
window ends 2 days ago (like YouTube Studio).
"""
from datetime import date, timedelta

from .report import analytics_service, analytics_token

LAG_DAYS = 2
AGE = {"age13-17": "13-17", "age18-24": "18-24", "age25-34": "25-34", "age35-44": "35-44",
       "age45-54": "45-54", "age55-64": "55-64", "age65-": "65+"}
SOURCES = {"SHORTS": "Shorts feed", "YT_SEARCH": "YouTube search", "BROWSE": "Browse / Home",
           "RELATED_VIDEO": "Suggested", "SUBSCRIBER": "Subscriptions", "YT_CHANNEL": "Channel page",
           "EXT_URL": "External sites", "NO_LINK_OTHER": "Direct / unknown", "PLAYLIST": "Playlists",
           "NOTIFICATION": "Notifications", "END_SCREEN": "End screens", "HASHTAGS": "Hashtags",
           "YT_OTHER_PAGE": "Other YouTube", "SOUND_PAGE": "Sound page"}
DEVICES = {"MOBILE": "Phone", "TABLET": "Tablet", "TV": "TV", "DESKTOP": "Computer", "GAME_CONSOLE": "Console"}
COUNTRY = {"US": "United States", "TH": "Thailand", "PH": "Philippines", "IN": "India", "GB": "United Kingdom",
           "CA": "Canada", "AU": "Australia", "ID": "Indonesia", "MY": "Malaysia", "VN": "Vietnam",
           "SG": "Singapore", "DE": "Germany", "FR": "France", "BR": "Brazil", "PK": "Pakistan",
           "NG": "Nigeria", "JP": "Japan", "KR": "South Korea", "MX": "Mexico", "LA": "Laos", "MM": "Myanmar",
           "KH": "Cambodia", "TW": "Taiwan", "HK": "Hong Kong", "CN": "China", "AE": "UAE", "SA": "Saudi Arabia"}


def flag(code):
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code.upper()) if len(code) == 2 and code.isalpha() else ""


def _q(ya, start, end, metrics, dims=None, sort=None, limit=None):
    kw = {"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(), "metrics": metrics}
    if dims:
        kw["dimensions"] = dims
    if sort:
        kw["sort"] = sort
    if limit:
        kw["maxResults"] = limit
    return ya.reports().query(**kw).execute().get("rows") or []


def _totals(ya, start, end):
    rows = _q(ya, start, end, "views,estimatedMinutesWatched,subscribersGained,subscribersLost,averageViewPercentage")
    v, m, sg, sl, pct = (rows[0] if rows else [0, 0, 0, 0, 0])
    return {"views": int(v), "hours": round(float(m) / 60, 1), "subs": int(sg) - int(sl),
            "avg_pct": round(float(pct), 1)}


def _shares(rows, names=None, top=None):
    """[(label, %)] from rows of (dimension, value), largest first."""
    total = sum(float(r[-1]) for r in rows) or 1
    out = [((names or {}).get(r[0], r[0]), round(100 * float(r[-1]) / total, 1)) for r in rows]
    out.sort(key=lambda x: -x[1])
    return out[:top] if top else out


def channel_audience(cfg, key, days=28, ya=None, today=None):
    """Audience data for one channel, or {"missing": True} without the analytics sign-in."""
    ch = cfg["channels"][key]
    ya = ya or analytics_service(ch)
    if ya is None:
        return {"key": key, "name": ch["name"], "missing": True}
    end = (today or date.today()) - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=days - 1)
    prev_end, prev_start = start - timedelta(days=1), start - timedelta(days=days)
    countries = _q(ya, start, end, "views", "country", sort="-views", limit=5)
    total_views = _totals(ya, start, end)
    age_gender = _q(ya, start, end, "viewerPercentage", "ageGroup,gender")
    ages, genders = {}, {}
    for age, gender, pct in age_gender:
        ages[AGE.get(age, age)] = ages.get(AGE.get(age, age), 0) + float(pct)
        genders[gender] = genders.get(gender, 0) + float(pct)
    return {
        "key": key, "name": ch["name"], "days": days, "start": start.isoformat(), "end": end.isoformat(),
        "now": total_views, "before": _totals(ya, prev_start, prev_end),
        "countries": [(c, round(100 * int(v) / max(total_views["views"], 1), 1)) for c, v in countries],
        "ages": sorted(((a, round(p, 1)) for a, p in ages.items()), key=lambda x: -x[1]),
        "genders": {g: round(p, 1) for g, p in genders.items()},
        "devices": _shares(_q(ya, start, end, "estimatedMinutesWatched", "deviceType"), DEVICES),
        "sources": _shares(_q(ya, start, end, "views", "insightTrafficSourceType"), SOURCES, top=4),
        "subscribed": dict(_shares(_q(ya, start, end, "views", "subscribedStatus"))),
    }


def _arrow(now, before):
    if not before:
        return ""
    ch = 100 * (now - before) / before
    return f" ({'⬆️' if ch >= 0 else '⬇️'}{abs(ch):.0f}%)"


def card(a, compact=False):
    """Discord text for one channel."""
    if a.get("missing"):
        return f"**{a['name']}**: no analytics sign-in - run `py -3 run.py auth-analytics --channel {a['key']}`"
    if a.get("error"):
        return f"**{a['name']}**: couldn't read analytics ({a['error']})"
    n, b = a["now"], a["before"]
    lines = [f"**{a['name']}** - last {a['days']} days (to {a['end']})",
             f"👁️ {n['views']:,} views{_arrow(n['views'], b['views'])} · ⏱️ {n['hours']:,} h"
             f"{_arrow(n['hours'], b['hours'])} · ➕ {n['subs']:+,} subs · 📊 {n['avg_pct']}% viewed"]
    if a["countries"]:
        lines.append("🌍 " + " · ".join(f"{flag(c)} {c} {p}%" for c, p in a["countries"][:5 if not compact else 3]))
    if a["ages"]:
        g = a["genders"]
        lines.append("👥 " + " · ".join(f"{age} {p}%" for age, p in a["ages"][:3])
                     + f" | ♂ {g.get('male', 0)}% ♀ {g.get('female', 0)}%")
    if not compact:
        if a["devices"]:
            lines.append("📱 " + " · ".join(f"{d} {p}%" for d, p in a["devices"][:3]))
        if a["sources"]:
            lines.append("🚪 " + " · ".join(f"{s} {p}%" for s, p in a["sources"]))
        sub = a["subscribed"]
        if sub:
            lines.append(f"🔔 subscribers {sub.get('SUBSCRIBED', 0)}% · non-subscribers {sub.get('UNSUBSCRIBED', 0)}%")
    return "\n".join(lines)


def audience_all(cfg, keys, days=28):
    """[audience dict] for every channel with a YouTube sign-in (errors kept per channel)."""
    from .common import ROOT
    out = []
    for k in keys:
        ch = cfg["channels"][k]
        if not (ROOT / ch.get("youtube_token", "-")).exists():
            continue
        try:
            out.append(channel_audience(cfg, k, days))
        except Exception as e:
            out.append({"key": k, "name": ch["name"], "error": str(e)[:200]})
    return out


def has_analytics(cfg, key):
    ch = cfg["channels"][key]
    return bool(ch.get("youtube_token")) and analytics_token(ch).exists()
