"""TikTok drafts: the approved video goes to the channel's TikTok inbox; you open TikTok and post it.

Docs checked Oct 2026 (Content Posting API v2, "Upload" = draft flow):
  POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/
       {"source_info": {"source": "FILE_UPLOAD", "video_size", "chunk_size", "total_chunk_count"}}
       -> publish_id, upload_url (6 requests / min per token)
  PUT  upload_url, chunk by chunk: Content-Range "bytes a-b/size", Content-Type video/mp4
       (chunks 5-64 MB, the last one may be up to 128 MB; files < 5 MB go whole)
  POST /v2/post/publish/status/fetch/ {"publish_id"} -> PROCESSING_UPLOAD / SEND_TO_USER_INBOX / FAILED
       (30 requests / min). At most 5 pending drafts per account per 24 h.
  The draft API takes no caption and has no AI-content field: the caption is posted in Discord for
  you to paste, and you switch on "AI-generated content" in TikTok when posting.
Login Kit for Desktop: https://www.tiktok.com/v2/auth/authorize/ with PKCE (S256; TikTok wants the
HEX-encoded SHA-256 of the verifier), redirect http://127.0.0.1:<port>/tiktok-callback (registered
as http://127.0.0.1:*/tiktok-callback). Token: POST /v2/oauth/token/ - access token 24 h, refresh
token 365 days; refreshed automatically here. Scopes: user.info.basic (check the account),
video.upload. Saved to tokens/tiktok_<key>.json.
"""
import hashlib
import json
import math
import secrets
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .common import ROOT, env

API = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload"
PORT = 8766
REDIRECT = f"http://127.0.0.1:{PORT}/tiktok-callback"
MAX_TRIES = 3
DRAFTS_PER_DAY = 5
MB = 1024 * 1024
CHUNK = 10 * MB


def tt_settings(cfg, key):
    """channels.<key>.crosspost.tiktok -> {enabled, mode, display_name}."""
    t = ((cfg["channels"][key].get("crosspost") or {}).get("tiktok") or {})
    return {"enabled": bool(t.get("enabled")), "mode": t.get("mode", "draft"),
            "display_name": str(t.get("display_name") or "").strip()}


def token_file(key):
    return ROOT / "tokens" / f"tiktok_{key}.json"


def load_token(key):
    f = token_file(key)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _save_token(key, data):
    f = token_file(key)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- HTTP with retries

class TikTokError(RuntimeError):
    def __init__(self, what, status, body):
        self.status, self.body = status, body
        err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
        self.code = err.get("code") or (body or {}).get("error") if isinstance(body, dict) else None
        self.transient = status >= 500 or status == 429 or self.code in ("rate_limit_exceeded", "internal_error")
        msg = err.get("message") or (body or {}).get("error_description") if isinstance(body, dict) else None
        super().__init__(f"{what}: TikTok {status} {self.code or ''} {msg or str(body)[:300]}".strip())


def _ok(r, body):
    if r.status_code not in (200, 201, 206):
        return False
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code") in (None, "", "ok")
    return not (isinstance(body, dict) and body.get("error"))  # token endpoint style errors


def call(method, url, what, tries=MAX_TRIES, sleep=time.sleep, **kw):
    """One API request; rate limits / 5xx / network errors retried (max 3 tries, 5 s then 15 s)."""
    for attempt in range(1, tries + 1):
        try:
            r = requests.request(method, url, timeout=kw.pop("timeout", 120), **kw)
            try:
                body = r.json() if r.content else {}
            except ValueError:
                body = {"raw": r.text[:300]}
            if _ok(r, body):
                return body
            err = TikTokError(what, r.status_code, body)
            if not err.transient or attempt == tries:
                raise err
        except requests.RequestException as e:
            if attempt == tries:
                raise RuntimeError(f"{what}: network error {e}") from e
        sleep(5 * 3 ** (attempt - 1))


# ---------------------------------------------------------------- auth (you log in in the browser)

def pkce_pair():
    """TikTok desktop PKCE: verifier 43-128 unreserved chars, challenge = HEX sha256 (not base64url)."""
    verifier = secrets.token_urlsafe(64)[:96]
    return verifier, hashlib.sha256(verifier.encode()).hexdigest()


def _catch_code(state, wait_s=300):
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/tiktok-callback":
                got.update({k: v[0] for k, v in parse_qs(u.query).items()})
                ok = "code" in got and got.get("state") == state
                if ok:
                    msg = "TikTok connected - you can close this tab and go back to the terminal."
                elif "code" in got:  # a tab from an older login attempt
                    msg = ("This login tab is from an older attempt (state does not match). Close the old "
                           "TikTok tabs and run tiktok-auth again.")
                    got.clear()  # keep waiting for the right tab
                else:
                    msg = f"Login failed: {got.get('error_description') or got.get('error') or 'no code'}"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{msg}</h2></body></html>".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    t0 = time.time()
    while "code" not in got and "error" not in got and time.time() - t0 < wait_s:
        time.sleep(0.3)
    srv.shutdown()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"No TikTok login code received ({got.get('error_description') or got.get('error') or 'timed out'}).")
    return got["code"]


