"""Tests for the news recap safety rules and layout.  Run:  python -m unittest tests.test_news"""
import copy
import shutil
import unittest

from pipeline import newslib as N

CREDIBLE = {"thairath.co.th": "Thairath", "thaipbs.or.th": "Thai PBS", "thaipbsworld.com": "Thai PBS",
            "bangkokpost.com": "Bangkok Post", "reuters.com": "Reuters"}
U = {
    "tr1": "https://www.thairath.co.th/news/local/1001",
    "tp1": "https://www.thaipbs.or.th/news/content/2001",
    "tpw": "https://www.thaipbsworld.com/story-3001/",
    "bp1": "https://www.bangkokpost.com/thailand/general/4001",
    "blog": "https://someblog.example.com/post/5",
    "fake": "https://www.bangkokpost.com/thailand/general/9999",   # never returned by the search
}
VERIFIED = [U["tr1"], U["tp1"], U["tpw"], U["bp1"], U["blog"]]


def research(**over):
    r = {
        "topic": "ข้อพิพาทค่าโดยสารรถไฟฟ้า",
        "safety_check": {"monarchy": False, "private_person": False, "minor_or_victim": False, "notes": ""},
        "sources": [{"outlet": "x", "url": u, "date": "2026-09-10", "title": "t"} for u in VERIFIED],
        "timeline": [
            {"date": "2026-09-12", "time_if_known": "", "event_th": "C", "source_urls": [U["tr1"], U["bp1"]]},
            {"date": "2026-09-01", "time_if_known": "14:00", "event_th": "A2", "source_urls": [U["tr1"], U["tp1"]]},
            {"date": "2026-09-01", "time_if_known": "09:30", "event_th": "A1", "source_urls": [U["tp1"], U["tpw"]]},
            {"date": "2026-09-05", "time_if_known": "", "event_th": "B", "source_urls": [U["tr1"], U["blog"]]},
            {"date": "2026-09-08", "time_if_known": "", "event_th": "D", "source_urls": [U["tr1"], U["fake"]]},
            {"date": "2026-09-09", "time_if_known": "", "event_th": "E", "source_urls": [U["tp1"], U["bp1"]]},
            {"date": "2026-09-10", "time_if_known": "", "event_th": "F", "source_urls": [U["bp1"], U["tr1"], U["tp1"]]},
        ],
        "sides": [{"party": "กระทรวง", "position_th": "ชี้แจงว่า...", "source_urls": [U["tr1"], U["tp1"]]}],
        "status_now_th": "รอการพิจารณา", "status_source_urls": [U["bp1"], U["tp1"]],
    }
    r.update(over)
    return r


class Monarchy(unittest.TestCase):
    BLOCK = [
        "ในหลวงเสด็จเปิดงาน", "ข่าวพระราชินีล่าสุด", "คดี ม.112 ของนักกิจกรรม", "มาตรา 112 แก้ไข",
        "ดราม่าเจ้าฟ้าหญิง", "Thai king visits", "royal family news", "lese-majeste case", "lèse-majesté",
        "Section 112 amendment", "Crown Prince ceremony", "สถาบันพระมหากษัตริย์กับการเมือง",
        "พระราชทานรางวัล", "ม . 112",
    ]
    ALLOW = [
        "พ.ร.บ.คอมพิวเตอร์ หรือพระราชบัญญัติว่าด้วยการกระทำความผิดเกี่ยวกับคอมพิวเตอร์",
        "Royal Thai Police arrest scam gang", "ประกาศในราชกิจจานุเบกษา", "Burger King opens in Bangkok",
        "ค่าโดยสารรถไฟฟ้า 20 บาท", "Kingdom of Thailand budget", "ราคาทองวันนี้ 112 บาท",
        "speaking at the Prince of Songkla University",
    ]

    def test_blocks(self):
        for t in self.BLOCK:
            self.assertTrue(N.monarchy_hits(t), t)

    def test_allows(self):
        for t in self.ALLOW:
            self.assertEqual(N.monarchy_hits(t), [], t)

    def test_source_mentioning_monarchy_blocks_the_story(self):
        r = research()
        r["sources"][0]["title"] = "เรื่องนี้เกี่ยวข้องกับราชสำนัก"
        self.assertTrue(any("monarchy" in p for p in N.safety_problems(r)))

    def test_quoted_search_text_blocks(self):
        self.assertTrue(N.safety_problems(research(), extra_texts=["...the palace said..."]))

    def test_claude_flag_blocks(self):
        r = research(safety_check={"monarchy": True, "notes": "linked to a royal event"})
        self.assertTrue(N.safety_problems(r))

    def test_clean_story_passes(self):
        self.assertEqual(N.safety_problems(research()), [])


