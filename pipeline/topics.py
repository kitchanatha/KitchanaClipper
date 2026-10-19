"""Daily topic finder for สรุปให้ฟัง: what Thai people are talking about in the last 24-48 h.

Claude + web search (the credible outlets only) returns 6 candidates; this code then applies the
HARD filter (no override): monarchy / royal family (words in any field), private individuals,
minors, crime victims, and fewer than 2 distinct credible outlets among URLs the search really
returned. Topics covered in the last 14 days are dropped unless Claude marks a major new
development (label "อัปเดต"). The best 3 (ad-friendliness first) are saved to
data/topics/<date>.json and posted to Discord; nothing is researched, rendered or uploaded until
you tap a button and approve the facts and the video as usual.
"""
import json
import re
from datetime import datetime, timedelta

from .common import ROOT
from .newslib import monarchy_hits, norm_url, outlet_for

TOPICS_DIR = ROOT / "data" / "topics"
COVERED = ROOT / "data" / "news_topics.json"
AD_RANK = {"high": 0, "medium": 1, "low": 2}

PROMPT = """Today is {today} (Bangkok). You find topics for the Thai YouTube channel "สรุปให้ฟัง"
(neutral news timeline recaps). Search the Thai news sites you can reach for what Thai people are
talking about in the LAST 24-48 HOURS (most-read, most-shared, trending).

Return {n} candidates. HARD RULES - leave a topic out completely if it involves ANY of:
the Thai monarchy or royal family (directly or indirectly: royal projects, ceremonies,
appointments, Section 112); a private individual (not a public figure / organisation) at its
centre; minors; crime victims; graphic violence. Each topic needs at least 2 DIFFERENT outlets
you actually found, with URLs.

Prefer topics advertisers are comfortable with (ad_friendliness "high"): prices and cost of
living, government policies, brands and products, public figures' PUBLIC disputes, travel,
tech, consumer issues, weather, sports. Mark politics / disasters / crime "medium" or "low".

Topics already covered in the last 14 days (skip them, UNLESS there is a major new development -
then set "update_of" to that topic and say what is new in "update_what"):
{covered}

Reply with ONLY this JSON inside <topics></topics> tags:
{{"topics": [{{"title_th": "short Thai title", "topic_en": "3-8 word English summary",
"summary_th": "one Thai sentence", "why_trending": "one English sentence",
"sources": [{{"outlet": "...", "url": "...", "date": "YYYY-MM-DD"}}],
"safety": {{"monarchy": false, "private_person": false, "minors": false, "crime_victims": false,
"ongoing_criminal_case": false, "politics": false}},
"ad_friendliness": {{"level": "high|medium|low", "reason": "..."}},
"update_of": null, "update_what": null}}]}}"""


def covered_topics(cfg, days=14):
    """[{title, topic_en, date}] covered recently: picked in the finder + every news folder."""
    since = datetime.now() - timedelta(days=days)
    out = []
    try:
        out += [t for t in json.loads(COVERED.read_text(encoding="utf-8"))
                if datetime.fromisoformat(t["date"]) >= since]
    except (OSError, ValueError):
        pass
    for f in (ROOT / cfg["news"].get("dir", "news")).glob("*/research.json"):
        try:
            when = datetime.strptime(f.parent.name[:8], "%Y%m%d")
            if when < since:
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
            out.append({"title": r.get("topic", ""), "topic_en": r.get("topic_en", f.parent.name[9:]),
                        "date": when.date().isoformat()})
        except (OSError, ValueError):
            continue
    return out


