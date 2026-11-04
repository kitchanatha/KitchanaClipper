import copy
import unittest

from pipeline import links as L
from pipeline.common import load_config


def cfg_with(**links):
    cfg = copy.deepcopy(load_config())
    cfg["channels"]["summed_up"]["links"] = links
    return cfg


class FakeResp:
    def __init__(self, code):
        self.status_code = code

    def close(self):
        pass


class LinksTests(unittest.TestCase):
    def test_config_links_valid(self):
        self.assertEqual(L.validate_links(load_config()), [])

    def test_empty_links_left_out(self):
        cfg = cfg_with(youtube="", facebook="https://www.facebook.com/x", tiktok="")
        self.assertEqual(L.channel_links(cfg, "summed_up"), {"facebook": "https://www.facebook.com/x"})
        block = L.follow_block(cfg, "summed_up")
        self.assertTrue(block.startswith("ติดตามเราได้ที่:"))
        self.assertNotIn("YouTube", block)

    def test_no_links_no_block(self):
        cfg = cfg_with(youtube="", facebook="", tiktok="", sister_channels=[])
        self.assertEqual(L.follow_block(cfg, "summed_up"), "")
        meta = {"kind": "news", "variant": "long"}
        self.assertEqual(L.with_links(cfg, "summed_up", meta, "desc"), "desc")

    def test_long_gets_block_last_with_sub_confirmation(self):
        cfg = cfg_with(youtube="https://www.youtube.com/@a", tiktok="https://www.tiktok.com/@a",
                       sister_channels=[{"name": "Wonder Minute", "url": "https://www.youtube.com/@w"}])
        desc = "story\n\nช่วงเวลาในคลิป:\n0:00 บทนำ\n\nแหล่งข่าว:\nhttps://x"
        out = L.with_links(cfg, "summed_up", {"kind": "news", "variant": "long"}, desc)
        self.assertTrue(out.startswith(desc))
        self.assertIn("https://www.youtube.com/@a?sub_confirmation=1", out)
        self.assertTrue(out.rstrip().endswith("Wonder Minute: https://www.youtube.com/@w"))
        self.assertEqual(L.with_links(cfg, "summed_up", {"kind": "news", "variant": "long"}, out), out)  # once

    def test_shorts_plain_text_only(self):
        cfg = cfg_with(youtube="https://www.youtube.com/@a")
        out = L.with_links(cfg, "summed_up", {"kind": "news", "variant": "short"}, "d")
        self.assertEqual(out, "d\n\nดูฉบับเต็มบนช่อง สรุปให้ฟัง")
        self.assertNotIn("http", out)

    def test_shorts_line_override(self):
        self.assertIn("More amazing facts", L.shorts_line(load_config(), "wonder_minute"))

    def test_back_to_youtube(self):
        cfg = cfg_with(youtube="https://www.youtube.com/@a")
        fb = L.back_to_youtube(cfg, "summed_up", {}, True, "https://youtu.be/L", 600)
        self.assertEqual(fb, "ฉบับเต็ม 10 นาทีบน YouTube: สรุปให้ฟัง\nhttps://youtu.be/L")
        self.assertEqual(L.back_to_youtube(cfg, "summed_up", {}, False), "ฉบับเต็มบน YouTube: สรุปให้ฟัง")
        self.assertTrue(L.back_to_youtube(cfg, "summed_up", {}, True).endswith("https://www.youtube.com/@a"))

    def test_pinned_comment(self):
        cfg = cfg_with(youtube="https://www.youtube.com/@a", facebook="https://www.facebook.com/x")
        pin = L.pinned_comment(cfg, "summed_up", {})
        self.assertIn("?", pin.split("\n")[0])
        self.assertIn("Facebook: https://www.facebook.com/x", pin)
        self.assertNotIn("youtube.com", pin)

    def test_validate_bad_urls(self):
        cfg = cfg_with(youtube="www.youtube.com/@a", sister_channels=[{"name": "x"}], affiliate="https://a")
        probs = " | ".join(L.validate_links(cfg))
        self.assertIn("links.youtube must start with https://", probs)
        self.assertIn("sister_channels", probs)
        self.assertIn("unknown links entry 'affiliate'", probs)

    def test_link_report(self):
        cfg = cfg_with(youtube="https://www.youtube.com/@a", facebook="https://www.facebook.com/dead")
        fake = lambda url, **kw: FakeResp(200 if "youtube" in url else 404)  # noqa: E731
        lines = L.link_report(cfg, "summed_up", get=fake)
        self.assertTrue(lines[0].startswith("✅ YouTube"))
        self.assertTrue(lines[1].startswith("❌ Facebook") and "404" in lines[1])
        self.assertIn("not set: TikTok", lines[-1])


class CaptionBacklinkTests(unittest.TestCase):
    def test_captions_point_back(self):
        from pipeline.crosspost import captions
        cfg = copy.deepcopy(load_config())
        cfg["channels"]["wonder_minute"]["links"]["youtube"] = "https://www.youtube.com/@wm"
        meta = {"title": "Why otters hold hands", "description": "Cute.", "video_id": "abc"}
        c = captions(cfg, "wonder_minute", meta)
        self.assertTrue(c["facebook"].endswith("Full video on YouTube: Wonder Minute\nhttps://www.youtube.com/@wm"))
        self.assertTrue(c["tiktok"].endswith("Full video on YouTube: Wonder Minute"))
        self.assertNotIn("http", c["tiktok"])
        self.assertNotIn("http", c["instagram"])


if __name__ == "__main__":
    unittest.main()
