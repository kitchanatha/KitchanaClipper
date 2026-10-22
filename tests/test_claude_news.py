import json
import tempfile
import unittest
from pathlib import Path

from pipeline import claude_queue as cq
from pipeline.newslong import _width_chars, split_number


class SplitNumberTest(unittest.TestCase):
    def test_last_number_goes_big(self):
        self.assertEqual(split_number("ดีเซล 30 สู่ 42.94"), ("ดีเซล 30 สู่", "42.94"))

    def test_number_keeps_its_unit(self):
        self.assertEqual(split_number("ประท้วงฝรั่งเศส เสียหาย 2 พันล้าน"), ("ประท้วงฝรั่งเศส เสียหาย", "2 พันล้าน"))
        self.assertEqual(split_number("ฝนถล่ม 40 จังหวัด"), ("ฝนถล่ม", "40 จังหวัด"))

    def test_no_number(self):
        self.assertEqual(split_number("ทองขึ้นอีก"), ("ทองขึ้นอีก", ""))

    def test_thai_marks_take_no_width(self):
        self.assertEqual(_width_chars("พันล้าน"), 5)  # พ น ล า น (ั and ้ sit above)


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.old = cq.QUEUE, cq.CARDS
        d = Path(tempfile.mkdtemp())
        cq.QUEUE, cq.CARDS = d / "q.json", d / "c.json"

    def tearDown(self):
        cq.QUEUE, cq.CARDS = self.old

    def test_add_status_and_cards(self):
        a = cq.add("NEWS-RESEARCH ราคาน้ำมัน", "Boom")
        cq.set_status(a["id"], "done", "facts card posted")
        self.assertIn("✅ #1", cq.queue_text())
        cq.card_later("facts:20261022-x")
        cq.card_later("C:/some/folder")
        self.assertEqual(cq.take_cards(), ["facts:20261022-x", "C:/some/folder"])
        self.assertEqual(cq.take_cards(), [])
        self.assertEqual(json.loads(cq.QUEUE.read_text(encoding="utf-8"))[0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
