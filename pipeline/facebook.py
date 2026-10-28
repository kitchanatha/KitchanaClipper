"""Facebook Reels on each channel's own Facebook Page (Graph API, Reels publishing flow).

Docs checked Oct 2026 (Graph API v26.0):
  POST /{page-id}/video_reels upload_phase=start            -> video_id
  POST rupload.facebook.com/video-upload/{ver}/{video_id}    (headers: Authorization OAuth, offset, file_size)
  POST /{page-id}/video_reels upload_phase=finish, video_state=PUBLISHED|SCHEDULED,
       scheduled_publish_time (10 min - 29 days ahead), description, is_ai_generated
  GET  /{video_id}?fields=status                             -> processing / publishing phases
Permissions: pages_show_list, pages_read_engagement, pages_manage_posts (Page token).
Reels: 9:16, >= 540x960, 24-60 fps, 3-90 s. Max 30 API-published Reels per Page per 24 h.

Token: `python run.py fb-auth --channel <key>` - you log in to Facebook yourself in the browser
(OAuth dialog -> http://localhost:8765/fb-callback). The code is exchanged for a long-lived user
token, then for the Page token of channels.<key>.crosspost.facebook.page_id (Page tokens from a
long-lived user token don't expire). Saved to tokens/facebook_<key>.json - never in config / .env.
"""
import json
import secrets
import subprocess
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .common import ROOT, env

SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
IG_SCOPES = ["instagram_basic", "instagram_content_publish"]  # only when the channel's instagram is on
REDIRECT = "http://localhost:8765/fb-callback"
TRANSIENT_CODES = {1, 2, 4, 17, 341, 368}  # Meta "temporary / rate" error codes worth one more try
MAX_TRIES = 3
REELS_PER_DAY = 30


def graph_version(cfg):
    return (cfg.get("crosspost") or {}).get("graph_version", "v26.0")


def fb_settings(cfg, key):
    """channels.<key>.crosspost.facebook -> {enabled, page_id, mode}."""
    ch = cfg["channels"][key]
    fb = ((ch.get("crosspost") or {}).get("facebook") or {})
    return {"enabled": bool(fb.get("enabled")), "page_id": str(fb.get("page_id") or "").strip(),
            "mode": fb.get("mode", "publish")}


def token_file(key):
    return ROOT / "tokens" / f"facebook_{key}.json"


def load_token(key):
    f = token_file(key)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


# ---------------------------------------------------------------- HTTP with retries

class MetaError(RuntimeError):
    def __init__(self, what, status, body):
        self.status, self.body = status, body
        err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
        self.code = err.get("code")
        self.transient = bool(err.get("is_transient")) or self.code in TRANSIENT_CODES or status >= 500
        super().__init__(f"{what}: Meta API {status} {err.get('message') or str(body)[:300]}")


def call(method, url, what, tries=MAX_TRIES, sleep=time.sleep, **kw):
    """One Graph API request; transient errors / network problems are retried (max 3 tries,
    backoff 5 s, 15 s). Returns the JSON body."""
    for attempt in range(1, tries + 1):
        try:
            r = requests.request(method, url, timeout=kw.pop("timeout", 120), **kw)
            try:
                body = r.json()
            except ValueError:
                body = {"raw": r.text[:300]}
            if r.status_code == 200 and not (isinstance(body, dict) and body.get("error")):
                return body
            err = MetaError(what, r.status_code, body)
            if not err.transient or attempt == tries:
                raise err
        except requests.RequestException as e:  # network: always worth a retry
            if attempt == tries:
                raise RuntimeError(f"{what}: network error {e}") from e
        sleep(5 * 3 ** (attempt - 1))


# ---------------------------------------------------------------- auth (you log in in the browser)

def _catch_code(state, port=8765, wait_s=300):
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path == "/fb-callback":
                got.update({k: v[0] for k, v in q.items()})
                ok = "code" in got and got.get("state") == state
                msg = ("Facebook connected - you can close this tab and go back to the terminal."
                       if ok else f"Login failed: {got.get('error_description') or got.get('error') or 'no code'}")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{msg}</h2></body></html>".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    t0 = time.time()
    while "code" not in got and "error" not in got and time.time() - t0 < wait_s:
        time.sleep(0.3)
    srv.shutdown()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"No Facebook login code received ({got.get('error_description') or 'timed out'}).")
    return got["code"]


