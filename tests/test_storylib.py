"""Unit tests for story mode's pure logic.  Run:  python -m unittest discover tests"""
import random
import tempfile
import unittest
from pathlib import Path

from pipeline import storylib as L
from pipeline.longvideo import plan_shots, plan_windows, window_inputs

PD = {"title": "The Monkey's Paw", "author": "W. W. Jacobs", "year_published": 1902,
      "author_death_year": 1943, "source_url": "https://www.gutenberg.org/ebooks/12122",
      "rights": "public_domain"}


class Rights(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def ok(self, meta, year=2026):
        return L.validate_rights(meta, self.dir, year=year)

    def bad(self, meta, needle, year=2026):
        with self.assertRaises(L.RightsError) as e:
            L.validate_rights(meta, self.dir, year=year)
        self.assertIn(needle, str(e.exception))

    def test_public_domain_ok(self):
        self.assertTrue(self.ok(PD))

    def test_cutoff_is_1931_in_2026(self):
        self.assertEqual(L.us_public_domain_cutoff(2026), 1931)
        self.ok({**PD, "year_published": 1930})
        self.bad({**PD, "year_published": 1931}, "before 1931")

    def test_author_died_within_50_years(self):
        self.bad({**PD, "author_death_year": 1980}, "50 years")
        self.bad({**PD, "author_death_year": 1976}, "50 years")   # exactly 50 years: not yet
        self.ok({**PD, "author_death_year": 1975})

    def test_missing_rights_and_fields(self):
        self.bad({k: v for k, v in PD.items() if k != "rights"}, "'rights'")
        self.bad({**PD, "rights": "fair_use"}, "'rights'")
        self.bad({**PD, "author_death_year": None}, "author_death_year")
        self.bad({**PD, "source_url": ""}, "source_url")

    def test_permission_needs_proof_file(self):
        meta = {**PD, "rights": "permission", "year_published": 2020, "author_death_year": None,
                "permission_proof": "permission.png"}
        self.bad(meta, "not found")
        self.bad({**meta, "permission_proof": ""}, "permission_proof")
        Path(self.dir, "permission.png").write_bytes(b"x")
        self.assertTrue(self.ok(meta))

    def test_all_problems_listed(self):
        with self.assertRaises(L.RightsError) as e:
            L.validate_rights({**PD, "year_published": 1950, "author_death_year": 2000}, self.dir, 2026)
        self.assertIn("1950", str(e.exception))
        self.assertIn("2000", str(e.exception))


GB = """The Project Gutenberg eBook of The Lady of the Barge
This ebook is for the use of anyone anywhere in the United States.

*** START OF THE PROJECT GUTENBERG EBOOK THE LADY OF THE BARGE ***

Produced by Someone and the Online Distributed Proofreading Team

I.

Without, the night was cold and wet, but in the small
parlour of Laburnum Villa the blinds were drawn and the
fire burned brightly.

"Hark at the wind," said Mr. White, who, having seen a
fatal mistake after it was too late--was _amiably_ desirous.

*** END OF THE PROJECT GUTENBERG EBOOK THE LADY OF THE BARGE ***

Section 1. General Terms of Use and Redistributing Project Gutenberg-tm electronic works
"""


class Cleaning(unittest.TestCase):
    def test_gutenberg_stripped_and_unwrapped(self):
        t = L.clean_text(GB)
        self.assertNotIn("Gutenberg", t)
        self.assertNotIn("Produced by", t)
        self.assertNotIn("Section 1", t)
        paras = L.paragraphs(t)
        self.assertEqual(paras[0], "I.")
        self.assertTrue(L.is_heading(paras[0]))
        self.assertTrue(paras[1].startswith("Without, the night was cold and wet, but in the small parlour"))
        self.assertIn("too late—was amiably desirous", paras[2])
        self.assertNotIn("\n", paras[1])

    def test_headings(self):
        for h in ("I.", "II", "Chapter 3", "PART IV.", "一", "第三章", "ตอนที่ 2"):
            self.assertTrue(L.is_heading(h), h)
        self.assertFalse(L.is_heading("It was a dark night."))

    def test_spoken_heading(self):
        self.assertEqual(L.spoken_heading("III.", "en"), "Part three.")
        self.assertEqual(L.spoken_heading("II.", "th"), "ตอนที่ 2")
        self.assertEqual(L.spoken_heading("II.", "zh"), "第二部分。")
        self.assertEqual(L.spoken_heading("Hello.", "en"), "Hello.")


class Chunking(unittest.TestCase):
    def check(self, text, lang, max_chars):
        chunks = L.split_for_narration(text, lang, max_chars)
        for c in chunks:
            self.assertLessEqual(len(c), max_chars)
        # nothing lost, nothing reordered
        squash = lambda s: "".join(s.split())
        self.assertEqual(squash("".join(chunks)), squash(text))
        return chunks

    def test_groups_paragraphs(self):
        text = "\n\n".join(f"Paragraph {i} has some words in it." for i in range(20))
        chunks = self.check(text, "en", 120)
        self.assertTrue(all(c.startswith("Paragraph") and c.endswith(".") for c in chunks))

    def test_long_paragraph_splits_at_sentences(self):
        text = " ".join(f"Sentence number {i} ends here." for i in range(40))
        chunks = self.check(text, "en", 200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.endswith("here.") for c in chunks))

    def test_mr_is_not_a_sentence_end(self):
        spans = L.sentence_spans('"Hark at the wind," said Mr. White. He smiled.', "en")
        self.assertEqual(len(spans), 2)

    def test_chinese_and_thai(self):
        zh = "他走进房间。窗外下着雨！她问：“你听见了吗？”" * 30
        chunks = self.check(zh, "zh", 100)
        self.assertTrue(all(c[-1] in "。！？”" for c in chunks))
        th = "คืนนั้นอากาศหนาวและเปียกชื้น ในห้องนั่งเล่นเล็ก ๆ ของบ้าน ไฟในเตาผิงลุกโชน " * 20
        self.check(th.strip(), "th", 150)

    def test_translation_groups(self):
        text = "\n\n".join("word " * 400 for _ in range(10))
        groups = L.group_for_translation(text, 1500)
        self.assertEqual(sum(len(g) for g in groups), 10)
        self.assertTrue(all(sum(len(p.split()) for p in g) <= 1600 for g in groups))


