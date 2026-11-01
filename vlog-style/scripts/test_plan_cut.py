#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for plan_cut.py. Run: python3 -I -B test_plan_cut.py"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir

import paths  # noqa: E402
import plan_cut  # noqa: E402

FIX = HERE / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class PlanCutTest(unittest.TestCase):
    def setUp(self):
        self.style, self.index = load("style.json"), load("footage.json")
        self.brief, self.beats = load("brief.json"), load("beats.json")
        self.clips = {c["id"]: c for c in self.index["clips"]}

    def build(self, index=None, seed=None):
        return plan_cut.build(self.style, index or self.index, self.brief, self.beats, seed)

    def test_deterministic_and_well_formed(self):
        a = self.build()
        self.assertEqual(a, self.build())
        self.assertTrue(a["checks"]["ok"], a["checks"]["problems"])
        self.assertEqual(a["timeline"]["fps"], 29.97)  # fixture footage is NTSC
        self.assertEqual(a["timeline"]["duration_frames"], round(90 * 29.97))
        b = self.build(seed=8)
        self.assertNotEqual([c["clip"] for c in a["video"]], [c["clip"] for c in b["video"]])

    def test_sources_stay_inside_shots_with_handles(self):
        plan = self.build()
        fps, v = plan["timeline"]["fps"], plan["video"]
        for i, c in enumerate(v):
            clip = self.clips[c["clip"]]
            shot = clip["shots"][c["shot"]]
            ratio = clip["fps"] / fps * c["speed"]
            prev_t = v[i - 1]["transition_out"] if i else None
            hl = math.ceil(prev_t["frames"] / 2) if prev_t else 0
            hr = math.ceil(c["transition_out"]["frames"] / 2) if c["transition_out"] else 0
            self.assertGreaterEqual(c["src_in"] - plan_cut.fceil(hl * ratio), shot["start"], c["id"])
            self.assertLessEqual(c["src_out"] + plan_cut.fceil(hr * ratio), shot["end"], c["id"])
            self.assertEqual(c["src_out"] - c["src_in"], plan_cut.fceil((c["rec_out"] - c["rec_in"]) * ratio))

    def test_avoid_list_and_last_clip(self):
        plan = self.build()
        self.assertNotIn("C0005", {c["clip"] for c in plan["video"]})
        self.assertIsNone(plan["video"][-1]["transition_out"])

    def test_slow_mo_only_on_high_frame_rate(self):
        plan = self.build()
        slow = [c for c in plan["video"] if c["speed"] != 1.0]
        self.assertTrue(slow)
        for c in slow:
            self.assertEqual(c["speed"], 0.5)
            self.assertGreaterEqual(self.clips[c["clip"]]["fps"], 1.9 * plan["timeline"]["fps"])
            # 59.94 fps at 50% on a 29.97 timeline uses source frames 1:1
            self.assertEqual(c["src_out"] - c["src_in"], c["rec_out"] - c["rec_in"])
            self.assertEqual(c["audio"], "mute")

    def test_cuts_snap_to_beats(self):
        plan = self.build()
        fps = plan["timeline"]["fps"]
        grid = {round(b * fps) - 1 for b in self.beats["beats"]}  # offset_frames = -1
        bounds = {s["rec_in"] for s in plan["sections"]}
        cuts = [c["rec_in"] for c in plan["video"][1:] if c["rec_in"] not in bounds]
        on = sum(1 for t in cuts if t in grid)
        self.assertGreater(on / len(cuts), 0.3, (on, len(cuts)))  # chance is about 1/15

    def test_chapter_titles(self):
        plan = self.build()
        fps = plan["timeline"]["fps"]
        secs = {s["name"]: s for s in plan["sections"]}
        self.assertEqual([t["text"] for t in plan["titles"]], ["特罗姆瑟 · 挪威", "罗弗敦群岛"])
        for t, name in zip(plan["titles"], ["特罗姆瑟", "罗弗敦"]):
            self.assertEqual(t["rec_in"], secs[name]["rec_in"] + round(1.0 * fps))
            self.assertEqual(t["rec_out"] - t["rec_in"], round(3.0 * fps))
            self.assertEqual((t["track"], t["template"]), (3, "Templates/Edit/Titles/Fade On"))

    def test_music_markers_speech(self):
        plan = self.build()
        total = plan["timeline"]["duration_frames"]
        self.assertEqual([(m["rec_in"], m["rec_out"], m["track"]) for m in plan["audio"]], [(0, total, 2)])
        self.assertEqual(plan["audio"][-1]["fade_out_frames"], round(2 * plan["timeline"]["fps"]))
        self.assertIn("设置播放帧率", plan["markers"][0]["note"])
        self.assertIn("镜头光斑", plan["markers"][0]["note"])
        for a, b in plan["speech"]:
            self.assertTrue(0 <= a < b <= total)

    def test_push_in_on_static_shots(self):
        index = copy.deepcopy(self.index)
        for clip in index["clips"]:
            for shot in clip["shots"]:
                shot["motion"] = "static"
        plan = self.build(index=index)
        pushes = [c for c in plan["video"] if c["motion"]]
        self.assertTrue(pushes)
        for c in pushes:
            self.assertEqual(c["speed"], 1.0)
            self.assertEqual((c["motion"]["type"], c["motion"]["to"], c["motion"]["route"]),
                             ("push_in", 1.08, "fusion"))
        self.assertEqual(len(plan["markers"]), 1 + len(pushes))

    def test_short_footage_fails_loudly(self):
        index = {"clips": [{"id": "A", "source": "projects/x/footage/A.MP4", "fps": 29.97, "frames": 60,
                            "shots": [{"start": 0, "end": 60, "tags": []}]}]}
        brief = dict(self.brief, chapters=[{"name": "x", "tags": []}], avoid=[])
        with self.assertRaises(plan_cut.PlanError):
            plan_cut.build(self.style, index, brief, None)

    def test_explain_table(self):
        text = plan_cut.explain(self.build())
        self.assertIn("| # | 时间 | 段落 |", text)
        self.assertIn("特罗姆瑟 · 挪威", text)

    def test_cli_project_layout_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "projects" / "fixture"
            for name, sub in (("footage.json", "index"), ("beats.json", "index"), ("brief.json", "plan")):
                (proj / sub).mkdir(parents=True, exist_ok=True)
                (proj / sub / name).write_bytes((FIX / name).read_bytes())
            r = subprocess.run([sys.executable, "-I", "-B", str(HERE / "plan_cut.py"),
                                "--style-file", str(FIX / "style.json"), "--project", "fixture"],
                               env=dict(os.environ, VLOG_STYLE_ROOT=tmp), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = proj / "plan" / "cut_plan.json"
            self.assertTrue(out.exists())
            self.assertEqual(paths.check([out]), [])


    def test_rejected_shots_are_never_used(self):
        index = json.loads((FIX / "footage.json").read_text(encoding="utf-8"))
        for c in index["clips"]:
            for s in c["shots"]:
                if s["shot_type"] == "close":
                    s["reject"] = True
        brief = json.loads((FIX / "brief.json").read_text(encoding="utf-8"))
        style = json.loads((FIX / "style.json").read_text(encoding="utf-8"))
        plan = plan_cut.build(style, index, brief, None)
        self.assertTrue(plan["video"])
        self.assertNotIn("close", {v["shot_type"] for v in plan["video"]})

if __name__ == "__main__":
    unittest.main()
