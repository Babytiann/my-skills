#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for stats.py and schema.py. Run: python3 -I -B test_stats.py"""

from __future__ import annotations

import copy
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import schema  # noqa: E402
import stats  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class SummaryTest(unittest.TestCase):
    def test_median_mad_percentile(self):
        xs = [1, 2, 3, 4, 100]
        self.assertEqual(stats.median(xs), 3)
        self.assertEqual(stats.mad(xs), 1)
        self.assertAlmostEqual(stats.percentile([0, 10], 25), 2.5)
        self.assertIsNone(stats.median([]))
        s = stats.summary(xs)
        self.assertEqual((s["n"], s["median"], s["max"]), (5, 3, 100))

    def test_consistency(self):
        self.assertEqual(stats.consistency({"a": [3, 3.2, 2.9], "b": [3.1, 3.3]})["verdict"], "consistent")
        self.assertEqual(stats.consistency({"a": [1, 1.1], "b": [4, 4.2]})["verdict"], "varies")
        self.assertEqual(stats.consistency({"a": [1, 2, 3]})["verdict"], "single_source")

    def test_shot_lengths(self):
        self.assertEqual(stats.shot_lengths([2.0, 5.0, 12.0], 0.0, 10.0), [2.0, 3.0, 5.0])


class BeatTest(unittest.TestCase):
    beats = [0.5 * k for k in range(240)]  # 120 BPM, 2 min

    def test_on_beat_cuts_beat_baseline(self):
        rng = random.Random(1)
        cuts = sorted(rng.choice(self.beats[4:-4]) - 1 / 60 for _ in range(30))
        r = stats.beat_alignment(cuts, self.beats, tol=1 / 30)
        self.assertEqual(r["verdict"], "on_beat")
        self.assertEqual(r["hit_rate"], 1.0)
        self.assertLess(r["baseline"], 0.3)
        self.assertAlmostEqual(r["bpm"], 120, places=1)
        self.assertLess(r["offset_s"]["median"], 0)  # cuts land just before the beat

    def test_random_cuts_are_not_on_beat(self):
        rng = random.Random(2)
        cuts = sorted(rng.uniform(2, 118) for _ in range(40))
        self.assertEqual(stats.beat_alignment(cuts, self.beats, tol=1 / 30)["verdict"], "not_on_beat")

    def test_spans_limit_which_cuts_count(self):
        cuts = [10.0, 10.5, 11.0, 60.2, 61.3]
        r = stats.beat_alignment(cuts, self.beats, tol=0.03, spans=[(9, 12)])
        self.assertEqual(r["n_cuts"], 3)
        self.assertEqual(stats.bpm_from_beats(self.beats, spans=[(0, 30)]), 120.0)

    def test_bpm_survives_frame_quantisation(self):
        hop = 512 / 22050  # librosa frame
        beats = [round(k * 60 / 128 / hop) * hop for k in range(200)]
        self.assertAlmostEqual(stats.bpm_from_beats(beats), 128, delta=0.3)
        beats = [round(k * 0.5 / hop) * hop for k in range(60)]
        self.assertAlmostEqual(stats.bpm_from_beats(beats), 120, delta=0.3)


def ocr(t, text, box, conf=0.9):
    return {"t": t, "text": text, "box": box, "conf": conf}


class TextTest(unittest.TestCase):
    def test_link_and_classify(self):
        step = 0.25
        samples = []
        # burned-in captions: bottom centre, one line every 1.5 s, 8 lines
        for k in range(8):
            for j in range(6):
                t = 1.0 + 1.5 * k + j * step
                samples.append(ocr(t, f"这是第{k}句字幕的内容", [0.3, 0.88, 0.4, 0.04]))
        # a big centred title 4.0-6.0 s
        for j in range(8):
            samples.append(ocr(4.0 + j * step, "北极圈", [0.35, 0.42, 0.3, 0.1]))
        # a shop sign drifting with the camera
        for j in range(6):
            samples.append(ocr(2.0 + j * step, "超市营业中", [0.1 + 0.03 * j, 0.3, 0.12, 0.03]))
        tracks = stats.link_text(samples, step)
        self.assertEqual(len(tracks), 10, [t["text"] for t in tracks])
        info = stats.classify_text(tracks)
        kinds = {t["text"]: t["kind"] for t in tracks}
        self.assertEqual(info["counts"].get("subtitle"), 8)
        self.assertEqual(kinds["北极圈"], "title")
        self.assertEqual(kinds["超市营业中"], "other")
        title = next(t for t in tracks if t["text"] == "北极圈")
        self.assertAlmostEqual(title["t_in"], 4.0)
        self.assertAlmostEqual(title["t_out"], 6.0)

    def test_gap_tolerance(self):
        step = 0.25
        s = [ocr(0.0, "地名", [0.4, 0.4, 0.2, 0.1]), ocr(0.25, "地名", [0.4, 0.4, 0.2, 0.1]),
             ocr(0.75, "地名", [0.4, 0.4, 0.2, 0.1]),  # one missed sample
             ocr(2.0, "地名", [0.4, 0.4, 0.2, 0.1])]   # too far: new track
        self.assertEqual(len(stats.link_text(s, step, max_gap=1)), 2)

    def test_single_misread_does_not_split(self):
        step = 0.25
        texts = ["极夜里的特罗姆瑟"] * 3 + ["极夜里的特罗姆琴"] + ["极夜里的特罗姆瑟"] * 3
        s = [ocr(i * step, t, [0.3, 0.88, 0.4, 0.04]) for i, t in enumerate(texts)]
        tracks = stats.link_text(s, step)
        self.assertEqual([(t["text"], t["n"]) for t in tracks], [("极夜里的特罗姆瑟", 7)])


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.style = json.loads((FIX / "style.json").read_text(encoding="utf-8"))

    def test_fixture_is_valid(self):
        self.assertEqual(schema.check_style(self.style), [])

    def test_errors_are_specific(self):
        bad = copy.deepcopy(self.style)
        del bad["beat"]
        bad["transitions"][0]["type"] = "spin"
        bad["structure"]["sections"][0]["share"] = 0.5
        errs = schema.check_style(bad)
        self.assertTrue(any("missing beat" in e for e in errs), errs)
        self.assertTrue(any("'spin'" in e for e in errs), errs)

    def test_shares_must_sum_to_one(self):
        bad = copy.deepcopy(self.style)
        bad["structure"]["sections"][0]["share"] = 0.5
        self.assertTrue(any("shares sum" in e for e in schema.check_style(bad)))

    def test_bool_is_not_a_number(self):
        self.assertTrue(schema.validate(True, {"type": "number"}))
        self.assertFalse(schema.validate(3, {"type": "number"}))


if __name__ == "__main__":
    unittest.main()
