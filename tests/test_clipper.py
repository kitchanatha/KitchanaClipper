import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline import clipper as C
from pipeline.common import load_config

CREATOR = {"name": "TestStreamer", "platform": "twitch", "channel": "teststreamer",
           "url": "https://www.twitch.tv/teststreamer",
           "permission": {"date": "2026-11-04", "how": "test", "proof": ""}}


def cfg_with(creators):
    cfg = copy.deepcopy(load_config())
    cfg["channels"]["aion2_clips"]["creators"] = creators
    return cfg


class RegistryTests(unittest.TestCase):
    def test_config_valid(self):
        self.assertEqual(C.validate_creators(load_config()), [])
        self.assertIn("aion2_clips", C.clipper_keys(load_config()))

    def test_missing_permission_is_a_problem(self):
        bad = dict(CREATOR, permission={})
        probs = " | ".join(C.validate_creators(cfg_with([bad])))
        self.assertIn("permission needs date and how", probs)

    def test_match_by_url_and_metadata(self):
        ch = cfg_with([CREATOR])["channels"]["aion2_clips"]
        self.assertIs(C.match_creator(ch, {}, "https://www.twitch.tv/teststreamer/clip/AbcDef"), CREATOR)
        self.assertIs(C.match_creator(ch, {"channel": "TestStreamer"}, "https://clips.twitch.tv/AbcDef"), CREATOR)

    def test_unlisted_creator_refused(self):
        ch = cfg_with([CREATOR])["channels"]["aion2_clips"]
        self.assertIsNone(C.match_creator(ch, {"channel": "someoneelse"}, "https://www.twitch.tv/someoneelse/clip/X"))
        # same handle on another platform is not the same permission
        self.assertIsNone(C.match_creator(ch, {"channel": "teststreamer"}, "https://www.youtube.com/watch?v=x"))
        with self.assertRaises(C.ClipError):
            C.add(cfg_with([CREATOR]), "aion2_clips", "http://insecure.example/clip")

    def test_chapters_rules(self):
        self.assertEqual(C._chapters([(0, 20, "a"), (20, 20, "b")]), [])
        self.assertEqual(C._chapters([(0, 20, "a"), (19.5, 15, "b"), (34, 30, "c")]),
                         ["0:00 a", "0:19 b", "0:34 c"])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not on PATH")
class BuildTests(unittest.TestCase):
    def test_local_clip_short_and_compilation(self):
        tmp = Path(tempfile.mkdtemp())
        orig = C.clips_dir
        try:
            C.clips_dir = lambda key: tmp / "clips" / key
            cfg = cfg_with([CREATOR])
            cfg["output_dir"] = str(tmp / "out")
            src = tmp / "fight.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=12",
                            "-f", "lavfi", "-i", "sine=frequency=330:d=12", "-shortest", "-c:v", "libx264",
                            "-c:a", "aac", str(src)], check=True)
            with self.assertRaises(C.ClipError):  # local file without a listed creator
                C.add(cfg, "aion2_clips", str(src), "nobody", log=lambda *_: None)
            cid = C.add(cfg, "aion2_clips", str(src), "TestStreamer", log=lambda *_: None)
            out = C.short(cfg, "aion2_clips", cid, 1, 9, "Insane 1v3 clutch", "1V3 CLUTCH", log=lambda *_: None)
            meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["kind"], "clip")
            self.assertIn("https://www.twitch.tv/teststreamer", meta["description"])
            self.assertFalse(meta["contains_synthetic_media"])
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                    "stream=width,height", "-of", "csv=p=0", str(out / "video.mp4")],
                                   capture_output=True, text=True).stdout.strip()
            self.assertEqual(probe, "1080,1920")
            with self.assertRaises(C.ClipError):
                C.short(cfg, "aion2_clips", cid, 5, 2, "bad range", log=lambda *_: None)
            comp = C.compile_video(cfg, "aion2_clips", [cid, cid], "Best of the week", log=lambda *_: None)
            cmeta = json.loads((comp / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(cmeta["kind"], "clip_compilation")
            self.assertNotIn("#shorts", cmeta["hashtags"])
            self.assertAlmostEqual(cmeta["length_sec"], 23.5, delta=0.6)
        finally:
            C.clips_dir = orig
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
