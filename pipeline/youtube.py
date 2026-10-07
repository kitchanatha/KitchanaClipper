"""Step 6 - upload approved videos to YouTube, scheduled."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
# Story channels also upload caption tracks, which needs youtube.force-ssl. Kept separate so the
# Shorts channels' existing sign-ins stay valid (asking a saved token for new scopes fails).
STORY_SCOPES = SCOPES + ["https://www.googleapis.com/auth/youtube.force-ssl"]
YT_LANG = {"en": "en", "th": "th", "zh": "zh-Hans"}


def _scopes(ch):
    return STORY_SCOPES if ch.get("type") == "story" else SCOPES
CLIENT_SECRET = ROOT / "client_secret.json"
SCHEDULE = ROOT / "data" / "schedule.json"


def _creds(ch, interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token = ROOT / ch["youtube_token"]
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), _scopes(ch))
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise SystemExit(f"Not signed in for {ch['name']}. Run: python run.py auth --channel <key>")
        if not CLIENT_SECRET.exists():
            raise SystemExit("client_secret.json is missing - see README step 4.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), _scopes(ch))
        print(f"\nA browser will open. Sign in and CHOOSE THE '{ch['name']}' CHANNEL.\n")
        creds = flow.run_local_server(port=0, prompt="consent")
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service(ch, interactive=False):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_creds(ch, interactive), cache_discovery=False)


def auth(ch):
    yt = _service(ch, interactive=True)
    me = yt.channels().list(part="snippet", mine=True).execute()
    title = me["items"][0]["snippet"]["title"] if me.get("items") else "?"
    print(f"Signed in to YouTube channel: {title}")
    if ch["name"].strip().lower() not in title.strip().lower():  # "Name (English name)" is fine
        print(f"WARNING: expected '{ch['name']}'. Delete {ch['youtube_token']} and run auth again "
              "if this is the wrong channel.")


def next_slot(cfg, key, times=None):
    """Next free publish time for this channel (one video per slot)."""
    yc = cfg["youtube"]
    sign = 1 if yc["utc_offset"][0] == "+" else -1
    hh, mm = map(int, yc["utc_offset"][1:].split(":"))
    tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    taken = json.loads(SCHEDULE.read_text()) if SCHEDULE.exists() else {}
    used = set(taken.get(key, []))
    now = datetime.now(tz) + timedelta(minutes=30)
    day = now.date()
    for _ in range(60):
        for t in sorted(times or yc["publish_times"]):
            h, m = map(int, t.split(":"))
            slot = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            iso = slot.isoformat()
            if slot > now and iso not in used:
                taken.setdefault(key, []).append(iso)
                SCHEDULE.parent.mkdir(parents=True, exist_ok=True)
                SCHEDULE.write_text(json.dumps(taken, indent=2))
                return slot
        day += timedelta(days=1)
    raise RuntimeError("No free publish slot found")


def upload(cfg, key, ch, folder):
    from googleapiclient.http import MediaFileUpload

    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    kind = meta.get("kind", "short")
    lang = YT_LANG.get(ch.get("language", "en"), ch.get("language", "en"))
    if kind in ("story", "trailer"):  # description (with credits) and tags made by story-build
        st = cfg.get("story", {})
        slot = next_slot(cfg, key, st.get("publish_times") if kind == "story" else None)
        desc, tags = meta["description"], meta.get("tags", [])
        category = str(st.get("category_id", cfg["youtube"]["category_id"]))
    elif kind == "news":  # description with the source list, made by news-build
        nc = cfg.get("news", {})
        slot = next_slot(cfg, key, nc.get("publish_times"))
        desc, tags = meta["description"], meta.get("tags", [])
        category = str(nc.get("category_id", "25"))
    else:
        slot = next_slot(cfg, key)
        tags = list(dict.fromkeys(ch.get("hashtags", []) + meta.get("hashtags", [])))
        desc = "\n\n".join(x for x in [meta.get("description", ""), ch.get("description_footer", ""),
                                       " ".join(tags)] if x)
        category = cfg["youtube"]["category_id"]
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": desc[:4900],
            "tags": [t.lstrip("#") for t in tags],
            "categoryId": category,
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": bool(cfg["youtube"].get("contains_synthetic_media", True)),
        },
    }
    # Unverified Google Cloud projects can only upload PRIVATE videos (YouTube rule), so a
    # scheduled publish time would be ignored. Until your project passes YouTube's API audit,
    # the video uploads private and you schedule it in YouTube Studio at the suggested slot.
    if cfg["youtube"].get("api_verified", False):
        body["status"]["publishAt"] = slot.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    yt = _service(ch)
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(folder / "video.mp4"),
                                                        chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    if kind == "story":
        warnings = _story_extras(yt, vid, folder, meta, lang)
        if warnings:  # the video is up - report, don't fail (a retry would upload it twice)
            meta["upload_warnings"] = warnings
            (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
            for w in warnings:
                print(f"WARNING: {w}")
    return vid, slot


def _story_extras(yt, vid, folder, meta, lang):
    """Custom thumbnail + SRT caption track. Returns a list of warnings."""
    from googleapiclient.http import MediaFileUpload

    warnings = []
    thumb = folder / meta.get("thumbnail", "thumbnail.jpg")
    if thumb.exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb),
                                                                        mimetype="image/jpeg")).execute()
        except Exception as e:  # needs a phone-verified channel
            warnings.append(f"thumbnail not set ({str(e)[:160]}) - verify the channel by phone, "
                            "then add thumbnail.jpg in YouTube Studio")
    srt = folder / meta.get("captions", "subtitles.srt")
    if srt.exists():
        try:
            yt.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": vid, "language": lang, "name": "Subtitles",
                                  "isDraft": False}},
                media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream")).execute()
        except Exception as e:
            warnings.append(f"captions not uploaded ({str(e)[:160]}) - add subtitles.srt in "
                            "YouTube Studio > Subtitles")
    return warnings
