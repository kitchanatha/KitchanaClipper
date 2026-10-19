"""Weekly report: last 7 days per channel from the YouTube Data API (views, likes, comments per
video; subscribers per channel) and, when a channel has the extra Analytics sign-in, average view
percentage. Rows are appended to data/weekly_stats.csv; the summary goes to Discord.

Analytics needs the scope yt-analytics.readonly. It is kept in a SEPARATE token
(tokens/<key>_analytics.json, made by `python run.py auth-analytics --channel <key>`), so the
existing upload sign-ins never change.
"""
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT

STATS = ROOT / "data" / "weekly_stats.csv"
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
                    "https://www.googleapis.com/auth/youtube.readonly"]


def analytics_token(ch):
    return ROOT / ch["youtube_token"].replace(".json", "_analytics.json")


def auth_analytics(ch):
    """One-time browser sign-in for YouTube Analytics (read-only), saved separately."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from .youtube import CLIENT_SECRET
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), ANALYTICS_SCOPES)
    print(f"\nA browser will open. Sign in and CHOOSE THE '{ch['name']}' CHANNEL (read-only analytics).\n")
    creds = flow.run_local_server(port=0, prompt="consent")
    p = analytics_token(ch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {p.name}")


def _analytics(ch, ids, start, end):
    """{video_id: average view percentage} or {} without the analytics sign-in."""
    p = analytics_token(ch)
    if not p.exists() or not ids:
        return {}
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(p), ANALYTICS_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        p.write_text(creds.to_json(), encoding="utf-8")
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    out = {}
    for i in range(0, len(ids), 200):
        r = ya.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                               metrics="averageViewPercentage", dimensions="video",
                               filters="video==" + ",".join(ids[i:i + 200]), maxResults=200).execute()
        for row in r.get("rows", []):
            out[row[0]] = round(float(row[1]), 1)
    return out


def channel_week(cfg, key, days=7):
    """{name, subscribers, videos:[{id, title, published, views, likes, comments, avg_pct}], analytics}"""
    from .youtube import _service
    ch = cfg["channels"][key]
    yt = _service(ch)
    me = yt.channels().list(part="statistics,contentDetails", mine=True).execute()["items"][0]
    uploads = me["contentDetails"]["relatedPlaylists"]["uploads"]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    ids, token = [], None
    while True:  # newest first; stop once older than the window
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                    pageToken=token).execute()
        page = [(i["contentDetails"]["videoId"], i["contentDetails"].get("videoPublishedAt", ""))
                for i in r.get("items", [])]
        ids += [v for v, p in page if not p or p >= since.isoformat()[:19]]
        token = r.get("nextPageToken")
        if not token or (page and page[-1][1] and page[-1][1] < since.isoformat()[:19]):
            break
    vids = []
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,statistics,status", id=",".join(ids[i:i + 50])).execute().get("items", []):
            st = v.get("statistics", {})
            vids.append({"id": v["id"], "title": v["snippet"]["title"],
                         "published": v["snippet"]["publishedAt"][:10],
                         "privacy": v.get("status", {}).get("privacyStatus", ""),
                         "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                         "comments": int(st.get("commentCount", 0))})
    pct = {}
    try:
        pct = _analytics(ch, [v["id"] for v in vids], (since.date()), date.today())
    except Exception:  # analytics is optional
        pct = {}
    for v in vids:
        v["avg_pct"] = pct.get(v["id"])
    return {"key": key, "name": ch["name"], "subscribers": int(me["statistics"].get("subscriberCount", 0)),
            "videos": vids, "analytics": analytics_token(ch).exists()}


def weekly(cfg, keys, days=7):
    """Collect, append to data/weekly_stats.csv, return the per-channel data."""
    out, today = [], date.today().isoformat()
    for k in keys:
        if not (ROOT / cfg["channels"][k].get("youtube_token", "-")).exists():
            continue
        try:
            out.append(channel_week(cfg, k, days))
        except Exception as e:
            out.append({"key": k, "name": cfg["channels"][k]["name"], "error": str(e)[:200], "videos": []})
    new = not STATS.exists()
    STATS.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["report_date", "channel", "subscribers", "video_id", "title", "published",
                        "views", "likes", "comments", "avg_view_pct"])
        for c in out:
            for v in c["videos"]:
                w.writerow([today, c["name"], c.get("subscribers", ""), v["id"], v["title"], v["published"],
                            v["views"], v["likes"], v["comments"], v["avg_pct"] if v["avg_pct"] is not None else ""])
    return out


def summary(data, limit=1900):
    """Discord text: per channel totals, top 3 / bottom 3, best channel, one suggested change."""
    lines, avgs = ["**Weekly report (last 7 days)**"], {}
    for c in data:
        if c.get("error"):
            lines.append(f"\n**{c['name']}**: couldn't read stats ({c['error']})")
            continue
        vids = sorted(c["videos"], key=lambda v: v["views"], reverse=True)
        total = sum(v["views"] for v in vids)
        avg = total / len(vids) if vids else 0
        avgs[c["name"]] = avg
        pcts = [v["avg_pct"] for v in vids if v["avg_pct"] is not None]
        lines.append(f"\n**{c['name']}** - {len(vids)} videos, {total:,} views (avg {avg:,.0f}), "
                     f"{c['subscribers']:,} subscribers"
                     + (f", avg viewed {sum(pcts) / len(pcts):.0f}%" if pcts else
                        "" if c.get("analytics") else " (avg % viewed: run auth-analytics)"))

        def row(v):
            return (f"  {v['views']:,} · {v['title'][:55]}"
                    + (f" · {v['avg_pct']}%" if v["avg_pct"] is not None else ""))
        if vids:
            lines.append(" Top: \n" + "\n".join(row(v) for v in vids[:3]))
            if len(vids) > 3:
                lines.append(" Bottom: \n" + "\n".join(row(v) for v in vids[-3:]))
    if avgs:
        best = max(avgs, key=avgs.get)
        worst = min(avgs, key=avgs.get)
        lines.append(f"\nBest channel by views per video: **{best}** ({avgs[best]:,.0f}).")
        if worst != best and avgs[worst] < 0.25 * max(avgs[best], 1):
            lines.append(f"Suggested change: **{worst}** gets under a quarter of {best}'s views per "
                         f"video - move its daily slot to {best} or pause it for a week.")
        else:
            lines.append(f"Suggested change: make more topics like {best}'s top video this week.")
    return "\n".join(lines)[:limit]