def _keys():
    ck, cs = env("TIKTOK_CLIENT_KEY"), env("TIKTOK_CLIENT_SECRET")
    if not ck or not cs:
        raise SystemExit("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are missing in .env (README section 13).")
    return ck, cs


def _user_info(access_token, sleep=time.sleep):
    body = call("GET", f"{API}/user/info/", "user info", params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {access_token}"}, sleep=sleep)
    return (body.get("data") or {}).get("user") or {}


def _store(key, tok, user, old=None):
    now = time.time()
    data = {"channel": key, "open_id": tok.get("open_id") or (old or {}).get("open_id"),
            "display_name": user.get("display_name") or (old or {}).get("display_name"),
            "scope": tok.get("scope") or (old or {}).get("scope"),
            "access_token": tok["access_token"], "expires_at": now + int(tok.get("expires_in") or 86400),
            "refresh_token": tok.get("refresh_token") or (old or {}).get("refresh_token"),
            "refresh_expires_at": now + int(tok["refresh_expires_in"]) if tok.get("refresh_expires_in")
            else (old or {}).get("refresh_expires_at"),
            "updated": datetime.now().isoformat(timespec="seconds")}
    _save_token(key, data)
    return data


def auth(cfg, key, open_browser=True):
    """Browser login (PKCE) -> tokens/tiktok_<key>.json, after checking the account."""
    ck, cs = _keys()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urlencode({"client_key": ck, "scope": SCOPES, "response_type": "code",
                                      "redirect_uri": REDIRECT, "state": state,
                                      "code_challenge": challenge, "code_challenge_method": "S256"})
    want = tt_settings(cfg, key)["display_name"]
    print(f"Opening TikTok in your browser - log in yourself with the {cfg['channels'][key]['name']} "
          f"TikTok account{f' ({want})' if want else ''} and allow access.\n"
          f"If it doesn't open, paste this into the browser:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    code = _catch_code(state)
    tok = call("POST", f"{API}/oauth/token/", "code exchange", data={
        "client_key": ck, "client_secret": cs, "code": code, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code_verifier": verifier},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    missing = [s for s in SCOPES.split(",") if s not in (tok.get("scope") or "")]
    if missing:
        raise SystemExit(f"TikTok did not grant {', '.join(missing)} - tick them on the consent screen.")
    user = _user_info(tok["access_token"])
    if want and user.get("display_name") != want:
        raise SystemExit(f"You logged in as '{user.get('display_name')}', but config.yaml says this channel's "
                         f"TikTok is '{want}'. Nothing saved - log in with the right account.")
    data = _store(key, tok, user)
    print(f"Saved {token_file(key).name}: TikTok '{data['display_name']}' (open_id {data['open_id'][:8]}...). "
          f"The login renews itself for a year.")
    if not want:
        print(f"Tip: put  display_name: \"{data['display_name']}\"  under channels.{key}.crosspost.tiktok so a "
              f"wrong account is refused later.")
    return data


def access_token(key, sleep=time.sleep):
    """A valid access token, refreshed when it has < 10 minutes left."""
    t = load_token(key)
    if not t:
        raise RuntimeError(f"no tokens/tiktok_{key}.json - run python run.py tiktok-auth --channel {key}")
    if t["expires_at"] - time.time() > 600:
        return t["access_token"]
    if not t.get("refresh_token") or (t.get("refresh_expires_at") or 0) < time.time():
        raise RuntimeError(f"TikTok login for {key} expired - run python run.py tiktok-auth --channel {key}")
    ck, cs = _keys()
    tok = call("POST", f"{API}/oauth/token/", "token refresh", data={
        "client_key": ck, "client_secret": cs, "grant_type": "refresh_token",
        "refresh_token": t["refresh_token"]}, headers={"Content-Type": "application/x-www-form-urlencoded"},
        sleep=sleep)
    return _store(key, tok, {}, old=t)["access_token"]


# ---------------------------------------------------------------- checks

def token_status(cfg, key, sleep=time.sleep):
    """{ok, display_name, expires, problem}: token present, for THIS channel and the same account."""
    t = load_token(key)
    if not t:
        return {"ok": False, "problem": f"no tokens/tiktok_{key}.json - run python run.py tiktok-auth --channel {key}"}
    if t.get("channel") != key:
        return {"ok": False, "problem": f"token file belongs to channel {t.get('channel')} - not posting"}
    want = tt_settings(cfg, key)["display_name"]
    try:
        user = _user_info(access_token(key, sleep=sleep), sleep=sleep)
    except Exception as e:
        return {"ok": False, "problem": f"token rejected: {str(e)[:200]} - run tiktok-auth again"}
    if user.get("open_id") and user["open_id"] != t.get("open_id"):
        return {"ok": False, "problem": "token answers for a different TikTok account - not posting"}
    if want and user.get("display_name") != want:
        return {"ok": False, "problem": f"TikTok account is '{user.get('display_name')}', config says '{want}' - not posting"}
    exp = t.get("refresh_expires_at")
    return {"ok": True, "display_name": user.get("display_name") or t.get("display_name"),
            "expires": datetime.fromtimestamp(exp).strftime("%d %b %Y") if exp else "?"}


def video_problems(path):
    """TikTok upload limits: MP4/H.264, 23-60 fps, 360-4096 px each side, <= 10 min, <= 4 GB."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,codec_name:format=duration,size", "-of", "json",
                          str(path)], capture_output=True, text=True)
    try:
        info = json.loads(out.stdout)
        st = info["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        num, den = (st.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den or 1)
        dur, size = float(info["format"]["duration"]), int(info["format"]["size"])
    except (ValueError, KeyError, IndexError):
        return [f"could not read the video ({out.stderr.strip()[:120]})"]
    p = []
    if st.get("codec_name") not in ("h264", "hevc", "vp8", "vp9"):
        p.append(f"codec {st.get('codec_name')} (TikTok wants H.264)")
    if not 22.9 <= fps <= 60.1:
        p.append(f"{fps:.0f} fps (TikTok needs 23-60)")
    if not (360 <= w <= 4096 and 360 <= h <= 4096):
        p.append(f"{w}x{h} (each side 360-4096 px)")
    if dur > 600:
        p.append(f"{dur:.0f} s (max 10 min)")
    if size > 4 * 1024 * MB:
        p.append("larger than 4 GB")
    return p


def chunk_plan(size, chunk=CHUNK):
    """(chunk_size, total_chunk_count): < 5 MB whole; else 10 MB chunks, the remainder joins the
    last chunk (TikTok: total = floor(size / chunk), last chunk up to 128 MB)."""
    if size < 5 * MB:
        return size, 1
    chunk = min(max(chunk, 5 * MB), 64 * MB)
    return chunk, max(1, math.floor(size / chunk))


def _drafts_last_24h(key):
    f = ROOT / "data" / "tiktok_drafts.json"
    log = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return [t for t in log.get(key, []) if t > time.time() - 86400], log, f


def _remember_draft(key):
    recent, log, f = _drafts_last_24h(key)
    log[key] = recent + [time.time()]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(log), encoding="utf-8")


# ---------------------------------------------------------------- upload

def upload_draft(cfg, key, video, wait_s=300, sleep=time.sleep):
    """Send the file to the channel's TikTok inbox. Returns (publish_id, display_name)."""
    st = token_status(cfg, key, sleep=sleep)
    if not st["ok"]:  # never send one channel's video to another channel's account
        raise RuntimeError(st["problem"])
    if len(_drafts_last_24h(key)[0]) >= DRAFTS_PER_DAY:
        raise RuntimeError(f"{DRAFTS_PER_DAY} TikTok drafts already sent in 24 h (TikTok's limit) - post those first")
    token = access_token(key, sleep=sleep)
    data = Path(video).read_bytes()
    size = len(data)
    chunk, count = chunk_plan(size)
    auth_h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    init = call("POST", f"{API}/post/publish/inbox/video/init/", "tiktok init", headers=auth_h,
                json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                                      "total_chunk_count": count}}, sleep=sleep)["data"]
    pid, url = init["publish_id"], init["upload_url"]
    for i in range(count):
        a = i * chunk
        b = size - 1 if i == count - 1 else a + chunk - 1
        part = data[a:b + 1]
        call("PUT", url, f"tiktok upload chunk {i + 1}/{count}", data=part, timeout=600, sleep=sleep,
             headers={"Content-Type": "video/mp4", "Content-Length": str(len(part)),
                      "Content-Range": f"bytes {a}-{b}/{size}"})
    _remember_draft(key)
    t0 = time.time()
    while True:
        s = call("POST", f"{API}/post/publish/status/fetch/", "tiktok status", headers=auth_h,
                 json={"publish_id": pid}, sleep=sleep).get("data", {})
        status = s.get("status")
        if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return pid, st["display_name"]
        if status == "FAILED":
            raise RuntimeError(f"TikTok rejected the draft: {s.get('fail_reason', 'unknown')}")
        if time.time() - t0 > wait_s:
            return pid, st["display_name"]  # still processing - it usually lands in the inbox soon
        sleep(5)
