"""Daily topic finder: the hard filter has no override."""
import unittest
from datetime import date, timedelta

from pipeline.topics import filter_topics, is_repeat

CREDIBLE = {"thairath.co.th": "Thairath", "khaosod.co.th": "Khaosod", "thaipbs.or.th": "Thai PBS",
            "thaipbsworld.com": "Thai PBS"}
TODAY = date.today().isoformat()
OLD = (date.today() - timedelta(days=9)).isoformat()


def topic(title="ราคาน้ำมันขึ้น", en="fuel price rise", urls=("https://www.thairath.co.th/a",
                                                               "https://www.khaosod.co.th/b"),
          ad="high", day=TODAY, **safety):
    return {"title_th": title, "topic_en": en, "summary_th": "สรุป", "why_trending": "x",
            "sources": [{"outlet": "?", "url": u, "date": day} for u in urls],
            "safety": {"monarchy": False, "private_person": False, "minors": False, "crime_victims": False,
                       **safety},
            "ad_friendliness": {"level": ad, "reason": "r"}}


VERIFIED = ["https://www.thairath.co.th/a", "https://www.khaosod.co.th/b", "https://www.thaipbs.or.th/c",
            "https://www.thaipbsworld.com/d"]


class HardFilter(unittest.TestCase):
    def run_one(self, t, covered=()):
        kept, dropped = filter_topics([t], CREDIBLE, VERIFIED, list(covered))
        return kept, dict(dropped)

    def test_clean_topic_kept(self):
        kept, _ = self.run_one(topic())
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["outlets"], ["Khaosod", "Thairath"])

    def test_monarchy_flag_or_words_drop(self):
        self.assertFalse(self.run_one(topic(monarchy=True))[0])
        self.assertFalse(self.run_one(topic(title="โครงการในพระราชดำริ ใหม่"))[0])

    def test_private_minor_victim_drop(self):
        for k in ("private_person", "minors", "crime_victims"):
            kept, dropped = self.run_one(topic(**{k: True}))
            self.assertFalse(kept, k)

    def test_two_distinct_credible_verified_outlets_needed(self):
        same_newsroom = topic(urls=("https://www.thaipbs.or.th/c", "https://www.thaipbsworld.com/d"))
        self.assertFalse(self.run_one(same_newsroom)[0])            # Thai PBS counts once
        not_searched = topic(urls=("https://www.thairath.co.th/a", "https://www.khaosod.co.th/zzz"))
        self.assertFalse(self.run_one(not_searched)[0])             # URL the search never returned
        blog = topic(urls=("https://www.thairath.co.th/a", "https://someblog.com/x"))
        self.assertFalse(self.run_one(blog)[0])

    def test_repeat_dropped_unless_update(self):
        cov = [{"topic_en": "fuel price rise", "date": TODAY}]
        self.assertFalse(self.run_one(topic(), cov)[0])
        upd = {**topic(), "update_of": "fuel price rise", "update_what": "second rise this month"}
        kept, _ = self.run_one(upd, cov)
        self.assertEqual(kept[0]["label"], "อัปเดต")
        self.assertTrue(is_repeat({"topic_en": "Fuel price rises again"}, cov))

    def test_fresh_then_ad_friendly_first_top3(self):
        cands = [topic(title="old", en="a old", day=OLD, ad="high"),
                 topic(title="low", en="b low", ad="low"),
                 topic(title="high", en="c high", ad="high"),
                 topic(title="med", en="d med", ad="medium")]
        kept, dropped = filter_topics(cands, CREDIBLE, VERIFIED, [])
        self.assertEqual([t["title_th"] for t in kept], ["high", "med", "low"])
        self.assertIn(("old", "not in the top 3"), dropped)


if __name__ == "__main__":
    unittest.main()