class PrivatePeople(unittest.TestCase):
    def test_private_person_refused(self):
        r = research(safety_check={"private_person": True, "notes": "viral video of a shop owner"})
        self.assertTrue(any("private person" in p for p in N.safety_problems(r)))

    def test_minor_or_victim_refused(self):
        r = research(safety_check={"minor_or_victim": True, "notes": "student victim"})
        self.assertTrue(any("minor" in p for p in N.safety_problems(r)))


class Timeline(unittest.TestCase):
    def test_sort_by_date_then_time_undated_last(self):
        ev = [{"date": "2026-09-05"}, {"date": ""}, {"date": "2026-09-01", "time_if_known": "14:00"},
              {"date": "2026-09-01", "time_if_known": "9:30"}, {"date": "2026-08-31"}]
        out = [(e["date"], e.get("time_if_known", "")) for e in N.sort_timeline(ev)]
        self.assertEqual(out, [("2026-08-31", ""), ("2026-09-01", "9:30"), ("2026-09-01", "14:00"),
                               ("2026-09-05", ""), ("", "")])

    def test_confirmation_needs_two_outlets_from_the_search(self):
        r = N.apply_confirmation(research(), CREDIBLE, VERIFIED)
        by = {e["event_th"]: e for e in r["timeline"]}
        self.assertEqual([e["event_th"] for e in r["timeline"]], ["A1", "A2", "B", "D", "E", "F", "C"])
        self.assertTrue(by["A2"]["confirmed"])                  # Thairath + Thai PBS
        self.assertFalse(by["A1"]["confirmed"])                 # Thai PBS + Thai PBS World = one newsroom
        self.assertFalse(by["B"]["confirmed"])                  # blog is not credible
        self.assertFalse(by["D"]["confirmed"])                  # URL never returned by the search
        self.assertEqual(by["F"]["outlets"], ["Bangkok Post", "Thairath", "Thai PBS"])
        self.assertTrue(r["sides"][0]["confirmed"])
        self.assertTrue(r["status_confirmed"])

    def test_claude_confirmed_flag_is_ignored(self):
        r = research()
        r["timeline"][3]["confirmed"] = True                    # B: blog + Thairath
        r = N.apply_confirmation(r, CREDIBLE, VERIFIED)
        self.assertFalse(next(e for e in r["timeline"] if e["event_th"] == "B")["confirmed"])


class OnlyConfirmedInScript(unittest.TestCase):
    def setUp(self):
        self.r = N.apply_confirmation(research(), CREDIBLE, VERIFIED)
        self.ids = {e["event_th"]: e["id"] for e in self.r["timeline"]}
        self.script = {
            "title": "สรุปดราม่าค่ารถไฟฟ้า 12 ก.ย.", "hook": "เกิดอะไรขึ้นกับค่ารถไฟฟ้า?",
            "beats": [{"event_ids": [self.ids[x]], "card_text": "ข้อความสั้น", "narration": "..."}
                      for x in ("A2", "E", "F", "C")],
            "latest": {"card_text": "รอการพิจารณา", "narration": "..."},
            "closing": "ติดตามสรุปให้ฟัง เพื่อไม่พลาดเรื่องที่คนพูดถึง"}

    def test_confirmed_facts_only(self):
        f = N.confirmed_facts(self.r)
        self.assertEqual([e["event_th"] for e in f["events"]], ["A2", "E", "F", "C"])
        self.assertEqual(f["status_now_th"], "รอการพิจารณา")

    def test_valid_script_passes(self):
        self.assertEqual(N.script_problems(self.script, self.r, closing=self.script["closing"]), [])

    def test_script_using_unconfirmed_event_fails(self):
        s = copy.deepcopy(self.script)
        s["beats"][0]["event_ids"] = [self.ids["B"]]
        self.assertTrue(any("unconfirmed" in p for p in N.script_problems(s, self.r)))

    def test_other_script_rules(self):
        s = copy.deepcopy(self.script)
        s["beats"] = s["beats"][:3]
        s["title"] = "ดราม่าค่ารถ"
        s["beats"][0]["card_text"] = "ก" * 41
        s["hook"] = "ในหลวงเกี่ยวข้องไหม"
        p = " | ".join(N.script_problems(s, self.r, closing=self.script["closing"]))
        for needle in ("4-7 timeline beats", "สรุป", "over 40", "monarchy"):
            self.assertIn(needle, p)


