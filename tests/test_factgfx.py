import unittest

from pipeline import factgfx as fx


def words_of(text, step=0.4):
    return [{"word": w, "start": i * step, "end": i * step + 0.35} for i, w in enumerate(text.split())]


SAHARA = words_of("The Sahara used to be green. Between roughly eleven thousand and five thousand years ago, "
                  "it had lakes. When the orbit shifted back, the rains retreated and the sand took over.")


class SpecTest(unittest.TestCase):
    def test_all_types_render(self):
        spec = [
            {"type": "timeline", "from": "Between roughly", "until": "end", "oldest": 12000,
             "marks": [["Between roughly", 11000], ["eleven thousand", 11000, "end"], ["years ago", 5000, "end"],
                       ["sand took over", 0, "end"]],
             "span": [11000, 5000]},
            {"type": "map", "from": "it had lakes", "until": "end", "view": [-20, 52, 0, 38],
             "region": [[-10, 20], [30, 20], [30, 30], [-10, 30]], "fade": ["the rains retreated", "sand took over"],
             "pin": [12, 23, "SAHARA"], "title": "NORTH AFRICA"},
            {"type": "stat", "from": "The Sahara", "until": "Between roughly", "big": "9M km2", "caption": "today"},
            {"type": "ratio", "from": "When the orbit", "lit": 3, "total": 4, "caption": "3 OUT OF 4"},
            {"type": "bars", "from": "it had lakes", "until": "end", "title": "T", "items": [["A", 1, "#FFFFFF"]]},
            {"type": "level", "from": "the rains retreated", "title": "SHRINKING", "note": "n"},
            {"type": "depth", "from": "Between roughly", "stops": [["Between roughly", 0], ["years ago", 1000, "end"]],
             "glow_from": "it had lakes"},
        ]
        events, skipped = fx.spec_events(spec, SAHARA, 12.0)
        self.assertEqual(skipped, [])
        self.assertTrue(all(e.startswith("Dialogue:") for e in events))
        self.assertTrue(any("SAHARA" in e for e in events))
        self.assertTrue(any("11,000 YEARS AGO" in e for e in events) and any("TODAY" in e for e in events))

    def test_missing_phrase_skips_only_that_graphic(self):
        spec = [{"type": "stat", "from": "not in the script", "big": "1"},
                {"type": "stat", "from": "it had lakes", "big": "2", "caption": "c"}]
        events, skipped = fx.spec_events(spec, SAHARA, 12.0)
        self.assertEqual(len(skipped), 1)
        self.assertTrue(events)

    def test_unknown_type_skipped(self):
        _, skipped = fx.spec_events([{"type": "hologram", "from": "it had lakes"}], SAHARA, 12.0)
        self.assertIn("unknown type", skipped[0])

    def test_land_outline_loads_and_projects(self):
        box = fx.MapBox(-20, 52, 0, 38, 90, 360, 900)
        paths = box.land_paths()
        self.assertTrue(paths)
        x, y = box.xy(-20, 38)
        self.assertEqual((x, y), (90, 360))

    def test_when_finds_phrase_start_and_end(self):
        self.assertAlmostEqual(fx.when(SAHARA, "sand took over"), SAHARA[-3]["start"])
        self.assertAlmostEqual(fx.when(SAHARA, "sand took over", end=True), SAHARA[-1]["end"])


if __name__ == "__main__":
    unittest.main()
