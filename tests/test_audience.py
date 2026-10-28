import unittest
from datetime import date

from pipeline import audience as au


class FakeYA:
    """Answers reports().query(**kw).execute() like YouTube Analytics, by dimensions/window."""

    def __init__(self):
        self.calls = []

    def reports(self):
        return self

    def query(self, **kw):
        self.calls.append(kw)
        self._kw = kw
        return self

    def execute(self):
        kw = self._kw
        dims = kw.get("dimensions")
        before = kw["endDate"] < "2026-10-01"
        if not dims:
            return {"rows": [[3000 if before else 7365, 600 if before else 1656, 2, 0, 41.5]]}
        rows = {
            "country": [["US", 1164], ["TH", 670], ["PH", 501]],
            "ageGroup,gender": [["age25-34", "male", 22.0], ["age25-34", "female", 9.5],
                                ["age35-44", "male", 20.0], ["age35-44", "female", 9.7],
                                ["age13-17", "male", 7.2], ["age18-24", "female", 7.6]],
            "deviceType": [["MOBILE", 773], ["TABLET", 141], ["TV", 56], ["DESKTOP", 29]],
            "insightTrafficSourceType": [["SHORTS", 900], ["YT_SEARCH", 50], ["BROWSE", 30], ["EXT_URL", 20]],
            "subscribedStatus": [["UNSUBSCRIBED", 996], ["SUBSCRIBED", 4]],
        }[dims]
        return {"rows": rows}


CFG = {"channels": {"wonder_minute": {"name": "Wonder Minute", "youtube_token": "tokens/wonder_minute.json"}}}


class AudienceTest(unittest.TestCase):
    def test_overview(self):
        a = au.channel_audience(CFG, "wonder_minute", ya=FakeYA(), today=date(2026, 10, 29))
        self.assertEqual((a["start"], a["end"]), ("2026-09-30", "2026-10-27"))  # 28 days, 2-day lag
        self.assertEqual(a["now"]["views"], 7365)
        self.assertEqual(a["before"]["views"], 3000)
        self.assertEqual(a["countries"][0], ("US", 15.8))
        self.assertEqual(a["ages"][0][0], "25-34")
        self.assertEqual(a["devices"][0], ("Phone", 77.4))
        self.assertEqual(a["sources"][0], ("Shorts feed", 90.0))
        self.assertEqual(a["subscribed"]["UNSUBSCRIBED"], 99.6)
        text = au.card(a)
        for bit in ("7,365 views (⬆️146%)", "🇺🇸 US 15.8%", "Shorts feed 90.0%", "non-subscribers 99.6%"):
            self.assertIn(bit, text)

    def test_compact_card_is_short(self):
        a = au.channel_audience(CFG, "wonder_minute", ya=FakeYA(), today=date(2026, 10, 29))
        self.assertLessEqual(len(au.card(a, compact=True).splitlines()), 4)

    def test_missing_sign_in_explains_what_to_run(self):
        self.assertIn("auth-analytics --channel wonder_minute",
                      au.card({"key": "wonder_minute", "name": "Wonder Minute", "missing": True}))

    def test_flag(self):
        self.assertEqual(au.flag("TH"), "🇹🇭")
        self.assertEqual(au.flag("ZZZ"), "")


if __name__ == "__main__":
    unittest.main()