class CardText(unittest.TestCase):
    def test_thai_wrap_two_lines_word_boundaries(self):
        from pythainlp.tokenize import word_tokenize
        text = "กระทรวงคมนาคมประกาศปรับค่าโดยสารรถไฟฟ้าทุกสาย"
        lines = N.wrap_card(text, 19, 2)
        self.assertLessEqual(len(lines), 2)
        self.assertEqual("".join(lines), text)
        self.assertLessEqual(N.card_width(lines[0]), 19)
        bounds, pos = set(), 0
        for w in word_tokenize(text, engine="newmm"):
            pos += len(w)
            bounds.add(pos)
        self.assertIn(len(lines[0]), bounds)

    def test_strip_leading_date(self):
        self.assertEqual(N.strip_date("26 ก.ย. กทม. ประกาศเขตภัยครบ 50 เขต"), "กทม. ประกาศเขตภัยครบ 50 เขต")
        self.assertEqual(N.strip_date("วันที่ 2 ต.ค. 69 เปิดลงทะเบียน"), "เปิดลงทะเบียน")
        self.assertEqual(N.strip_date("ท่วม 27 จังหวัด"), "ท่วม 27 จังหวัด")   # numbers inside stay

    def test_thai_captions_keep_script_spaces_and_punctuation(self):
        from pipeline.voice import _split_words, word_timings
        text = "ตอนนี้เป็นอย่างไร? ล่าสุด 6 ต.ค. ยังท่วม 27 จังหวัด"
        words = [w for w, _, _ in _split_words(text, "th")]
        self.assertFalse(any(w.startswith("?") for w in words), words)
        self.assertTrue(any(w.endswith("?") for w in words), words)
        al = {"characters": list(text), "character_start_times_seconds": [i * .05 for i in range(len(text))],
              "character_end_times_seconds": [i * .05 + .05 for i in range(len(text))]}
        wt = word_timings(text, al, "th")
        joined = "".join((" " if w["space_before"] else "") + w["word"] for w in wt)
        self.assertEqual(joined, text)

    def test_card_width_ignores_marks(self):
        self.assertEqual(N.card_width("ที่นี่"), 2)          # ท + น; the rest are marks
        self.assertEqual(N.card_width("กระทรวงคมนาคม"), 13)

    def test_thai_date(self):
        self.assertEqual(N.thai_date("2026-09-12"), "12 ก.ย.")
        self.assertEqual(N.thai_date("2026-01-03", with_year=True), "3 ม.ค. 69")

    def test_sources_block(self):
        r = N.apply_confirmation(research(), CREDIBLE, VERIFIED)
        block = N.sources_block(r)
        self.assertIn("Thairath", block)
        self.assertNotIn("someblog", block)
        self.assertIn("2569"[-2:], block)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
class Render(unittest.TestCase):
    """Builds a short sample with fake voice + footage into output/work/news-render-test/."""

    def test_render(self):
        import subprocess
        from pipeline.assemble import finish_video, make_background, run
        from pipeline.common import ROOT, load_config
        from pipeline.newsvideo import write_news_ass
        cfg = load_config()
        ch = cfg["channels"]["summed_up"]
        out = ROOT / "output" / "work" / "news-render-test"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        clip = out / "clip.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "mandelbrot=size=720x1280:rate=30",
             "-t", "8", "-pix_fmt", "yuv420p", str(clip)])
        voice = out / "voice.mp3"
        total = 26.0
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=200:duration={total}",
             "-af", "volume=0.1", str(voice)])
        text = "เกิดอะไรขึ้นกับค่ารถไฟฟ้า กระทรวงประกาศ ผู้ให้บริการชี้แจง ล่าสุด รอการพิจารณา"
        words = [{"word": w, "start": i * 1.2, "end": i * 1.2 + 1.0} for i, w in enumerate(text.split())]
        beats = [{"start": 3 + 4 * i, "date_label": d, "card_text": t, "outlets": o} for i, (d, t, o) in enumerate([
            ("1 ก.ย.", "กระทรวงประกาศปรับค่าโดยสารรถไฟฟ้า", ["Thairath", "Thai PBS"]),
            ("5 ก.ย.", "ผู้ให้บริการชี้แจงว่ายังไม่ได้รับหนังสือ", ["Bangkok Post", "Thairath"]),
            ("9 ก.ย.", "สภาผู้บริโภคยื่นหนังสือคัดค้าน", ["Thai PBS", "Bangkok Post"]),
            ("12 ก.ย.", "ครม. รับทราบ ให้ศึกษาเพิ่มเติม 30 วัน", ["Reuters", "Thai PBS"])])]
        latest = {"start": 19, "card_text": "อยู่ระหว่างการศึกษา คาดได้ข้อสรุปภายในเดือนตุลาคม",
                  "outlets": ["Thai PBS", "Thairath"]}
        temp = make_background(cfg, [str(clip)], total, out)
        write_news_ass(out / "news.ass", ch, cfg, "สรุปดราม่าค่ารถไฟฟ้า ใครพูดอะไรบ้าง", beats, latest,
                       words, total)
        finish_video(cfg, out, "news.ass", voice, total, out / "video.mp4", temp, darken=0.55)
        for t in (1, 4, 13, 18, 23):
            run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(out / "video.mp4"),
                 "-frames:v", "1", "-vf", "scale=360:-2", str(out / f"frame_{t:02d}.png")])
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(out / "video.mp4")],
                                   capture_output=True, text=True).stdout)
        self.assertAlmostEqual(dur, total, delta=0.3)


if __name__ == "__main__":
    unittest.main()
