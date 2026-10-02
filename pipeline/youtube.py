"""Step 6 - upload approved videos to YouTube, scheduled."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRET = ROOT / "client_secret.json"
SCHEDULE = ROOT / "data" / "schedule.json"


def _creds(ch, interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token = ROOT / ch["youtube_token"]
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise SystemExit(f"Not signed in for {ch['name']}. Run: python run.py auth --channel <key>")
        if not CLIENT_SECRET.exists():
            raise SystemExit("client_secret.json is missing - see README step 4.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
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
    if title.strip().lower() != ch["name"].strip().lower():
        print(f"WARNING: expected '{ch['name']}'. Delete {ch['youtube_token']} and run auth again "
              "if this is the wrong channel.")


def next_slot(cfg, key):
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
        for t in sorted(yc["publish_times"]):
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
    slot = next_slot(cfg, key)
    tags = list(dict.fromkeys(ch.get("hashtags", []) + meta.get("hashtags", [])))
    desc = "\n\n".join(x for x in [meta.get("description", ""), ch.get("description_footer", ""),
                                   " ".join(tags)] if x)
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": desc[:4900],
            "tags": [t.lstrip("#") for t in tags],
            "categoryId": cfg["youtube"]["category_id"],
            "defaultLanguage": ch.get("language", "en"),
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
    return resp["id"], slot
