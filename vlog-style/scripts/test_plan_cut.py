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
import schema  # noqa: E402
import story  # noqa: E402

FIX = HERE / "fixtures"
BASE = FIX / "baseline"


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


class BaselineTest(unittest.TestCase):
    """With no story and no new style or brief field, output stays byte-identical to the saved runs."""

    def assert_same(self, style, seed, name):
        plan = plan_cut.build(style, load("footage.json"), load("brief.json"), load("beats.json"), seed)
        self.assertEqual(json.dumps(plan, ensure_ascii=False, indent=1),
                         (BASE / f"{name}.json").read_text(encoding="utf-8"), name)
        self.assertEqual(plan_cut.explain(plan) + "\n",
                         (BASE / f"{name}.explain.md").read_text(encoding="utf-8"), name)

    def test_no_story_matches_baseline(self):
        for seed in (1, 7, 8, 42):
            self.assert_same(load("style.json"), seed, f"seed-{seed}")

    def test_links_pack_as_frozen_matches_baseline(self):
        style = json.loads((BASE / "links-style.json").read_text(encoding="utf-8"))
        self.assert_same(style, 7, "links-seed-7")


def take(cid, at, fps=29.97, secs=40.0, tags=("town",), kind="wide", lines=(), order=0, day=1, sharp=0.6, **shot):
    """One clip of a test trip. `lines` are (start s, end s, text)."""
    frames = round(secs * fps)
    said = [[round(a * fps), round(b * fps), text] for a, b, text in lines]
    return {"id": cid, "source": f"projects/t/footage/{cid}.MP4", "fps": fps, "frames": frames, "width": 3840,
            "height": 2160, "hdr": False, "shot_at": at, "shot_at_source": "format.creation_time", "device": "cam",
            "order": order, "day": day, "sentences": said,
            "shots": [{"start": 0, "end": frames, "shot_type": kind, "tags": list(tags), "motion": "handheld",
                       "sharp": sharp, "exposure_ok": True, "shake": 0.3, "best": frames // 2,
                       "speech": [[a, b] for a, b, _ in said], "sentences": said, **shot}]}


D1_LINES = [(2.0, 6.0, "今天到了小镇"), (7.0, 12.0, "这里的路很窄，"), (12.5, 20.0, "但是人特别热情"),
            (40.0, 44.0, "我们去吃午饭"), (45.0, 52.0, "这家店开了五十年"), (90.0, 96.0, "下午准备去海边")]
D3_LINES = [(2.0, 8.0, "第三天我们上山了"), (10.0, 18.0, "风特别大"), (30.0, 34.0, "这就是我们要找的地方"),
            (50.0, 58.0, "太不容易了")]


def trip(day1_broll=10):
    """Day 1 in town, day 3 on the mountain: a talking clip and B-roll for each."""
    clips = [take("TALK1", "2026-02-11T09:00:00+00:00", 59.94, 120.0, ("town",), "medium", D1_LINES, 0, 1, light="day"),
             take("TALK3", "2026-02-13T09:00:00+00:00", 29.97, 120.0, ("mountain",), "medium", D3_LINES, 100, 3, light="day")]
    for i in range(day1_broll):
        clips.append(take(f"T{i:02d}", f"2026-02-11T09:{i * 3 + 1:02d}:00+00:00", order=1 + i, day=1))
    for i in range(10):
        clips.append(take(f"M{i:02d}", f"2026-02-13T09:{i * 3 + 1:02d}:00+00:00", tags=("mountain",), order=101 + i, day=3))
    return {"clips": clips}


def plan_brief(**over):
    return {"project": "t", "duration_s": 120, "seed": 3,
            "chapters": [{"name": "小镇", "tags": ["town"], "title": "小镇"},
                         {"name": "上山", "tags": ["mountain"], "title": "上山"}],
            "hook_tags": ["mountain"], "ending_tags": ["mountain"],
            "music": [{"source": "projects/t/music/track.wav", "start_s": 0}], **over}


def plan_story(**over):
    return {"hook": [{"clip": "TALK3", "in_s": 30.5, "out_s": 33.0}],
            "chapters": {"小镇": [{"clip": "TALK1", "in_s": 2.5, "out_s": 19.0, "note": "到达"},
                                  {"clip": "TALK1", "in_s": 40.5, "out_s": 50.0}],
                         "上山": [{"clip": "TALK3", "in_s": 3.0, "out_s": 17.0},
                                  {"clip": "TALK3", "in_s": 30.5, "out_s": 33.0}]}, **over}


