import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import crosspost as xp


def chan(**platforms):
    return {"name": "Wonder Minute", "hashtags": ["#facts"], "crosspost": platforms}


class ValidateTest(unittest.TestCase):
    def test_current_style_config_ok(self):
        cfg = {"channels": {
            "wonder_minute": chan(facebook={"enabled": True, "page_id": "111", "mode": "publish"},
                                  tiktok={"enabled": True, "mode": "draft", "display_name": "Wonder Minute"}),
            "summed_up": chan(facebook={"enabled": True, "page_id": "222", "mode": "schedule"}),
            "mindset": {"name": "Mindset"}}}
        self.assertEqual(xp.validate_crosspost(cfg), [])

    def test_same_page_for_two_channels(self):
        cfg = {"channels": {"a": chan(facebook={"enabled": True, "page_id": "111"}),
                            "b": chan(facebook={"enabled": True, "page_id": "111"})}}
        self.assertTrue(any("also used by a" in p for p in xp.validate_crosspost(cfg)))

    def test_same_tiktok_for_two_channels(self):
        cfg = {"channels": {"a": chan(tiktok={"enabled": True, "display_name": "X"}),
                            "b": chan(tiktok={"enabled": True, "display_name": "X"})}}
        self.assertTrue(any("also used by a" in p for p in xp.validate_crosspost(cfg)))

    def test_bad_values(self):
        cfg = {"channels": {"a": chan(facebook={"enabled": True, "page_id": "", "mode": "now"},
                                      tiktok={"enabled": True, "mode": "direct"},
                                      instagram={"enabled": True}, youtube_shorts={}),
                            "b": chan(tiktok={"enabled": True, "mode": "draft"})}}
        probs = " | ".join(xp.validate_crosspost(cfg))
        for bit in ("page_id is empty", "facebook.mode", "tiktok.mode", "without display_name",
                    "ig_id is empty", "unknown crosspost platform 'youtube_shorts'"):
            self.assertIn(bit, probs)


class PostPlatformsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "meta.json").write_text(json.dumps({"title": "Otters", "hashtags": ["#otters"]}),
                                               encoding="utf-8")
        self.cfg = {"channels": {"wonder_minute": chan(
            facebook={"enabled": True, "page_id": "111"}, tiktok={"enabled": True, "display_name": "WM"})}}
        self.csv = mock.patch.object(xp, "_log_csv")
        self.csv.start()

    def tearDown(self):
        self.csv.stop()

    def meta(self):
        return json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))

    def run_with(self, fb, tt, **kw):
        with mock.patch.dict(xp.POSTERS, {"facebook": fb, "tiktok": tt}):
            return xp.post_platforms(self.cfg, "wonder_minute", self.folder, log=lambda *a: None, **kw)

    def test_failure_recorded_and_retry_fixes_only_that_line(self):
        fb = mock.Mock(return_value={"platform": "facebook", "ok": True, "url": "https://fb/reel/1"})
        bad_tt = mock.Mock(side_effect=RuntimeError("TikTok 503 internal"))
        self.run_with(fb, bad_tt)
        m = self.meta()
        self.assertEqual(xp.failed_platforms(m), ["tiktok"])
        self.assertIn("❌", " ".join(m["crosspost"]))
        good_tt = mock.Mock(return_value={"platform": "tiktok", "ok": True, "url": "tiktok-draft:9",
                                          "draft": True, "note": "sent", "caption": "cap"})
        self.run_with(fb, good_tt, only=["tiktok"], force=True)
        m = self.meta()
        self.assertEqual(xp.failed_platforms(m), [])
        self.assertEqual(fb.call_count, 1)  # the retry did not post Facebook again
        lines = m["crosspost"]
        self.assertTrue(lines[0].startswith("📘 Facebook ✅") and lines[1].startswith("🎵 TikTok 📥"))

    def test_already_posted_is_not_posted_twice(self):
        fb = mock.Mock(return_value={"platform": "facebook", "ok": True, "url": "https://fb/reel/1"})
        tt = mock.Mock(return_value={"platform": "tiktok", "ok": True, "url": "tiktok-draft:9", "draft": True})
        self.run_with(fb, tt)
        self.run_with(fb, tt)
        self.assertEqual((fb.call_count, tt.call_count), (1, 1))

    def test_skipped_spec_problem_gets_no_retry_button(self):
        fb = mock.Mock(return_value={"platform": "facebook", "ok": False, "skipped": True,
                                     "error": "not posted - 427 s long"})
        tt = mock.Mock(return_value={"platform": "tiktok", "ok": True, "url": "tiktok-draft:9", "draft": True})
        self.run_with(fb, tt)
        self.assertEqual(xp.failed_platforms(self.meta()), [])

    def test_studio_mode_needs_no_login_and_sends_nothing(self):
        self.cfg["channels"]["wonder_minute"]["crosspost"]["tiktok"] = {"enabled": True, "mode": "studio"}
        self.assertEqual(xp.validate_crosspost(self.cfg), [])
        with mock.patch("pipeline.tiktok.upload_draft") as up:
            r = xp._post_tiktok(self.cfg, "wonder_minute", self.folder, {"tiktok": "Otters #facts"}, None)
        up.assert_not_called()
        self.assertTrue(r["ok"] and "tiktokstudio/upload" in r["note"] and r["caption"].endswith("AI voice"))

    def test_switched_off_platform_is_never_called(self):
        self.cfg["channels"]["wonder_minute"]["crosspost"]["tiktok"]["enabled"] = False
        fb = mock.Mock(return_value={"platform": "facebook", "ok": True, "url": "u"})
        tt = mock.Mock()
        self.run_with(fb, tt)
        tt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