def auth(cfg, key, page_id=None, open_browser=True):
    """Browser login -> long-lived user token -> this channel's Page token -> tokens/facebook_<key>.json."""
    app_id, secret = env("META_APP_ID"), env("META_APP_SECRET")
    if not app_id or not secret:
        raise SystemExit("META_APP_ID / META_APP_SECRET are missing in .env (README section 13).")
    ver = graph_version(cfg)
    g = f"https://graph.facebook.com/{ver}"
    want = str(page_id or fb_settings(cfg, key)["page_id"]).strip()
    state = secrets.token_urlsafe(16)
    xp = (cfg["channels"][key].get("crosspost") or {})
    want_ig = bool((xp.get("instagram") or {}).get("enabled"))
    scopes = SCOPES + (IG_SCOPES if want_ig else [])
    url = f"https://www.facebook.com/{ver}/dialog/oauth?" + urlencode({
        "client_id": app_id, "redirect_uri": REDIRECT, "state": state, "scope": ",".join(scopes),
        "response_type": "code"})
    print(f"Opening Facebook in your browser - log in yourself and allow the Pages for "
          f"{cfg['channels'][key]['name']}.\nIf it doesn't open, paste this into the browser:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    code = _catch_code(state)
    short = call("GET", f"{g}/oauth/access_token", "code exchange", params={
        "client_id": app_id, "client_secret": secret, "redirect_uri": REDIRECT, "code": code})
    long = call("GET", f"{g}/oauth/access_token", "long-lived token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": secret,
        "fb_exchange_token": short["access_token"]})
    pages = call("GET", f"{g}/me/accounts", "list Pages", params={
        "fields": "id,name,access_token,tasks", "limit": 100, "access_token": long["access_token"]})["data"]
    if not want:
        lines = "\n".join(f"  {p['id']}  {p['name']}" for p in pages) or "  (none - check the Page roles)"
        raise SystemExit(f"No page_id set for {key}. Your Pages:\n{lines}\nPut the right one in config.yaml "
                         f"(channels.{key}.crosspost.facebook.page_id) and run fb-auth again.")
    page = next((p for p in pages if p["id"] == want), None)
    if not page:
        raise SystemExit(f"Page {want} is not in the Pages you allowed. Run fb-auth again and tick that Page.")
    if "CREATE_CONTENT" not in (page.get("tasks") or []):
        raise SystemExit(f"Your account can't create content on '{page['name']}' (tasks: {page.get('tasks')}).")
    me = call("GET", f"{g}/me", "verify Page token", params={"fields": "id,name", "access_token": page["access_token"]})
    if me.get("id") != want:
        raise SystemExit(f"Token check failed: got {me} instead of Page {want}.")
    ig = {}
    if want_ig:  # the Instagram professional account linked to this Page
        ig = call("GET", f"{g}/{want}", "linked Instagram", params={
            "fields": "instagram_business_account{id,username}", "access_token": page["access_token"]}
                  ).get("instagram_business_account") or {}
        if not ig.get("id"):
            raise SystemExit(f"No Instagram professional account is linked to '{page['name']}'. Link it in the "
                             f"Page's Settings > Linked accounts > Instagram, then run fb-auth again.")
    data = {"channel": key, "page_id": want, "page_name": page["name"], "page_token": page["access_token"],
            "ig_id": ig.get("id"), "ig_username": ig.get("username"),
            "user_token_expires": (datetime.now(timezone.utc) + timedelta(seconds=int(long.get("expires_in") or 0))
                                   ).isoformat(timespec="seconds") if long.get("expires_in") else None,
            "created": datetime.now().isoformat(timespec="seconds")}
    f = token_file(key)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {f.name}: Page '{page['name']}' ({want}). The Page token does not expire."
          + (f"\nInstagram: @{ig.get('username')} ({ig['id']})" if ig.get("id") else ""))
    return data


# ---------------------------------------------------------------- checks

def token_status(cfg, key):
    """{ok, page_name, problem} - token present and for THIS channel's Page."""
    s = fb_settings(cfg, key)
    t = load_token(key)
    if not t:
        return {"ok": False, "problem": f"no tokens/facebook_{key}.json - run python run.py fb-auth --channel {key}"}
    if t.get("channel") != key or not s["page_id"] or t.get("page_id") != s["page_id"]:
        return {"ok": False, "problem": f"token is for Page {t.get('page_id')} ({t.get('page_name')}), "
                                        f"config says {s['page_id'] or '(empty)'} - not posting"}
    try:
        me = call("GET", f"https://graph.facebook.com/{graph_version(cfg)}/me", "token check",
                  params={"fields": "id,name", "access_token": t["page_token"]}, tries=2)
    except Exception as e:
        return {"ok": False, "problem": f"token rejected: {str(e)[:200]} - run fb-auth again"}
    if me.get("id") != s["page_id"]:
        return {"ok": False, "problem": f"token belongs to {me} - not posting"}
    return {"ok": True, "page_name": me.get("name"), "page_id": s["page_id"], "expires": "never (Page token)"}


