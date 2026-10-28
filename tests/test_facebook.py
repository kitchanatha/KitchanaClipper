import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import facebook as fb


def cfg_with(page_id="111", enabled=True):
    return {"crosspost": {"graph_version": "v26.0"},
            "channels": {"wonder_minute": {"name": "Wonder Minute", "crosspost": {
                "facebook": {"enabled": enabled, "page_id": page_id, "mode": "publish"}}}}}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class RetryTest(unittest.TestCase):
    def test_transient_error_retried_then_ok(self):
        seq = [Resp(500, {"error": {"message": "boom", "code": 2}}), Resp(200, {"id": "1"})]
        with mock.patch.object(fb.requests, "request", side_effect=seq) as req:
            self.assertEqual(fb.call("GET", "u", "x", sleep=lambda s: None), {"id": "1"})
        self.assertEqual(req.call_count, 2)

    def test_never_more_than_three_tries(self):
        bad = Resp(500, {"error": {"message": "down", "code": 2}})
        with mock.patch.object(fb.requests, "request", return_value=bad) as req:
            with self.assertRaises(fb.MetaError):
                fb.call("GET", "u", "x", sleep=lambda s: None)
        self.assertEqual(req.call_count, 3)

    def test_permanent_error_not_retried(self):
        bad = Resp(400, {"error": {"message": "bad param", "code": 100}})
        with mock.patch.object(fb.requests, "request", return_value=bad) as req:
            with self.assertRaises(fb.MetaError):
                fb.call("GET", "u", "x", sleep=lambda s: None)
        self.assertEqual(req.call_count, 1)


class TokenChannelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(fb, "token_file", lambda key: self.tmp / f"facebook_{key}.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def save(self, **t):
        (self.tmp / "facebook_wonder_minute.json").write_text(json.dumps(t), encoding="utf-8")

    def test_no_token(self):
        self.assertFalse(fb.token_status(cfg_with(), "wonder_minute")["ok"])

    def test_token_for_other_page_is_refused(self):
        self.save(channel="wonder_minute", page_id="999", page_name="Other", page_token="t")
        st = fb.token_status(cfg_with("111"), "wonder_minute")
        self.assertFalse(st["ok"])
        self.assertIn("not posting", st["problem"])

    def test_token_from_other_channel_is_refused(self):
        self.save(channel="summed_up", page_id="111", page_name="X", page_token="t")
        self.assertFalse(fb.token_status(cfg_with("111"), "wonder_minute")["ok"])

    def test_page_answering_with_other_id_is_refused(self):
        self.save(channel="wonder_minute", page_id="111", page_name="WM", page_token="t")
        with mock.patch.object(fb, "call", return_value={"id": "222", "name": "Other"}):
            self.assertFalse(fb.token_status(cfg_with("111"), "wonder_minute")["ok"])

    def test_matching_token_ok(self):
        self.save(channel="wonder_minute", page_id="111", page_name="WM", page_token="t")
        with mock.patch.object(fb, "call", return_value={"id": "111", "name": "WM"}):
            self.assertTrue(fb.token_status(cfg_with("111"), "wonder_minute")["ok"])

    def test_publish_refuses_mismatched_token_before_uploading(self):
        self.save(channel="wonder_minute", page_id="999", page_name="Other", page_token="t")
        with mock.patch.object(fb.requests, "request") as req:
            with self.assertRaises(RuntimeError):
                fb.publish_reel(cfg_with("111"), "wonder_minute", "video.mp4", "cap")
        req.assert_not_called()


class VideoCheckTest(unittest.TestCase):
    def probe(self, w, h, fps, dur):
        out = json.dumps({"streams": [{"width": w, "height": h, "r_frame_rate": f"{fps}/1"}],
                          "format": {"duration": str(dur)}})
        return mock.patch.object(fb.subprocess, "run", return_value=mock.Mock(stdout=out, stderr=""))

    def test_short_ok(self):
        with self.probe(1080, 1920, 30, 28):
            self.assertEqual(fb.video_problems("v.mp4"), [])

    def test_long_recap_rejected(self):
        with self.probe(1920, 1080, 30, 420):
            p = fb.video_problems("v.mp4")
        self.assertTrue(any("9:16" in x for x in p) and any("90 s" in x for x in p))


if __name__ == "__main__":
    unittest.main()