class Timing(unittest.TestCase):
    def test_offsets(self):
        offs, total = L.chunk_offsets([10.0, 5.5, 3.0], [0.5, 0.6, 2.0])
        self.assertEqual(offs, [0.0, 10.5, 16.6])
        self.assertAlmostEqual(total, 21.6)

    def test_char_times_exact(self):
        al = {"characters": list("Hi you"), "character_start_times_seconds": [0, .1, .2, .3, .4, .5],
              "character_end_times_seconds": [.1, .2, .3, .4, .5, .6]}
        t = L.char_times("Hi you", al)
        self.assertEqual(t[3], (0.3, 0.4))

    def test_char_times_mismatch_maps_and_interpolates(self):
        # ElevenLabs normalised "—" to "-" and dropped a quote
        text = 'He said "no—never".'
        aligned = 'He said no-never.'
        n = len(aligned)
        al = {"characters": list(aligned),
              "character_start_times_seconds": [i * 0.1 for i in range(n)],
              "character_end_times_seconds": [i * 0.1 + 0.1 for i in range(n)]}
        t = L.char_times(text, al)
        self.assertEqual(len(t), len(text))
        starts = [s for s, _ in t]
        self.assertEqual(starts, sorted(starts))          # monotonic
        self.assertAlmostEqual(t[0][0], 0.0)
        self.assertAlmostEqual(t[-1][1], n * 0.1, places=5)


