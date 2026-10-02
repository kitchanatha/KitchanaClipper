"""Render test: builds a short long-form video, SRT and thumbnail from fake footage/voice/music,
in English, Thai and Chinese. Needs FFmpeg + fonts (python run.py fonts); no API keys.
Keeps its output in output/work/render-test/ so you can look at it.
Run:  python -m unittest tests.test_render"""
import copy
import json
import random
import shutil
import subprocess
import unittest
from pathlib import Path

from pipeline import longvideo as lv
from pipeline.assemble import duration, run
from pipeline.common import ROOT, load_config
from pipeline.storylib import build_cues, to_srt

OUT = ROOT / "output" / "work" / "render-test"
TEXT = {
    "en": "Without, the night was cold and wet, but in the small parlour of Laburnum Villa the "
          "blinds were drawn and the fire burned brightly. Father and son were at chess. "
          "\"Hark at the wind,\" said Mr. White.",
    "th": "ข้างนอกคืนนั้นหนาวและเปียกชื้น แต่ในห้องนั่งเล่นเล็ก ๆ ของบ้านลาเบอร์นัม ม่านถูกปิดสนิท "
          "และไฟในเตาผิงลุกโชน พ่อกับลูกชายกำลังเล่นหมากรุกกันอยู่",
    "zh": "屋外，夜晚寒冷而潮湿；可是在拉伯纳姆别墅的小客厅里，窗帘已经拉上，炉火烧得正旺。"
          "父子俩正在下棋。“听听这风声，”怀特先生说。",
}
TITLE = {"en": "The Monkey's Paw", "th": "อุ้งเท้าลิง", "zh": "猴爪"}
AUTHOR = {"en": "W. W. Jacobs", "th": "ดับเบิลยู. ดับเบิลยู. เจคอบส์", "zh": "W.W.雅各布斯"}


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(OUT, ignore_errors=True)
        OUT.mkdir(parents=True)
        cls.clips = []
        for i, src in enumerate(["testsrc2=size=1920x1080:rate=30", "mandelbrot=size=1280x720:rate=30",
                                 "cellauto=size=1280x720:rate=30"]):
            c = OUT / f"clip{i}.mp4"
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-t", "12",
                 "-pix_fmt", "yuv420p", str(c)])
            cls.clips.append((str(c), duration(c)))
        cls.music = OUT / "music.mp3"
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=9",
             "-af", "volume=0.6", str(cls.music)])

    def build(self, lang):
        cfg = copy.deepcopy(load_config())
        cfg["story"]["segment_seconds"] = 14         # force several render windows
        key = cfg["story"]["channels"][lang]
        ch = cfg["channels"][key]
        work = OUT / lang
        work.mkdir()
        text = TEXT[lang]
        speech = 22.0
        voice = work / "voice.wav"                   # fake narration: quiet tone + intro/outro gaps
        intro, outro = (2.0, 6.0), (speech + 7.0, speech + 13.0)
        total = outro[1]
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"sine=frequency=180:duration={total}", "-ac", "1", "-ar", "44100",
             "-af", "volume=0.2", str(voice)])
        offset = intro[1] + 0.5
        times = [(offset + i * speech / len(text), offset + (i + 1) * speech / len(text))
                 for i in range(len(text))]
        cues = build_cues(text, times, lang, lv.SUB_STYLE[lang][1])
        (work / "subtitles.srt").write_text(to_srt(cues), encoding="utf-8")
        cards = lv.intro_outro_cards(ch, lang, TITLE[lang], AUTHOR[lang], intro, outro)
        lv.write_ass(work / "story.ass", ch, lang, cues, cards)
        shots = lv.plan_shots(total, [0.0, 15.0], [self.clips[:2], self.clips[2:]],
                              float(cfg["story"]["clip_seconds"]), float(cfg["story"]["crossfade_seconds"]),
                              random.Random(0))
        out = work / "video.mp4"
        lv.render(cfg, ch, shots, work / "story.ass", voice, total, work, out, music=self.music,
                  log=lambda *_: None)
        lv.thumbnail(cfg, ch, lang, self.clips[0][0], 3, TITLE[lang], AUTHOR[lang], work,
                     work / "thumbnail.jpg")
        for t in (4, 12, total - 3):                 # frames to look at
            run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                 "-vf", "scale=960:-2", str(work / f"frame_{int(t):02d}.png")])
        return out, total, work

    def check(self, lang):
        out, total, work = self.build(lang)
        info = probe(out)
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (1920, 1080))
        self.assertIn("audio", kinds)
        self.assertAlmostEqual(float(info["format"]["duration"]), total, delta=0.25)
        th = probe(work / "thumbnail.jpg")["streams"][0]
        self.assertEqual((th["width"], th["height"]), (1280, 720))
        self.assertTrue((work / "subtitles.srt").read_text(encoding="utf-8").startswith("1\n"))

    def test_en(self):
        self.check("en")

    def test_th(self):
        self.check("th")

    def test_zh(self):
        self.check("zh")


if __name__ == "__main__":
    unittest.main()