def mark_covered(topic):
    data = []
    try:
        data = json.loads(COVERED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data.append({"title": topic.get("title_th", ""), "topic_en": topic.get("topic_en", ""),
                 "date": datetime.now().date().isoformat()})
    COVERED.parent.mkdir(parents=True, exist_ok=True)
    COVERED.write_text(json.dumps(data[-300:], ensure_ascii=False, indent=2), encoding="utf-8")


def _words(t):
    return {w for w in re.findall(r"[a-z0-9]+", str(t).lower()) if len(w) > 2}


def is_repeat(topic, covered, overlap=0.6):
    """Same story as a recent one (English summaries share most words)."""
    a = _words(topic.get("topic_en", ""))
    for c in covered:
        b = _words(c.get("topic_en", ""))
        if a and b and len(a & b) / min(len(a), len(b)) >= overlap:
            return c
    return None


def filter_topics(cands, credible, verified, covered, keep=3):
    """The hard filter + ranking. Returns (kept, dropped [(title, reason)])."""
    verified_n = {norm_url(u) for u in verified}
    kept, dropped = [], []
    for t in cands:
        title = t.get("title_th") or t.get("topic_en") or "?"
        s = t.get("safety") or {}
        text = " ".join(str(t.get(k, "")) for k in ("title_th", "topic_en", "summary_th", "why_trending",
                                                    "update_what"))
        text += " " + " ".join(str(x.get("outlet", "")) + " " + str(x.get("url", ""))
                               for x in t.get("sources") or [])
        hits = monarchy_hits(text)
        if s.get("monarchy") or hits:
            dropped.append((title, "monarchy / royal family" + (f" ({', '.join(hits)})" if hits else "")))
            continue
        bad = [k for k in ("private_person", "minors", "crime_victims") if s.get(k)]
        if bad:
            dropped.append((title, "involves " + ", ".join(bad)))
            continue
        good = []
        for src in t.get("sources") or []:
            name = outlet_for(src.get("url"), credible)
            if name and norm_url(src.get("url")) in verified_n:
                good.append({**src, "outlet": name})
        outlets = {g["outlet"] for g in good}
        if len(outlets) < 2:
            dropped.append((title, f"only {len(outlets)} credible outlet(s) found by the search"))
            continue
        rep = is_repeat(t, covered)
        if rep and not (t.get("update_of") and t.get("update_what")):
            dropped.append((title, f"covered recently ({rep.get('date')})"))
            continue
        newest = max((str(g.get("date", "")) for g in good), default="")
        t = {**t, "sources": good, "outlets": sorted(outlets), "newest": newest,
             "fresh": newest >= (datetime.now() - timedelta(days=2)).date().isoformat(),
             "label": "อัปเดต" if (t.get("update_of") and t.get("update_what")) else ""}
        kept.append(t)
    # fresh first (a source from the last 2 days), then ad-friendliness, then more outlets
    kept.sort(key=lambda t: (not t["fresh"],
                             AD_RANK.get(str((t.get("ad_friendliness") or {}).get("level")).lower(), 3),
                             -len(t["outlets"])))
    return kept[:keep], dropped + [(t.get("title_th", "?"), "not in the top 3") for t in kept[keep:]]


def find_topics(cfg, log=print, n=6, keep=3):
    """Search, filter, save data/topics/<date>.json. Returns the saved dict."""
    from .news import claude_web_research
    nc = cfg["news"]
    credible = nc["credible_outlets"]
    covered = covered_topics(cfg)
    prompt = PROMPT.format(today=datetime.now().strftime("%d %B %Y"), n=n,
                           covered="\n".join(f"- {c['date']}: {c.get('topic_en') or c.get('title')}"
                                             for c in covered) or "- (none)")
    text, urls, _ = claude_web_research(cfg, prompt, credible, max_uses=int(nc.get("topic_searches", 15)),
                                        log=log, finish_tag="topics")
    m = re.search(r"<topics>(.*?)</topics>", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise RuntimeError(f"No topics JSON in the reply:\n{text[:400]}")
    body = m.group(1)
    raw, _ = json.JSONDecoder().raw_decode(body[body.find("{"):])
    kept, dropped = filter_topics(raw.get("topics", []), credible, urls, covered, keep)
    data = {"created": datetime.now().isoformat(timespec="seconds"), "topics": kept,
            "dropped": [{"title": a, "reason": b} for a, b in dropped],
            "candidates": raw.get("topics", [])}
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    (TOPICS_DIR / f"{datetime.now():%Y%m%d}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def latest(day=None):
    f = TOPICS_DIR / f"{day or datetime.now().strftime('%Y%m%d')}.json"
    if not f.exists():
        files = sorted(TOPICS_DIR.glob("*.json"))
        f = files[-1] if files else None
    return (f.stem, json.loads(f.read_text(encoding="utf-8"))) if f else (None, None)


def card_text(t, i=None):
    """Plain-text card (terminal and Discord)."""
    s = t.get("safety") or {}
    flags = [k for k, v in s.items() if v]
    ad = t.get("ad_friendliness") or {}
    src = "\n".join(f"  - {x.get('outlet')} {x.get('date', '')}: {x.get('url')}" for x in t["sources"])
    head = f"{'' if i is None else f'{i}. '}{('[' + t['label'] + '] ') if t.get('label') else ''}{t.get('title_th')}"
    return (f"{head}\n{t.get('summary_th', '')}\nWhy trending: {t.get('why_trending', '')}\n"
            + (f"Update: {t['update_what']}\n" if t.get("label") else "")
            + f"Ad-friendly: {ad.get('level', '?')} - {ad.get('reason', '')}\n"
            f"Safety flags: {', '.join(flags) or 'none'}\nSources ({len(t['outlets'])} outlets):\n{src}")