def video_problems(path):
    """Reels specs: vertical 9:16, >= 540x960, 24-60 fps, 3-90 s. Empty list = OK."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True)
    try:
        info = json.loads(out.stdout)
        st = info["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        num, den = (st.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den or 1)
        dur = float(info["format"]["duration"])
    except (ValueError, KeyError, IndexError):
        return [f"could not read the video ({out.stderr.strip()[:120]})"]
    p = []
    if not (h > w and abs(w / h - 9 / 16) < 0.02):
        p.append(f"not 9:16 ({w}x{h})")
    if w < 540 or h < 960:
        p.append(f"too small ({w}x{h}, minimum 540x960)")
    if not 23.9 <= fps <= 60.1:
        p.append(f"{fps:.0f} fps (Reels need 24-60)")
    if not 3 <= dur <= 90:
        p.append(f"{dur:.0f} s long (Reels need 3-90 s)")
    return p


def _posted_last_24h(key):
    f = ROOT / "data" / "facebook_posts.json"
    log = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    since = time.time() - 86400
    return [t for t in log.get(key, []) if t > since], log, f


def _remember_post(key):
    recent, log, f = _posted_last_24h(key)
    log[key] = recent + [time.time()]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(log), encoding="utf-8")


# ---------------------------------------------------------------- publish

def publish_reel(cfg, key, video, caption, slot=None, wait_s=900, sleep=time.sleep):
    """Upload + publish (or schedule at `slot` when mode is schedule). Returns (url, note)."""
    s = fb_settings(cfg, key)
    st = token_status(cfg, key)
    if not st["ok"]:  # never post one channel's video with another channel's Page token
        raise RuntimeError(st["problem"])
    if len(_posted_last_24h(key)[0]) >= REELS_PER_DAY:
        raise RuntimeError(f"{REELS_PER_DAY} Reels already posted in 24 h (Meta's limit) - try later")
    token = load_token(key)["page_token"]
    ver = graph_version(cfg)
    g = f"https://graph.facebook.com/{ver}"
    api = f"{g}/{s['page_id']}/video_reels"
    start = call("POST", api, "facebook start", data={"upload_phase": "start", "access_token": token}, sleep=sleep)
    vid = start["video_id"]
    data = Path(video).read_bytes()
    call("POST", start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{ver}/{vid}",
         "facebook upload", headers={"Authorization": f"OAuth {token}", "offset": "0",
                                     "file_size": str(len(data))}, data=data, timeout=600, sleep=sleep)
    finish = {"upload_phase": "finish", "video_id": vid, "description": caption,
              "is_ai_generated": "true", "access_token": token}  # our voiceovers are AI
    note = "published"
    when = None
    if s["mode"] == "schedule" and slot is not None:
        ts = int(slot.timestamp())
        if time.time() + 15 * 60 <= ts <= time.time() + 28 * 86400:
            finish.update(video_state="SCHEDULED", scheduled_publish_time=str(ts))
            when = slot
            note = f"scheduled for {slot:%d %b %H:%M}"
        else:
            note = "published now (the YouTube slot is outside Facebook's 15 min - 28 day window)"
    if "video_state" not in finish:
        finish["video_state"] = "PUBLISHED"
    call("POST", api, "facebook publish", data=finish, sleep=sleep)
    _remember_post(key)
    t0 = time.time()
    while True:  # wait until Facebook has processed it (or it's waiting for its schedule)
        stt = call("GET", f"{g}/{vid}", "facebook status", params={"fields": "status", "access_token": token},
                   sleep=sleep).get("status", {})
        vs = stt.get("video_status")
        pub = (stt.get("publishing_phase") or {}).get("status")
        if vs in ("error", "upload_failed", "expired"):
            raise RuntimeError(f"facebook processing failed: {json.dumps(stt)[:300]}")
        if vs == "ready" and (pub in ("complete", None) or when):
            break
        if time.time() - t0 > wait_s:
            note += " (still processing on Facebook - check the Page in a few minutes)"
            break
        sleep(10)
    return f"https://www.facebook.com/reel/{vid}", note
