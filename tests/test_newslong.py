"""Long recap: the same safety rules as the Short, plus its own structure checks."""
import copy
import unittest

from pipeline import newslib as N
from pipeline.newslong import long_parts, long_problems
from pipeline.youtube import _pair_line
from tests.test_news import CREDIBLE, VERIFIED, research

CLOSING = "กดติดตามสรุปให้ฟัง แล้วพบกันในเรื่องต่อไป"


class LongChecks(unittest.TestCase):
    def setUp(self):
        self.r = N.apply_confirmation(research(), CREDIBLE, VERIFIED)
        ids = [e["id"] for e in N.confirmed_facts(self.r)["events"]]
        self.script = {
            "title": "สรุปดราม่าค่ารถไฟฟ้า", "thumb_headline": "ค่ารถไฟฟ้า ใครจ่าย", "hook": "เกิดอะไรขึ้น?",
            "background": {"event_ids": ids[:1], "card_text": "เริ่มต้น", "narration": "เรื่องนี้เริ่มจาก..."},
            "beats": [{"event_ids": [i], "card_text": "ข้อความ", "narration": "..."} for i in ids],
            "sides": [{"party": "กระทรวง", "card_text": "ชี้แจง", "narration": "ชี้แจงว่า..."}],
            "next": {"card_text": "ยังไม่ประกาศ", "narration": "..."}, "closing": CLOSING}

    def test_valid_long_script_passes(self):
        self.assertEqual(long_problems(self.script, self.r, 1, 15, CLOSING), [])

    def test_structure_rules(self):
        s = copy.deepcopy(self.script)
        s["background"]["narration"] = "ย้อนไป..."
        s["beats"][0]["event_ids"] = ["e99"]
        s["thumb_headline"] = "หัวข้อยาวมากเกินไปสำหรับภาพปกที่ต้องสั้น"
        s["closing"] = "บาย"
        p = " | ".join(long_problems(s, self.r, 1, 15, CLOSING))
        for needle in ("เรื่องนี้เริ่มจาก", "unconfirmed", "thumb_headline", "closing"):
            self.assertIn(needle, p)

    def test_needs_enough_beats_and_no_monarchy(self):
        s = copy.deepcopy(self.script)
        s["hook"] = "ในหลวงเกี่ยวข้องไหม"
        p = " | ".join(long_problems(s, self.r, 8, 15, CLOSING))
        self.assertIn("8-15 timeline beats", p)
        self.assertIn("monarchy", p)

    def test_speaking_order(self):
        ids = [pid for pid, _, _ in long_parts(self.script)]
        self.assertEqual(ids[:2], ["hook", "bg"])
        self.assertEqual(ids[-3:], ["s0", "next", "closing"])


class Links(unittest.TestCase):
    def test_pair_lines(self):
        self.assertIn("คลิปยาว", _pair_line("long", "abc"))
        self.assertTrue(_pair_line("short", "x").startswith("▶ ดูฉบับสั้น"))
        self.assertTrue(_pair_line("full", "y").endswith("youtu.be/y"))


if __name__ == "__main__":
    unittest.main()
