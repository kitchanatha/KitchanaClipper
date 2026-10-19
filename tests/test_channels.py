"""Channel focus (enabled / daily_count / publish slots) and hook picking."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from pipeline import channels, youtube
from pipeline.assemble import hook_layout
from pipeline.script import choose_hook


def cfg(**ch):
    return {"daily_count": 1,
            "youtube": {"publish_times": ["12:00", "18:00", "20:30"], "min_gap_hours": 6,
                        "utc_offset": "+07:00"},
            "channels": {"a": {"name": "A", **ch}}}


class Switches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = channels.STATE
        channels.STATE = Path(self.tmp.name) / "channel_state.json"

    def tearDown(self):
        channels.STATE = self.old
        self.tmp.cleanup()

    def test_defaults_and_daily_count(self):
        self.assertTrue(channels.enabled(cfg(), "a"))
        self.assertEqual(channels.daily_count(cfg(daily_count=2), "a"), 2)
        self.assertEqual(channels.daily_count(cfg(), "a"), 1)  # global default

    def test_disabled_makes_none_and_override_wins(self):
        c = cfg(enabled=False, daily_count=3)
        self.assertEqual(channels.daily_count(c, "a"), 0)
        channels.set_enabled("a", True)  # /channel-enable
        self.assertTrue(channels.enabled(c, "a"))
        self.assertEqual(channels.daily_count(c, "a"), 3)
        channels.set_enabled("a", False)
        self.assertFalse(channels.enabled(cfg(), "a"))


class Slots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = youtube.SCHEDULE
        youtube.SCHEDULE = Path(self.tmp.name) / "schedule.json"

    def tearDown(self):
        youtube.SCHEDULE = self.old
        self.tmp.cleanup()

    def test_same_channel_at_least_6_hours_apart(self):
        c = cfg()
        slots = [youtube.next_slot(c, "a") for _ in range(6)]
        for x, y in zip(slots, slots[1:]):
            self.assertGreaterEqual(y - x, timedelta(hours=6))
        self.assertEqual(len(set(slots)), 6)

    def test_channel_slots_override_global(self):
        c = cfg(publish_times=["20:30"])
        s = youtube.next_slot(c, "a")
        self.assertEqual((s.hour, s.minute), (20, 30))


class Hooks(unittest.TestCase):
    def test_best_score_under_10_words_wins(self):
        opts = ["My neighbor left me a key and a warning.",
                "This is a very long hook that goes on and on well past ten words today.",
                "The note said: don't open the closet."]
        scores = [{"curiosity": 7, "clarity": 8}, {"curiosity": 10, "clarity": 10},
                  {"curiosity": 9, "clarity": 8}]
        self.assertEqual(choose_hook(opts, scores), 2)  # option 2 scores higher but is too long

    def test_claude_pick_used_unless_too_long(self):
        opts = ["short one here", "this one is far too long to be a two second hook line ok", "also short"]
        sc = [{"curiosity": 9, "clarity": 9}, {"curiosity": 1, "clarity": 1}, {"curiosity": 5, "clarity": 5}]
        self.assertEqual(choose_hook(opts, sc, best=3), 2)   # Claude's pick, fits
        self.assertEqual(choose_hook(opts, sc, best=2), 0)   # Claude's pick too long -> best score

    def test_missing_scores_still_picks(self):
        self.assertIn(choose_hook(["a b c", "d e"], []), (0, 1))

    def test_long_hook_fits_on_screen(self):
        lines, scale = hook_layout("MY NEIGHBOR LEFT ME A KEY AND A WARNING.")
        self.assertLessEqual(len(lines), 4)
        self.assertLess(scale, 1.0)
        self.assertEqual(" ".join(lines), "MY NEIGHBOR LEFT ME A KEY AND A WARNING.")
        short, big = hook_layout("YOUR EYE BREATHES AIR?")
        self.assertEqual(big, 1.0)


if __name__ == "__main__":
    unittest.main()
