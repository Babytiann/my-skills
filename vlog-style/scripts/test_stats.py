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

    def test_cfr_index_follows_timestamp_gaps(self):
        # one frame missing after the third: counting frames would put the rest one early
        pts = [10 + k / 30 for k in (0, 1, 2, 4, 5)]
        self.assertEqual(stats.cfr_index(pts, 30.0), [0, 1, 2, 4, 5])
        self.assertEqual(stats.cfr_index(list(reversed(pts)), 30.0), [0, 1, 2, 4, 5])
        self.assertEqual(stats.cfr_index([], 30.0), [])


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

    def test_too_few_cuts_say_nothing(self):
        # learnings 2026-11-13: the Lawrence vlog's 13 cuts in the music were called not_on_beat
        on = [self.beats[8 * k + 4] for k in range(stats.MIN_BEAT_CUTS)]
        self.assertEqual(stats.beat_alignment(on[:-1], self.beats, tol=1 / 30)["verdict"], "too_few_cuts")
        self.assertEqual(stats.beat_alignment(on, self.beats, tol=1 / 30)["verdict"], "on_beat")

    def test_half_and_double_time_agree(self):
        self.assertTrue(stats.tempo_agrees(75.3, 152.0))   # the Japan short: librosa at double time
        self.assertTrue(stats.tempo_agrees(80.0, 80.7))
        self.assertTrue(stats.tempo_agrees(120.0, 61.0))
        self.assertFalse(stats.tempo_agrees(93.17, 107.7))  # the Lawrence vlog
        self.assertFalse(stats.tempo_agrees(99.92, 129.2))
        self.assertIsNone(stats.tempo_agrees(None, 120.0))

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

    def test_band_sits_on_the_caption_line_not_on_scene_text(self):
        # Svalbard (BV1H5xie8EJZ): more small scene text low in the frame (a booking site on
        # screen, signs) than caption lines, so the median height of all bottom-centred text fell
        # between them and the captions were sorted as titles.
        def track(y, h, text, t):
            return {"t_in": t, "t_out": t + 1.5, "center": [0.5, y], "height": h, "drift": 0.0,
                    "text": text, "n": 6}
        caps = [track(0.952, 0.043, f"这是第{k}句字幕", 2.0 * k) for k in range(10)]
        scene = [track(0.74 + 0.012 * k, 0.013, f"标牌{k}", 30.0 + k) for k in range(15)]
        tracks = caps + scene
        info = stats.classify_text(tracks)
        self.assertAlmostEqual(info["subtitle_band"]["y"], 0.952, places=2)
        self.assertAlmostEqual(info["subtitle_band"]["height"], 0.043, places=3)
        self.assertEqual([t["kind"] for t in caps], ["subtitle"] * 10)
        self.assertNotIn("title", [t["kind"] for t in scene])

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

    def test_every_source_says_where_it_came_from(self):
        for key in ("url", "title"):
            bad = copy.deepcopy(self.style)
            del bad["sources"][0][key]
            self.assertTrue(any(f"missing {key}" in e for e in schema.check_style(bad)), key)
        bad = copy.deepcopy(self.style)
        bad["sources"][0]["title"] = " "
        self.assertTrue(any("sources[0].title" in e for e in schema.check_style(bad)))

    def test_only_whips_and_wipes_have_a_direction(self):
        ok = copy.deepcopy(self.style)
        ok["transitions"] = [{"type": "hard_cut", "share": 0.9}, {"type": "whip", "share": 0.1, "direction": "right"}]
        self.assertEqual(schema.check_style(ok), [])
        bad = copy.deepcopy(ok)
        bad["transitions"][1] = {"type": "dissolve", "share": 0.1, "frames": 12, "direction": "right"}
        self.assertTrue(any("transitions[1].direction" in e for e in schema.check_style(bad)), schema.check_style(bad))

    def test_bool_is_not_a_number(self):
        self.assertTrue(schema.validate(True, {"type": "number"}))
        self.assertFalse(schema.validate(3, {"type": "number"}))



class AsrPiecesTest(unittest.TestCase):
    def test_short_audio_is_one_piece(self):
        self.assertEqual(stats.quiet_cuts([-20.0] * 500, 0.05), [])

    def test_cuts_land_in_the_quiet_and_pieces_stay_short(self):
        db = [-20.0] * 1500  # 75 s at 0.05 s hops
        for i in range(480, 488):  # a pause at 24.0-24.4 s
            db[i] = -60.0
        for i in range(1000, 1008):  # and at 50.0-50.4 s
            db[i] = -60.0
        cuts = stats.quiet_cuts(db, 0.05)
        self.assertEqual(len(cuts), 2)
        self.assertTrue(24.0 <= cuts[0] <= 24.4, cuts)
        self.assertTrue(50.0 <= cuts[1] <= 50.4, cuts)
        edges = [0.0, *cuts, 75.0]
        self.assertTrue(all(b - a <= 28.0 for a, b in zip(edges, edges[1:])), edges)

    def test_no_pause_still_cuts_within_the_limit(self):
        cuts = stats.quiet_cuts([-20.0] * 1600, 0.05)
        pieces = [b - a for a, b in zip([0.0, *cuts], [*cuts, 80.0])]
        self.assertTrue(all(p <= 28.0 for p in pieces), pieces)
        self.assertTrue(all(p >= 10.0 for p in pieces[:-1]), pieces)


class SentencesTest(unittest.TestCase):
    @staticmethod
    def words(*rows):
        return [{"word": w, "start": a, "end": b} for w, a, b in rows]

    def test_split_at_sentence_end_and_long_silence(self):
        ws = self.words(("今天", 0.0, 0.4), ("到了", 0.4, 0.8), ("小镇。", 0.8, 1.2), ("我们", 1.2, 1.5),
                        ("去", 1.5, 1.7), ("吃饭", 1.7, 2.1), ("然后", 3.5, 3.9), ("上山", 3.9, 4.3))
        got = stats.sentences(ws)
        self.assertEqual([g["text"] for g in got], ["今天到了小镇。", "我们去吃饭", "然后上山"])
        self.assertEqual((got[1]["t_in"], got[1]["t_out"]), (1.2, 2.1))

    def test_commas_and_short_pauses_stay_inside_a_sentence(self):
        ws = self.words(("下雪了，", 0.0, 0.6), ("很", 1.0, 1.2), ("冷！", 1.2, 1.5))
        self.assertEqual([g["text"] for g in stats.sentences(ws)], ["下雪了，很冷！"])

    def test_language_change_splits_and_is_kept(self):
        ws = [{"word": "你好", "start": 0.0, "end": 0.4, "lang": "zh"},
              {"word": " Hello", "start": 0.5, "end": 0.8, "lang": "en"},
              {"word": " there", "start": 0.8, "end": 1.1, "lang": "en"}]
        got = stats.sentences(ws)
        self.assertEqual([(g["text"], g["lang"]) for g in got], [("你好", "zh"), ("Hello there", "en")])

    def test_blank_words_are_ignored(self):
        self.assertEqual(stats.sentences(self.words((" ", 0.0, 0.1))), [])

if __name__ == "__main__":
    unittest.main()