class StoryTest(unittest.TestCase):
    def setUp(self):
        self.style, self.beats = load("style.json"), load("beats.json")

    def build(self, index=None, brief=None, story=None, style=None, seed=None, beats=None):
        return plan_cut.build(style or self.style, index or trip(), brief or plan_brief(), beats,
                              seed, plan_story() if story is None else story)

    def v1(self, plan):
        return [v for v in plan["video"] if v["track"] == 1]

    def test_plan_is_well_formed_and_deterministic(self):
        plan = self.build()
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        self.assertEqual(plan, self.build())
        self.assertEqual(schema.validate(plan, schema.PLAN), [])

    def test_v1_runs_end_to_end_across_the_timeline(self):
        plan = self.build()
        v1 = self.v1(plan)
        self.assertEqual(v1[0]["rec_in"], 0)
        self.assertEqual(v1[-1]["rec_out"], plan["timeline"]["duration_frames"])
        for a, b in zip(v1, v1[1:]):
            self.assertEqual(a["rec_out"], b["rec_in"], (a["id"], b["id"]))

    def test_tracks_and_roles(self):
        plan = self.build()
        roles = {v["role"] for v in plan["video"]}
        self.assertEqual(roles, {"aroll", "broll", "cutaway"})
        for v in plan["video"]:
            self.assertEqual(v["audio"], "mute", v["id"])
            self.assertEqual(v["track"], 2 if v["role"] == "cutaway" else 1, v["id"])
        self.assertEqual(len(plan["dialogue"]), 5)
        self.assertEqual({d["track"] for d in plan["dialogue"]}, {1})

    def test_lines_align_outward_to_whole_sentences_with_pads(self):
        plan = self.build()
        d = next(x for x in plan["dialogue"] if x["clip"] == "TALK1")
        fps = 59.94
        # in 2.5 s falls inside the sentence starting at 2.0, out 19.0 inside the one ending at 20.0
        self.assertEqual([s[2] for s in d["sentences"]], ["今天到了小镇", "这里的路很窄，", "但是人特别热情"])
        self.assertLessEqual(d["src_in"], round(2.0 * fps))
        self.assertGreaterEqual(d["src_out"], round(20.0 * fps) - 2)
        self.assertAlmostEqual(d["src_in"] / fps, 2.0 - 0.15, delta=0.03)
        self.assertAlmostEqual(d["src_out"] / fps, 20.0 + 0.25, delta=0.03)

    def test_edges_in_silence_shrink_to_the_sentences_inside(self):
        story = plan_story()
        story["chapters"]["小镇"] = [{"clip": "TALK1", "in_s": 6.5, "out_s": 39.0}]  # 6.5 and 39 are both silent
        d = next(x for x in self.build(story=story)["dialogue"] if x["clip"] == "TALK1")
        self.assertEqual([s[2] for s in d["sentences"]], ["这里的路很窄，", "但是人特别热情"])

    def test_no_sentence_in_range_is_an_error(self):
        story = plan_story()
        story["chapters"]["小镇"] = [{"clip": "TALK1", "in_s": 25.0, "out_s": 30.0}]
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*\[0\].*no sentence"):
            self.build(story=story)

    def test_pad_stops_half_way_to_a_close_sentence(self):
        index = trip()
        talk = index["clips"][0]
        fps = talk["fps"]
        talk["sentences"] = [[round(a * fps), round(b * fps), t] for a, b, t in
                             [(2.0, 6.0, "一"), (6.1, 12.0, "二"), (12.1, 20.0, "三"), (40.0, 44.0, "四"), (45.0, 52.0, "五")]]
        story = plan_story()
        story["chapters"]["小镇"] = [{"clip": "TALK1", "in_s": 6.5, "out_s": 11.0}, {"clip": "TALK1", "in_s": 13.0, "out_s": 19.0}]
        plan = self.build(index=index, story=story)
        ds = [d for d in plan["dialogue"] if d["clip"] == "TALK1"]
        self.assertAlmostEqual(ds[0]["src_in"] / fps, 6.05, delta=0.03)
        self.assertLessEqual(ds[0]["src_out"], ds[1]["src_in"])  # two neighbours share the midpoint, no reuse
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])

    def test_59_94_source_on_a_29_97_timeline(self):
        plan = self.build()
        self.assertEqual(plan["timeline"]["fps"], 29.97)
        for d in plan["dialogue"]:
            if d["source_fps"] == 59.94:
                self.assertEqual(d["src_out"] - d["src_in"], 2 * (d["rec_out"] - d["rec_in"]), d["id"])

    def test_a_line_may_span_shots_of_one_clip(self):
        index = trip()
        talk = index["clips"][0]
        cut = round(9.0 * talk["fps"])  # scene detection split the take in the middle of a sentence
        shot = talk["shots"][0]
        talk["shots"] = [{**shot, "end": cut}, {**shot, "start": cut}]
        story = plan_story()
        story["chapters"]["小镇"] = [{"clip": "TALK1", "in_s": 10.0, "out_s": 11.0}]
        plan = self.build(index=index, story=story)
        d = next(x for x in plan["dialogue"] if x["clip"] == "TALK1")
        self.assertEqual([s[2] for s in d["sentences"]], ["这里的路很窄，"])  # the whole sentence, from before the cut
        self.assertLess(d["src_in"], cut)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])

    def test_cutaways_sit_inside_their_line_and_leave_the_speaker_visible(self):
        plan = self.build()
        fps = plan["timeline"]["fps"]
        arolls = [v for v in plan["video"] if v["role"] == "aroll"]
        v2 = [v for v in plan["video"] if v["role"] == "cutaway"]
        self.assertTrue(v2)
        for c in v2:
            over = [a for a in arolls if a["rec_in"] <= c["rec_in"] and c["rec_out"] <= a["rec_out"]]
            self.assertEqual(len(over), 1, c["id"])
            self.assertGreaterEqual(c["rec_in"] - over[0]["rec_in"], round(1.5 * fps) - 1)
            self.assertGreaterEqual(over[0]["rec_out"] - c["rec_out"], round(1.0 * fps) - 1)
            self.assertGreater(over[0]["rec_out"] - over[0]["rec_in"], 6.0 * fps)
            self.assertNotEqual(c["shot_type"], "selfie")
        for a, b in zip(v2, v2[1:]):
            self.assertLessEqual(a["rec_out"], b["rec_in"])

    def test_a1_has_no_overlap_even_with_j_and_l(self):
        style = dict(self.style, aroll={"j_s": 3.0, "l_s": 3.0})
        plan = self.build(style=style)
        ds = sorted(plan["dialogue"], key=lambda d: d["rec_in"])
        for a, b in zip(ds, ds[1:]):
            self.assertLessEqual(a["rec_out"], b["rec_in"])
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        talk = next(a for a in plan["video"] if a["role"] == "aroll" and a["clip"] == "TALK1")
        d = next(x for x in plan["dialogue"] if x["id"] == talk["dialogue"])
        self.assertLess(d["rec_in"], talk["rec_in"])  # the J reached in, as far as the silence allows

    def test_j_and_l_stop_at_the_sentence_next_door(self):
        style = dict(self.style, aroll={"j_s": 30.0, "l_s": 30.0})
        plan = self.build(style=style)
        for d in plan["dialogue"]:
            fps = d["source_fps"]
            lines = sorted(next(c for c in trip()["clips"] if c["id"] == d["clip"])["sentences"])
            first = lines.index(d["sentences"][0])
            if first:
                self.assertGreaterEqual(d["src_in"], lines[first - 1][1] - fps / 29.97)

    def test_hook_may_reuse_frames_heard_again_later(self):
        plan = self.build()
        hook = next(v for v in plan["video"] if v.get("preview"))
        later = [v for v in plan["video"] if v["role"] == "aroll" and not v.get("preview") and v["clip"] == hook["clip"]
                 and v["src_in"] < hook["src_out"] and hook["src_in"] < v["src_out"]]
        self.assertTrue(later)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])

    def test_hook_opens_on_the_words(self):
        plan = self.build()
        self.assertEqual(self.v1(plan)[0]["role"], "aroll")
        self.assertTrue(self.v1(plan)[0].get("preview"))

    def test_hook_grows_when_its_words_outrun_the_style_share(self):
        story = plan_story(hook=[{"clip": "TALK3", "in_s": 3.0, "out_s": 33.0}])
        story["chapters"]["上山"] = [{"clip": "TALK3", "in_s": 50.5, "out_s": 57.0}]
        plan = self.build(story=story, brief=plan_brief(duration_s=100))
        hook = next(s for s in plan["sections"] if s["role"] == "hook")
        self.assertGreater((hook["rec_out"] - hook["rec_in"]) / 29.97, 0.1 * 100 * 2)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])

    def test_a_roll_over_budget_names_the_numbers(self):
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*\d+\.\d s.*\d+\.\d s"):
            self.build(brief=plan_brief(chapters=[{"name": "小镇", "tags": ["town"], "weight": 0.01},
                                                  {"name": "上山", "tags": ["mountain"], "weight": 10}]))

    def test_chapter_weight_defaults_to_how_much_is_said(self):
        plan = self.build()
        secs = {s["name"]: s["rec_out"] - s["rec_in"] for s in plan["sections"]}
        said = {}
        for v in plan["video"]:
            if v["role"] == "aroll" and not v.get("preview"):
                said[v["section"]] = said.get(v["section"], 0) + v["rec_out"] - v["rec_in"]
        self.assertGreater(said["小镇"], said["上山"])
        self.assertGreater(secs["小镇"], secs["上山"])

    def test_film_length_can_follow_the_words(self):
        brief = plan_brief()
        del brief["duration_s"]
        plan = self.build(brief=brief, style=dict(self.style, aroll={"speech_share": 0.5}))
        self.assertAlmostEqual(plan["story_stats"]["aroll_share"], 0.5, delta=0.03)
        with self.assertRaisesRegex(plan_cut.PlanError, "duration_s"):
            self.build(brief=brief)

    def test_gaps_too_small_for_a_shot_are_dropped_and_v1_still_fills(self):
        shares, left = plan_cut.allocate(100, [1.5, 1.0, 1.0], [1000, 1000, 1000], 40)
        self.assertEqual((sum(shares), left), (100, 0))
        self.assertTrue(all(s == 0 or s >= 40 for s in shares), shares)

    def test_a_gap_with_no_footage_passes_its_share_on(self):
        shares, left = plan_cut.allocate(300, [1.5, 1.0, 1.0], [1000, 0, 1000], 40)
        self.assertEqual(shares[1], 0)
        self.assertEqual((sum(shares), left), (300, 0))

    def test_allocate_reports_what_nothing_can_take(self):
        shares, left = plan_cut.allocate(300, [1.0, 1.0], [100, 100], 40)
        self.assertEqual((shares, left), ([100, 100], 100))

    def test_speech_comes_from_the_lines_on_a1(self):
        plan = self.build()
        for d in plan["dialogue"]:
            self.assertTrue(any(d["rec_in"] <= a and b <= d["rec_out"] for a, b in plan["speech"]), d["id"])
        for a, b in plan["speech"]:
            self.assertTrue(any(d["rec_in"] <= a and b <= d["rec_out"] for d in plan["dialogue"]), (a, b))

    def test_broll_keeps_off_people_talking_while_there_is_a_choice(self):
        index = trip()
        for c in index["clips"]:
            if c["id"].startswith("T"):
                c["shots"][0]["sharp"] = 0.2
        index["clips"].append(take("CHAT", "2026-02-11T09:05:00+00:00", secs=60, lines=[(0, 60, "一直在聊天")],
                                   order=50, sharp=0.99, kind="medium"))
        plan = self.build(index=index)
        self.assertNotIn("CHAT", {v["clip"] for v in plan["video"]})

    def test_ambient_is_off_until_the_style_asks(self):
        self.assertEqual(self.build()["ambient"], [])
        style = dict(self.style, audio={**self.style["audio"], "broll_ambient_db": -18})
        plan = self.build(style=style)
        ids = {(a["clip"], a["src_in"]) for a in plan["ambient"]}
        broll = {(v["clip"], v["src_in"]) for v in plan["video"] if v["role"] == "broll" and v["speed"] == 1.0}
        self.assertEqual(ids, broll)
        self.assertEqual({a["track"] for a in plan["ambient"]}, {4})

    def test_explain_shows_roles_first_lines_and_tracks(self):
        text = plan_cut.explain(self.build())
        self.assertIn("| 角色 |", text)
        self.assertIn("今天到了小镇", text)
        self.assertIn("V2 盖在说话上的画面", text)
        self.assertIn("A1 对白", text)
        self.assertIn("预告", text)

    def test_music_shorter_than_the_film_is_an_error(self):
        brief = plan_brief(music=[{"source": "projects/t/music/a.wav", "start_s": 0, "duration_s": 30}])
        with self.assertRaisesRegex(plan_cut.PlanError, "music runs out"):
            self.build(brief=brief)
        self.assertEqual(self.build(brief=plan_brief(music=[]))["audio"], [])

    def test_review_cells_follow_the_timeline_with_marks_and_words(self):
        plan = self.build()
        cells = story.cells(plan, trip())
        self.assertEqual(len(cells), len(plan["video"]))
        recs = {v["id"]: v["rec_in"] for v in plan["video"]}
        self.assertEqual([recs[c["id"]] for c in cells], sorted(recs[c["id"]] for c in cells))
        first = cells[0]
        self.assertEqual((first["role"], first["marks"]), ("A-roll", "预告"))
        self.assertEqual(first["said"], "这就是我们要找的地方")
        self.assertTrue(first["when"].startswith("D3 "), first["when"])
        cover = next(c for c in cells if c["track"] == 2)
        before = cells[cells.index(cover) - 1]
        self.assertLessEqual(recs[before["id"]], recs[cover["id"]])
        self.assertTrue(cover["said"], "a cutaway sits over a line, so words are heard under it")

    def test_bad_stories_name_the_item(self):
        cases = [({"chapters": {"小镇": [{"clip": "NOPE", "in_s": 0, "out_s": 1}]}}, r"小镇.*\[0\].*NOPE"),
                 ({"chapters": {"小镇": [{"clip": "TALK1", "in_s": 0, "out_s": 9999}]}}, r"小镇.*\[0\].*outside the clip"),
                 ({"chapters": {"小镇": [{"clip": "TALK1", "in_s": 9, "out_s": 2}]}}, r"小镇.*\[0\].*outside the clip"),
                 ({"chapters": {"小镇": [{"clip": "TALK1", "in_s": 2}]}}, r"小镇.*\[0\].*in_s and out_s"),
                 ({"chapters": {"没有这章": []}}, r"没有这章"),
                 ({"hook": [{"clip": "NOPE", "in_s": 0, "out_s": 1}]}, r"hook\[0\].*NOPE")]
        for story, pattern in cases:
            with self.subTest(pattern), self.assertRaisesRegex(plan_cut.PlanError, pattern):
                self.build(story=story)

    def test_a_clip_with_no_transcript_cannot_carry_a_line(self):
        index = trip()
        index["clips"][0]["sentences"] = []
        with self.assertRaisesRegex(plan_cut.PlanError, "no-asr"):
            self.build(index=index)


