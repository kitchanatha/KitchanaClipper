"""Popular-video ideas: find the most-viewed recent Shorts in a channel's niche on YouTube and
turn them into ORIGINAL topic ideas for our own videos (never their titles, scripts or footage).

Config, per channel:  trend_queries: ["amazing facts", ...]   (searches; English results)
                      trend_days: 30                         (only videos from the last N days)
Uses the YouTube Data API with a saved sign-in (read-only); each search costs 100 of the
10,000 daily quota units.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT, load_history
from .script import ask_claude

IDEAS_PROMPT = """You plan videos for the YouTube Shorts channel "{name}".

Channel style:
{style}

These are the most-viewed Shorts in this niche from the last {days} days (title - views):
{popular}

Topics this channel already made (don't repeat them):
{recent}

Suggest {n} topic ideas for NEW, ORIGINAL videos that would attract the same viewers. Rules:
- Use the popular videos only to see which subjects and hooks people want. Never reuse a title,
  a script, a channel's catchphrase or a series name, and never suggest re-uploading or
  reacting to someone else's video.
- Each idea must be a real, well-documented fact or phenomenon you are confident about (it
  will be narrated as fact). Nothing that depends on recent news, rumours or one study.
- No real private individuals, no tragedies played for shock, nothing medical as advice.
- Vary the subjects (animals, space, ocean, human body, earth, history, engineering, ...).

Return ONLY JSON: {{"ideas": [{{"idea": "one-sentence topic in English", "hook": "a first line \
under 12 words", "inspired_by": [indexes of the popular videos it relates to]}}]}}"""


def _service(cfg, key):
    """A YouTube client from this channel's sign-in, or any other saved sign-in (search is
    public data - which account asks doesn't matter)."""
    from .youtube import _service as yt
    chans = cfg["channels"]
    order = [key] + [k for k in chans if k != key]
    for k in order:
        if (ROOT / chans[k].get("youtube_token", "-")).exists():
            return yt(chans[k])
    raise SystemExit("No YouTube sign-in found - run: python run.py auth --channel <key>")


def popular_videos(cfg, key, top=15):
    """[{title, channel, views, url, published}] most-viewed first."""
    ch = cfg["channels"][key]
    queries = ch.get("trend_queries") or []
    if not queries:
        raise SystemExit(f"{ch['name']} has no trend_queries in config.yaml")
    since = (datetime.now(timezone.utc) - timedelta(days=int(ch.get("trend_days", 30))))
    yt = _service(cfg, key)
    ids = []
    for q in queries:
        r = yt.search().list(part="id", q=q, type="video", videoDuration="short", order="viewCount",
                             publishedAfter=since.isoformat().replace("+00:00", "Z"),
                             relevanceLanguage=ch.get("language", "en"), maxResults=25).execute()
        ids += [i["id"]["videoId"] for i in r.get("items", []) if i["id"].get("videoId")]
    ids = list(dict.fromkeys(ids))
    vids = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics", id=",".join(ids[i:i + 50])).execute()
        for v in r.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            vids.append({"title": sn["title"], "channel": sn["channelTitle"],
                         "views": int(st.get("viewCount", 0)), "published": sn["publishedAt"][:10],
                         "url": f"https://youtube.com/shorts/{v['id']}"})
    vids.sort(key=lambda v: v["views"], reverse=True)
    return vids[:top]


def suggest_ideas(cfg, key, videos, n=8):
    ch = cfg["channels"][key]
    recent = load_history().get(key, {}).get("topics", [])[-60:]
    popular = "\n".join(f"{i}. {v['title']} - {v['views']:,} views" for i, v in enumerate(videos))
    text = ask_claude(cfg, IDEAS_PROMPT.format(
        name=ch["name"], style=ch["style"].strip(), days=ch.get("trend_days", 30), popular=popular,
        recent="\n".join(f"- {t}" for t in recent) or "- (none yet)", n=n), max_tokens=8000)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Claude did not return JSON:\n{text[:300]}")
    return [i for i in json.loads(m.group(0)).get("ideas", []) if i.get("idea")][:n]


def ideas_file(key):
    return ROOT / "data" / f"ideas_{key}.json"


def find_ideas(cfg, key, n=8, log=print):
    """Search, suggest, save to data/ideas_<key>.json. Returns the saved dict."""
    log(f"Searching YouTube for popular {cfg['channels'][key]['name']} topics...")
    videos = popular_videos(cfg, key)
    if not videos:
        raise SystemExit("No popular videos found - try other trend_queries")
    log(f"Found {len(videos)} popular videos - writing original ideas...")
    data = {"created": datetime.now().isoformat(timespec="seconds"), "channel": key,
            "popular": videos, "ideas": suggest_ideas(cfg, key, videos, n)}
    f = ideas_file(key)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_ideas(key):
    f = ideas_file(key)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def next_idea(cfg, key, max_age_days=7, log=print):
    """For the daily run: the next idea not made yet. Searches for a fresh list when the saved
    one is used up or older than max_age_days."""
    data = load_ideas(key)
    fresh = data and datetime.fromisoformat(data["created"]) > datetime.now() - timedelta(days=max_age_days)
    if not fresh or all(i.get("made") for i in data["ideas"]):
        data = find_ideas(cfg, key, log=log)
    return next(i for i in data["ideas"] if not i.get("made"))


def mark_made(key, idea):
    """Remember that this idea became a video (daily run and Make buttons skip it)."""
    data = load_ideas(key)
    if not data or not idea:
        return
    for i in data["ideas"]:
        if i.get("idea") == idea.get("idea"):
            i["made"] = True
    ideas_file(key).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ideas_text(data, limit=1900):
    """Discord / console summary: top popular videos, then the numbered ideas."""
    lines = ["**Most viewed lately** (for reference - we never copy them):"]
    for v in data["popular"][:5]:
        lines.append(f"• {v['title'][:70]} - {v['views']:,} views")
    lines.append("\n**Ideas for our own videos** - tap a number to make it:")
    for i, idea in enumerate(data["ideas"], 1):
        done = " ✅ made" if idea.get("made") else ""
        lines.append(f"**{i}.** {idea['idea']}{done}\n    _{idea.get('hook', '')}_")
    return "\n".join(lines)[:limit]