class Subtitles(unittest.TestCase):
    def test_srt(self):
        cues = [{"start": 0, "end": 1.5, "lines": ["Hello"]},
                {"start": 3661.25, "end": 3663.999, "lines": ["Two", "lines"]}]
        self.assertEqual(L.to_srt(cues),
                         "1\n00:00:00,000 --> 00:00:01,500\nHello\n\n"
                         "2\n01:01:01,250 --> 01:01:03,999\nTwo\nlines\n")

    def test_english_wrap(self):
        text = "The fire burned brightly while father and son played chess in the parlour."
        lines = L.wrap_text(text, "en", 30)
        self.assertTrue(all(len(l) <= 30 for l in lines))
        self.assertEqual(" ".join(lines), text)

    def test_thai_wrap_keeps_words_whole(self):
        from pythainlp.tokenize import word_tokenize
        text = "คืนนั้นอากาศหนาวและเปียกชื้นแต่ในห้องนั่งเล่นเล็กๆของบ้านลาเบอร์นัมม่านถูกปิดและไฟในเตาผิงลุกโชน"
        lines = L.wrap_text(text, "th", 20)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), text)
        self.assertTrue(all(L.display_width(l) <= 20 for l in lines))
        # every line boundary is a word boundary
        bounds, pos = set(), 0
        for w in word_tokenize(text, engine="newmm"):
            pos += len(w)
            bounds.add(pos)
        pos = 0
        for l in lines[:-1]:
            pos += len(l)
            self.assertIn(pos, bounds)

    def test_thai_repetition_mark_never_starts_a_line(self):
        text = "ในห้องนั่งเล่นเล็ก ๆ ของบ้านลาเบอร์นัม ม่านถูกปิดสนิท"
        for width in range(8, 30):
            for line in L.wrap_text(text, "th", width):
                self.assertFalse(line.startswith("ๆ"), (width, line))

    def test_thai_width_ignores_marks(self):
        self.assertEqual(L.display_width("ที่"), 1)   # base + vowel above + tone mark
        self.assertEqual(L.display_width("น้ำ"), 2)

    def test_chinese_wrap(self):
        text = "老怀特先生把猴爪扔进了火里，他的妻子尖叫着抢了出来。可是已经太迟了，那个愿望已经说出口。"
        lines = L.wrap_text(text, "zh", 14)
        self.assertEqual("".join(lines), text)
        self.assertTrue(all(L.display_width(l) <= 15 for l in lines))
        for l in lines[1:]:
            self.assertNotIn(l[0], "，。！？、；：”")

    def test_build_cues(self):
        text = "One short sentence. " + "This one is much longer and has to be wrapped onto more than two lines of text for sure."
        times = [(i * 0.05, i * 0.05 + 0.05) for i in range(len(text))]
        cues = L.build_cues(text, times, "en", 30, max_lines=2)
        self.assertEqual(cues[0]["lines"], ["One short sentence."])
        self.assertTrue(all(len(c["lines"]) <= 2 for c in cues))
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_ass_color(self):
        self.assertEqual(L.ass_color("#F2A541"), "&H0041A5F2")


class Descriptions(unittest.TestCase):
    def test_chapters(self):
        txt = L.chapters_text([(5, "Intro"), (75, "The Visitor"), (80, "too close"), (3700, "End")])
        self.assertEqual(txt, "0:00 Intro\n1:15 The Visitor\n1:01:40 End")
        self.assertEqual(L.chapters_text([(0, "a"), (60, "b")]), "")  # YouTube needs 3+

    def test_credits(self):
        en = L.credits_text(PD, "en")
        for s in ("The Monkey's Paw", "W. W. Jacobs", "1902", "Public domain", PD["source_url"]):
            self.assertIn(s, en)
        self.assertNotIn("Translated", en)
        self.assertIn("Translated by Candlelit Tales", L.credits_text(PD, "th"))
        perm = L.credits_text({**PD, "rights": "permission", "permission_link": "https://x.y"}, "zh")
        self.assertIn("Narrated with the author's permission: https://x.y", perm)


class RenderPlan(unittest.TestCase):
    def test_shots_and_windows(self):
        clips = [[("a.mp4", 20.0), ("b.mp4", 9.0)], [("c.mp4", 30.0)]]
        total = 400.0
        shots = plan_shots(total, [0.0, 200.0], clips, 8.0, 2.5, random.Random(0))
        self.assertGreaterEqual(shots[-1]["start"] + 8.0, total)
        self.assertTrue(all(s["src"] == "c.mp4" for s in shots if s["start"] + 4 > 210))
        wins = plan_windows(shots, total, 150)
        self.assertEqual(wins[0][0], 0)
        self.assertEqual(wins[-1][1], int(total * 30))
        for (a, b), (c, d) in zip(wins, wins[1:]):
            self.assertEqual(b, c)                       # contiguous
            t = b / 30
            for s in shots:                              # never inside a crossfade
                self.assertFalse(s["start"] < t < s["start"] + 2.5 and s is not shots[0])
        for k, (a, b) in enumerate(wins):
            ins = window_inputs(shots, a / 30, b / 30, last=(k == len(wins) - 1))
            self.assertAlmostEqual(ins[0][3], 0.0)
            for (_, _, ln, off), (_, _, _, off2) in zip(ins, ins[1:]):
                self.assertAlmostEqual(off + ln - 2.5, off2, places=3)


if __name__ == "__main__":
    unittest.main()