class FootageFactsTest(unittest.TestCase):
    """The footage's own time outranks everything the style or the score would prefer."""

    def setUp(self):
        self.style = load("style.json")

    def build(self, index, story=None, brief=None, style=None):
        return plan_cut.build(style or self.style, index, brief or self.long_film(), None, None,
                              plan_story() if story is None else story)

    @staticmethod
    def long_film():
        """Long enough that day 1's only clip, the talking one, cannot fill its chapter."""
        return plan_brief(duration_s=400)

    def thin_day1(self):
        """Day 1 has no B-roll, and day 3 has a beautiful shot tagged for day 1's place."""
        index = trip(day1_broll=0)
        index["clips"].append(take("LATER", "2026-02-13T10:00:00+00:00", secs=120, tags=("town", "mountain"),
                                   order=120, day=3, sharp=0.99))
        for c in index["clips"]:
            if c["id"].startswith("M"):
                c["frames"] = round(90 * c["fps"])
                c["shots"][0]["end"] = c["frames"]
        return index

    def test_later_day_never_fills_earlier_chapter(self):
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*short"):
            self.build(self.thin_day1())

    def test_a_tag_shared_with_a_later_day_is_refused_without_a_story_too(self):
        style = dict(self.style, structure={**self.style["structure"], "order": "chronological"})
        with self.assertRaisesRegex(plan_cut.PlanError, "overlap in time"):  # refuses to guess when "town" happened
            plan_cut.build(style, self.thin_day1(), self.long_film(), None)
        brief = plan_brief(duration_s=400, chapters=[
            {"name": "小镇", "tags": ["town"], "window": [None, "2026-02-12T00:00:00+00:00"]},
            {"name": "上山", "tags": ["mountain"], "window": ["2026-02-12T00:00:00+00:00", None]}])
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*short"):
            plan_cut.build(style, self.thin_day1(), brief, None)

    def test_scenery_marked_movable_may_be_borrowed_and_shows_in_the_table(self):
        index = self.thin_day1()
        index["clips"][-1]["shots"][0].update(movable=True, light="day")
        plan = self.build(index)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        lent = [v for v in plan["video"] if v.get("borrowed")]
        self.assertTrue(lent)
        self.assertEqual({v["clip"] for v in lent}, {"LATER"})
        self.assertEqual({v["section"] for v in lent}, {"小镇"})
        self.assertGreater(plan["story_stats"]["borrowed_s"]["小镇"], 0)
        self.assertIn("借用 D3", plan_cut.explain(plan))

    def test_nothing_is_borrowed_while_the_chapter_has_its_own_footage(self):
        index = trip()
        index["clips"].append(take("LATER", "2026-02-13T10:00:00+00:00", secs=120, tags=("town",), order=120, day=3,
                                   sharp=0.99, movable=True, light="day"))
        plan = self.build(index, brief=plan_brief())
        self.assertFalse([v for v in plan["video"] if v.get("borrowed")])
        self.assertNotIn("LATER", {v["clip"] for v in plan["video"] if v["section"] == "小镇"})

    def test_scenery_from_another_place_is_not_borrowed(self):
        index = self.thin_day1()
        index["clips"][-1]["shots"][0].update(movable=True, light="day", tags=["mountain"])
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*short"):
            self.build(index)

    def test_daylight_scenery_does_not_follow_a_night_line(self):
        index = self.thin_day1()
        index["clips"][-1]["shots"][0].update(movable=True, light="day")
        index["clips"][0]["shots"][0]["light"] = "night"
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*short"):
            self.build(index)

    def test_scenery_with_no_light_label_is_not_borrowed(self):
        index = self.thin_day1()
        index["clips"][-1]["shots"][0].update(movable=True)
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*short"):
            self.build(index)

    def test_broll_inside_a_chapter_does_not_run_backward_past_the_tolerance(self):
        for seed in range(1, 6):
            plan = plan_cut.build(self.style, trip(), plan_brief(), None, seed, plan_story())
            self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
            for name in ("小镇", "上山"):
                peak = None
                for v in sorted((v for v in plan["video"] if v["section"] == name and v["track"] == 1),
                                key=lambda v: v["rec_in"]):
                    if peak is not None:
                        self.assertGreaterEqual(v["t_src"], peak - plan["order"]["tolerance_s"], (seed, v["id"]))
                    peak = v["t_src"] if peak is None else max(peak, v["t_src"])

    def test_shots_of_one_scene_may_swap_within_the_tolerance(self):
        style = dict(self.style, structure={**self.style["structure"], "order_tolerance_s": 0})
        strict = plan_cut.build(style, trip(), plan_brief(), None, 3, plan_story())
        self.assertTrue(strict["checks"]["ok"], strict["checks"]["problems"])
        for name in ("小镇", "上山"):
            times = [v["t_src"] for v in sorted((v for v in strict["video"] if v["section"] == name and v["track"] == 1),
                                                key=lambda v: v["rec_in"])]
            self.assertEqual(times, sorted(times), name)

    def test_chapters_stay_in_their_own_days(self):
        plan = self.build(trip(), brief=plan_brief())
        for v in plan["video"]:
            if v["section"] == "小镇":
                self.assertEqual(v["day"], 1, v["id"])
            if v["section"] == "上山":
                self.assertEqual(v["day"], 3, v["id"])

    def test_a_line_against_the_clock_needs_a_reason(self):
        story = plan_story()
        story["chapters"]["小镇"] = [{"clip": "TALK1", "in_s": 40.5, "out_s": 50.0}, {"clip": "TALK1", "in_s": 2.5, "out_s": 19.0}]
        with self.assertRaisesRegex(plan_cut.PlanError, r"小镇.*\[1\].*out_of_order"):
            self.build(trip(), story)
        story["chapters"]["小镇"][1]["out_of_order"] = "回忆刚到的时候"
        plan = self.build(trip(), story, brief=plan_brief())
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        self.assertIn("顺序例外：回忆刚到的时候", plan_cut.explain(plan))

    def test_a_line_from_another_chapters_time_needs_a_reason(self):
        story = plan_story()
        story["chapters"]["小镇"].append({"clip": "TALK3", "in_s": 50.5, "out_s": 57.0})
        with self.assertRaisesRegex(plan_cut.PlanError, "overlap in time|out_of_order"):
            self.build(trip(), story)
        story["chapters"]["小镇"][-1]["out_of_order"] = "预告第三天"
        self.assertTrue(self.build(trip(), story, brief=plan_brief())["checks"]["ok"])

    def test_chapters_out_of_shooting_order_are_refused(self):
        brief = plan_brief(chapters=[{"name": "上山", "tags": ["mountain"]}, {"name": "小镇", "tags": ["town"]}])
        with self.assertRaisesRegex(plan_cut.PlanError, "overlap in time"):
            self.build(trip(), brief=brief)

    def test_a_chapter_with_no_way_to_tell_its_time_is_refused(self):
        brief = plan_brief(chapters=[{"name": "小镇", "tags": ["town"]}, {"name": "无名", "tags": ["nothing"]},
                                     {"name": "上山", "tags": ["mountain"]}])
        with self.assertRaisesRegex(plan_cut.PlanError, "无名"):
            self.build(trip(), brief=brief)

    def test_a_window_in_the_brief_is_the_truth(self):
        brief = plan_brief(chapters=[{"name": "小镇", "tags": ["town"], "window": [None, "2026-02-11T09:10:00+00:00"]},
                                     {"name": "上山", "tags": ["mountain"]}])
        plan = self.build(trip(), brief=brief)
        limit = plan_cut.epoch("2026-02-11T09:10:00+00:00")
        for v in plan["video"]:
            if v["section"] == "小镇":
                self.assertLess(v["t_src"], limit, v["id"])

    def test_showcase_takes_only_what_the_words_promised(self):
        index = trip()
        index["clips"].append(take("FJORD", "2026-02-13T11:00:00+00:00", secs=60, tags=("fjord",), order=130, day=3,
                                   movable=True, light="day"))
        story = plan_story()
        story["chapters"]["小镇"].insert(1, {"showcase": "峡湾", "tags": ["fjord"], "dur_s": 12})
        plan = self.build(index, story, brief=plan_brief())
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        shown = [v for v in plan["video"] if v.get("showcase")]
        self.assertTrue(shown)
        self.assertEqual({v["clip"] for v in shown}, {"FJORD"})
        total = sum(v["rec_out"] - v["rec_in"] for v in shown)
        self.assertEqual(total, round(12 * plan["timeline"]["fps"]))
        talk = [v for v in plan["video"] if v["role"] == "aroll" and v["section"] == "小镇"]
        self.assertLess(talk[0]["rec_out"], shown[0]["rec_in"] + 1)
        self.assertIn("风景段落：峡湾", plan_cut.explain(plan))
        for a, b in plan["speech"]:  # nobody talks over it, so the music stays up
            self.assertFalse(a < shown[-1]["rec_out"] and shown[0]["rec_in"] < b, (a, b))

    def test_showcase_with_too_little_footage_is_not_padded(self):
        index = trip()
        index["clips"].append(take("FJORD", "2026-02-13T11:00:00+00:00", secs=5, tags=("fjord",), order=130, day=3,
                                   movable=True, light="day"))
        story = plan_story()
        story["chapters"]["小镇"].insert(1, {"showcase": "峡湾", "tags": ["fjord"], "dur_s": 20})
        with self.assertRaisesRegex(plan_cut.PlanError, "showcase 峡湾"):
            self.build(index, story, brief=plan_brief(duration_s=200))

    def test_showcase_cannot_pull_events_from_another_time(self):
        index = trip()
        index["clips"].append(take("FJORD", "2026-02-13T11:00:00+00:00", secs=60, tags=("fjord",), order=130, day=3))
        story = plan_story()
        story["chapters"]["小镇"].insert(1, {"showcase": "峡湾", "tags": ["fjord"], "dur_s": 12})
        with self.assertRaisesRegex(plan_cut.PlanError, "showcase 峡湾"):
            self.build(index, story, brief=plan_brief())

    def test_chosen_picture_for_a_line_goes_on_v2(self):
        story = plan_story()
        story["chapters"]["小镇"][1]["cover"] = [{"clip": "T03", "in_s": 5.0, "out_s": 8.0}]
        plan = self.build(trip(), story, brief=plan_brief())
        chosen = [v for v in plan["video"] if v.get("chosen")]
        self.assertEqual([v["clip"] for v in chosen], ["T03"])
        self.assertEqual(chosen[0]["track"], 2)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])

    def test_chosen_picture_from_another_time_needs_a_reason_or_movable(self):
        story = plan_story()
        story["chapters"]["小镇"][1]["cover"] = [{"clip": "M03", "in_s": 5.0, "out_s": 8.0}]
        with self.assertRaisesRegex(plan_cut.PlanError, r"cover\[0\].*M03"):
            self.build(trip(), story)
        story["chapters"]["小镇"][1]["cover"][0]["out_of_order"] = "提前看一眼第三天的山"
        self.assertTrue(self.build(trip(), story, brief=plan_brief())["checks"]["ok"])

    def test_chosen_broll_from_another_time_needs_a_reason(self):
        story = plan_story()
        story["chapters"]["小镇"].insert(1, {"broll": True, "clip": "M03", "in_s": 5.0, "out_s": 9.0})
        with self.assertRaisesRegex(plan_cut.PlanError, "overlap in time|out_of_order"):
            self.build(trip(), story)

    def test_order_free_lifts_the_clock_when_the_style_says_so(self):
        style = dict(self.style, structure={**self.style["structure"], "order": "free"})
        plan = self.build(self.thin_day1(), style=style)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        self.assertNotIn("order", plan)
        self.assertIn("LATER", {v["clip"] for v in plan["video"] if v["section"] == "小镇"})

    def test_untimed_clips_warn_and_follow_file_order(self):
        index = trip()
        for c in index["clips"]:
            c["shot_at"] = None
        for i, c in enumerate(sorted(index["clips"], key=lambda c: c["order"])):
            c["order"] = i
        plan = self.build(index, brief=plan_brief())
        self.assertTrue(any("no shooting time" in w for w in plan["warnings"]))
        self.assertEqual(len(plan["order"]["untimed"]), len(index["clips"]))

    def test_an_index_without_times_is_refused_for_chronological(self):
        index = trip()
        for c in index["clips"]:
            del c["shot_at"], c["order"]
        with self.assertRaisesRegex(plan_cut.PlanError, "aggregate.py footage"):
            self.build(index)

    def test_chronological_without_a_story_writes_no_story_fields(self):
        style = dict(self.style, structure={**self.style["structure"], "order": "chronological"})
        plan = plan_cut.build(style, trip(), plan_brief(), None)
        self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
        self.assertNotIn("dialogue", plan)
        self.assertNotIn("role", plan["video"][0])
        self.assertEqual(plan["video"][0]["audio"], "keep")
        for v in plan["video"]:
            if v["section"] == "小镇":
                self.assertEqual(v["day"], 1, v["id"])

    def test_check_plan_catches_a_plan_gone_wrong_by_hand(self):
        def corrupted(fn):
            plan = self.build(trip(), brief=plan_brief())
            self.assertTrue(plan["checks"]["ok"], plan["checks"]["problems"])
            fn(plan)
            return plan_cut.check_plan(plan)

        def later_day(plan):
            v = next(v for v in plan["video"] if v["section"] == "小镇" and v["role"] == "broll")
            v["t_src"] += 2 * 86400
        res = corrupted(later_day)
        self.assertFalse(res["ok"])
        self.assertTrue(any("shot outside the time" in p for p in res["problems"]), res["problems"])

        def fake_borrow(plan):
            v = next(v for v in plan["video"] if v["section"] == "小镇" and v["role"] == "broll")
            v["borrowed"] = True
        res = corrupted(fake_borrow)
        self.assertTrue(any("not marked movable" in p for p in res["problems"]), res["problems"])

        def cut_a_sentence(plan):
            plan["dialogue"][1]["src_in"] += 300
        res = corrupted(cut_a_sentence)
        self.assertTrue(any("cuts into a sentence" in p for p in res["problems"]), res["problems"])

        def reach_next_door(plan):
            d = plan["dialogue"][1]
            d["src_out"] = d["clear"][1] + 60
        res = corrupted(reach_next_door)
        self.assertTrue(any("sentence next door" in p for p in res["problems"]), res["problems"])

        def stray_cutaway(plan):
            v = next(v for v in plan["video"] if v["role"] == "cutaway")
            v["rec_in"], v["rec_out"] = 0, plan["timeline"]["duration_frames"]
        res = corrupted(stray_cutaway)
        self.assertTrue(any("not over one A-roll" in p for p in res["problems"]), res["problems"])

        def overlap_a1(plan):
            plan["dialogue"][1]["rec_in"] = plan["dialogue"][0]["rec_in"]
        res = corrupted(overlap_a1)
        self.assertTrue(any("overlaps on A1" in p for p in res["problems"]), res["problems"])

        def hole_in_v1(plan):
            v = next(v for v in plan["video"] if v["track"] == 1)
            v["rec_out"] -= 1
        res = corrupted(hole_in_v1)
        self.assertTrue(any("gap/overlap" in p for p in res["problems"]), res["problems"])


if __name__ == "__main__":
    unittest.main()
