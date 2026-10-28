import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pipeline import tiktok as tt

MB = 1024 * 1024


def cfg(display_name=""):
    return {"channels": {"wonder_minute": {"name": "Wonder Minute", "crosspost": {
        "tiktok": {"enabled": True, "mode": "draft", "display_name": display_name}}}}}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class ChunkTest(unittest.TestCase):
    def test_small_file_goes_whole(self):
        self.assertEqual(tt.chunk_plan(3 * MB), (3 * MB, 1))

    def test_shorts_go_whole(self):  # the 18 MB Short TikTok refused as "10 MB x 1 chunk"
        self.assertEqual(tt.chunk_plan(18_368_783), (18_368_783, 1))
        self.assertEqual(tt.chunk_plan(64 * MB), (64 * MB, 1))

    def test_big_file_remainder_joins_last_chunk(self):
        size, count = tt.chunk_plan(105 * MB)
        self.assertEqual((size, count), (10 * MB, 10))  # 9 x 10 MB + 15 MB (last chunk <= 128 MB)

    def test_chunk_size_kept_in_limits(self):
        self.assertEqual(tt.chunk_plan(200 * MB, chunk=100 * MB)[0], 64 * MB)
        self.assertEqual(tt.chunk_plan(200 * MB, chunk=1 * MB)[0], 5 * MB)


class PkceTest(unittest.TestCase):
    def test_hex_sha256_challenge(self):
        v, c = tt.pkce_pair()
        self.assertTrue(43 <= len(v) <= 128)
        self.assertEqual(c, hashlib.sha256(v.encode()).hexdigest())


class RetryTest(unittest.TestCase):
    def test_rate_limit_retried(self):
        seq = [Resp(429, {"error": {"code": "rate_limit_exceeded", "message": "slow"}}),
               Resp(200, {"data": {"x": 1}, "error": {"code": "ok"}})]
        with mock.patch.object(tt.requests, "request", side_effect=seq) as req:
            self.assertEqual(tt.call("GET", "u", "x", sleep=lambda s: None)["data"], {"x": 1})
        self.assertEqual(req.call_count, 2)

    def test_max_three_tries(self):
        bad = Resp(503, {"error": {"code": "internal_error", "message": "down"}})
        with mock.patch.object(tt.requests, "request", return_value=bad) as req:
            with self.assertRaises(tt.TikTokError):
                tt.call("GET", "u", "x", sleep=lambda s: None)
        self.assertEqual(req.call_count, 3)

    def test_permanent_error_once(self):
        bad = Resp(400, {"error": {"code": "invalid_params", "message": "bad"}})
        with mock.patch.object(tt.requests, "request", return_value=bad) as req:
            with self.assertRaises(tt.TikTokError):
                tt.call("GET", "u", "x", sleep=lambda s: None)
        self.assertEqual(req.call_count, 1)


class AccountCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = mock.patch.object(tt, "token_file", lambda key: self.tmp / f"tiktok_{key}.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def save(self, **t):
        base = {"channel": "wonder_minute", "open_id": "A", "display_name": "Wonder Minute",
                "access_token": "tok", "expires_at": time.time() + 3600, "refresh_token": "r",
                "refresh_expires_at": time.time() + 86400}
        base.update(t)
        (self.tmp / "tiktok_wonder_minute.json").write_text(json.dumps(base), encoding="utf-8")

    def test_other_channels_token_refused(self):
        self.save(channel="summed_up")
        self.assertFalse(tt.token_status(cfg(), "wonder_minute")["ok"])

    def test_other_account_refused(self):
        self.save()
        with mock.patch.object(tt, "_user_info", return_value={"open_id": "B", "display_name": "Other"}):
            self.assertFalse(tt.token_status(cfg(), "wonder_minute")["ok"])

    def test_wrong_display_name_refused(self):
        self.save()
        with mock.patch.object(tt, "_user_info", return_value={"open_id": "A", "display_name": "Other"}):
            self.assertFalse(tt.token_status(cfg("Wonder Minute"), "wonder_minute")["ok"])

    def test_matching_ok(self):
        self.save()
        with mock.patch.object(tt, "_user_info", return_value={"open_id": "A", "display_name": "Wonder Minute"}):
            self.assertTrue(tt.token_status(cfg("Wonder Minute"), "wonder_minute")["ok"])

    def test_upload_refused_before_any_request(self):
        self.save(channel="summed_up")
        with mock.patch.object(tt.requests, "request") as req:
            with self.assertRaises(RuntimeError):
                tt.upload_draft(cfg(), "wonder_minute", "video.mp4")
        req.assert_not_called()

    def test_expired_access_token_is_refreshed(self):
        self.save(expires_at=time.time() + 60)
        new = {"access_token": "tok2", "expires_in": 86400, "refresh_token": "r2", "refresh_expires_in": 31536000,
               "open_id": "A", "scope": "user.info.basic,video.upload"}
        with mock.patch.object(tt, "_keys", return_value=("k", "s")), \
                mock.patch.object(tt, "call", return_value=new):
            self.assertEqual(tt.access_token("wonder_minute"), "tok2")
        saved = json.loads((self.tmp / "tiktok_wonder_minute.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["refresh_token"], "r2")


if __name__ == "__main__":
    unittest.main()
