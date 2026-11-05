#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for cleanup.py, duck.py and compare.py readback. Run: python3 -I -B test_tools.py

The duck render test needs ffmpeg on PATH and is skipped without it.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import aggregate  # noqa: E402
import cleanup  # noqa: E402
import compare  # noqa: E402
import duck  # noqa: E402
import paths  # noqa: E402
import plan_cut  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        os.environ[paths.ENV_ROOT] = str(self.root)
        self.P = paths.load()

    def tearDown(self):
        os.environ.pop(paths.ENV_ROOT, None)
        self.tmp.cleanup()

    def run_main(self, fn, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fn(argv)
        return code, out.getvalue(), err.getvalue()


class CleanupTest(TempRoot):
    def test_list_and_dry_run(self):
        ref = self.P.ref("BV1test")
        ref.mkdir(parents=True)
        (ref / "proxy.mp4").write_bytes(b"x" * 2048)
        code, out, _ = self.run_main(cleanup.main, ["list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["path"], "cache/refs/BV1test")
        code, out, err = self.run_main(cleanup.main, ["trash", "BV1test"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1test", out)
        self.assertTrue(ref.exists())  # dry run moves nothing

    def test_refuses_outside_cache(self):
        outside = Path(self.tmp.name) / "footage"
        outside.mkdir()
        refs = self.P.dir("cache") / "refs"
        refs.mkdir(parents=True)
        (refs / "evil").symlink_to(outside)
        code, _, err = self.run_main(cleanup.main, ["trash", "evil", "--yes"])
        self.assertEqual(code, 2)
        self.assertIn("refuse", err)
        self.assertTrue(outside.exists())
        with self.assertRaises(ValueError):
            self.P.ref("../footage")

    def measured_ref(self, ref_id="BV1keep") -> Path:
        ref = self.P.ref(ref_id)
        ref.mkdir(parents=True)
        (ref / "proxy.mp4").write_bytes(b"x" * 2048)
        mv = {"schema": "measure_video/1", "bvid": ref_id, "measured": "2026-11-01",
              "text": {"subtitle_band": [0.9, 0.97],
                       "tracks": [{"i": 0, "kind": "title", "text": "北极圈", "t_in": 7.0},
                                  {"i": 1, "kind": "subtitle", "text": "旁白第一句", "t_in": 1.0}],
                       "transcript": [{"t_in": 1.0, "t_out": 2.0, "text": "旁白第一句"}]}}
        ma = {"speech": [[1.0, 2.0]],
              "transcript": {"source": "captions", "whisper": None,
                             "segments": [{"t_in": 1.0, "t_out": 2.0, "text": "旁白第一句"}]}}
        meta = {"ok": True, "data": [{"field": k, "value": v} for k, v in (
            ("bvid", ref_id), ("title", "测试片"), ("author", "Links (mid: 3816626)"), ("mid", "3816626"),
            ("publish_time", "2026-01-07 18:00"), ("duration_sec", "1787"), ("description", "简介原文"))]}
        for name, data in (("measure_video.json", mv), ("measure_audio.json", ma), ("meta.json", meta),
                           ("labels.json", {"ref": ref_id, "chapters": []})):
            (ref / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return ref

    def test_trash_wants_measurements_kept_first(self):
        self.measured_ref()
        code, _, err = self.run_main(cleanup.main, ["trash", "BV1keep"])
        self.assertEqual(code, 2)
        self.assertIn("never kept", err)
        code, out, _ = self.run_main(cleanup.main, ["trash", "BV1keep", "--no-keep"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1keep", out)

        code, out, _ = self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links"])
        self.assertEqual(code, 0)
        self.assertIn("would keep cache/refs/BV1keep -> styles/links/refs/BV1keep", out)
        dest = self.P.style_refs("links") / "BV1keep"
        self.assertFalse(dest.exists())  # dry run writes nothing
        code, _, _ = self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links", "--yes"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in dest.iterdir()),
                         ["labels.json", "measure_audio.json", "measure_video.json", "meta.json"])
        kept_text = "".join(p.read_text(encoding="utf-8") for p in dest.iterdir())
        self.assertNotIn("旁白第一句", kept_text)
        self.assertNotIn("简介原文", kept_text)
        mv = json.loads((dest / "measure_video.json").read_text(encoding="utf-8"))
        self.assertEqual([t["text"] for t in mv["text"]["tracks"]], ["北极圈", ""])
        self.assertEqual(mv["text"]["transcript"], [])
        self.assertEqual(mv["measured"], "2026-11-01")
        ma = json.loads((dest / "measure_audio.json").read_text(encoding="utf-8"))
        self.assertEqual(ma["speech"], [[1.0, 2.0]])
        self.assertEqual(ma["transcript"]["segments"], [{"t_in": 1.0, "t_out": 2.0}])
        meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(set(meta), set(aggregate.META_KEYS))
        self.assertEqual(meta["author"], "Links")
        self.assertEqual(aggregate.video_meta(dest), meta)
        self.assertEqual(paths.check([dest]), [])

        code, out, _ = self.run_main(cleanup.main, ["list"])
        self.assertEqual(json.loads(out)[0]["kept"], ["styles/links/refs/BV1keep"])
        code, out, _ = self.run_main(cleanup.main, ["trash", "BV1keep"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1keep", out)

    def test_keep_refuses_what_it_cannot_re_draft(self):
        ref = self.measured_ref()
        code, _, err = self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links/polar-night"])
        self.assertEqual(code, 2)
        self.assertIn("top folder", err)
        (ref / "labels.json").unlink()
        code, _, err = self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links"])
        self.assertEqual(code, 2)
        self.assertIn("labels.json", err)
        code, _, err = self.run_main(cleanup.main, ["keep", "BV1none", "--style", "links"])
        self.assertEqual(code, 2)
        self.assertIn("not a reference", err)

    def test_keep_dates_old_measurements_by_file_time(self):
        ref = self.measured_ref()
        mv_path = ref / "measure_video.json"
        mv = json.loads(mv_path.read_text(encoding="utf-8"))
        del mv["measured"]  # measured before measure_video wrote the date
        mv_path.write_text(json.dumps(mv, ensure_ascii=False), encoding="utf-8")
        when = time.mktime((2026, 1, 8, 12, 0, 0, 0, 0, -1))
        os.utime(mv_path, (when, when))
        self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links", "--yes"])
        kept = self.P.style_refs("links") / "BV1keep" / "measure_video.json"
        self.assertEqual(json.loads(kept.read_text(encoding="utf-8"))["measured"], "2026-01-08")


class DraftGuardTest(TempRoot):
    def test_existing_out_is_not_overwritten(self):
        out = Path(self.tmp.name) / "style.json"
        out.write_text("手写的内容", encoding="utf-8")
        nothing = str(Path(self.tmp.name) / "nothing")
        with self.assertRaises(SystemExit) as cm:
            aggregate.main(["draft", "--name", "x", nothing, "--out", str(out)])
        self.assertIn("--force", str(cm.exception))
        with self.assertRaises(SystemExit) as cm:  # --force gets past the guard, then needs measurements
            aggregate.main(["draft", "--name", "x", nothing, "--out", str(out), "--force"])
        self.assertIn("measure_video", str(cm.exception))
        self.assertEqual(out.read_text(encoding="utf-8"), "手写的内容")


class StyleLookupTest(TempRoot):
    def test_missing_pack_names_existing_ones(self):
        code, _, err = self.run_main(plan_cut.main, ["--style", "no-such-pack", "--index", "i", "--brief", "b",
                                                     "--out", "o"])
        self.assertEqual(code, 1)
        self.assertIn("no style pack", err)
        self.assertIn("links/polar-night", err)
        with self.assertRaises(SystemExit) as cm:
            compare.main(["readback", "rb.json", "--plan", "p.json", "--style", "no-such-pack"])
        self.assertIn("no style pack", str(cm.exception))


class EnvelopeTest(unittest.TestCase):
    def test_merge_and_ramps(self):
        self.assertEqual(duck.merge([(1, 2), (2.3, 3), (5, 6)], hold=0.6), [(1, 3), (5, 6)])
        env = duck.envelope([(1.0, 2.0)], 4.0, duck_db=12, attack=0.2, release=0.4, hold=0.6, rate=100)
        floor = 10 ** (-12 / 20)
        self.assertAlmostEqual(env[50], 1.0)            # 0.5 s, before the dip
        self.assertAlmostEqual(env[90], (1 + floor) / 2, places=4)  # half-way down the attack
        self.assertAlmostEqual(env[150], floor, places=5)
        self.assertAlmostEqual(env[220], (1 + floor) / 2, places=4)  # half-way up the release
        self.assertAlmostEqual(env[300], 1.0)


def read_wav_rms(path: Path, t0: float, t1: float) -> float:
    with wave.open(str(path)) as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        w.setpos(int(t0 * sr))
        raw = w.readframes(int((t1 - t0) * sr))
    vals = [int.from_bytes(raw[i:i + width], "little", signed=True) for i in range(0, len(raw), width)]
    return math.sqrt(sum(v * v for v in vals) / len(vals)) / (2 ** (8 * width - 1))


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class DuckRenderTest(TempRoot):
    def test_bed_drops_by_duck_db_under_speech(self):
        proj = self.P.project("p")
        (proj / "music").mkdir(parents=True)
        (proj / "plan").mkdir()
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000:duration=8", "-af", "volume=0.3",
                        "-ac", "2", str(proj / "music" / "m.wav")], check=True)
        plan = {"timeline": {"fps": 30}, "speech": [[90, 150]],
                "audio": [{"id": "m001", "kind": "music", "source": "projects/p/music/m.wav",
                           "src_in_s": 1.0, "rec_in": 0, "rec_out": 180}]}
        plan_path = proj / "plan" / "cut_plan.json"
        plan_path.write_text(json.dumps(plan))
        report = duck.duck_plan(plan_path, self.P, duck_db=12, attack=0.15, release=0.4, hold=0.6)
        self.assertEqual(report["beds"][0]["bed"], "projects/p/plan/music_ducked_m001.wav")
        bed = self.P.abs(report["beds"][0]["bed"])
        with wave.open(str(bed)) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 6.0, places=2)
            self.assertEqual(w.getnchannels(), 2)
        loud, quiet = read_wav_rms(bed, 0.5, 2.5), read_wav_rms(bed, 3.3, 4.7)
        self.assertAlmostEqual(20 * math.log10(quiet / loud), -12.0, delta=0.5)
        self.assertEqual(paths.check([plan_path.parent / "ducked.json"]), [])


class ReadbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load = lambda n: json.loads((FIX / n).read_text(encoding="utf-8"))  # noqa: E731
        cls.style = load("style.json")
        cls.plan = plan_cut.build(cls.style, load("footage.json"), load("brief.json"), load("beats.json"))

    def readback(self, s0=86400):
        video: dict = {}
        for v in self.plan["video"]:
            video.setdefault(str(v["track"]), []).append(
                {"start": s0 + v["rec_in"], "end": s0 + v["rec_out"], "source_start": v["src_in"]})
        for t in self.plan["titles"]:
            video.setdefault(str(t["track"]), []).append({"start": s0 + t["rec_in"], "end": s0 + t["rec_out"]})
        return {"start_frame": s0, "video": video}

    def test_exact_build_passes(self):
        res = compare.check_readback(self.readback(), self.plan, self.style)
        self.assertTrue(res["ok"], res["issues"])
        self.assertEqual(res["items_exact"], len(self.plan["video"]) + len(self.plan["titles"]))
        self.assertIn("within_style", res["rhythm"])

    def test_shift_missing_and_transition_edge(self):
        rb = self.readback()
        v1 = rb["video"]["1"]
        k = next(i for i, v in enumerate(self.plan["video"]) if v["transition_out"])
        half = -(-self.plan["video"][k]["transition_out"]["frames"] // 2)
        v1[k]["end"] += half          # inside a transition: tolerated
        v1[k + 1]["start"] += half
        self.assertTrue(compare.check_readback(rb, self.plan)["ok"])
        j = next(i for i, v in enumerate(self.plan["video"]) if i and not v["transition_out"]
                 and not self.plan["video"][i - 1]["transition_out"])
        v1[j]["start"] += 1           # a hard cut one frame late: fails
        del rb["video"]["3"][0]       # a title missing: fails
        res = compare.check_readback(rb, self.plan)
        self.assertFalse(res["ok"])
        self.assertTrue(any(f"#{j + 1}" in i for i in res["issues"]), res["issues"])
        self.assertTrue(any(i.startswith("V3:") for i in res["issues"]), res["issues"])


if __name__ == "__main__":
    unittest.main()
