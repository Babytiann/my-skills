#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for paths.py. Run: python3 -I -B test_paths.py"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths  # noqa: E402


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        os.environ[paths.ENV_ROOT] = str(self.root)
        self.p = paths.load()

    def tearDown(self):
        os.environ.pop(paths.ENV_ROOT, None)
        self.tmp.cleanup()

    def test_env_overrides_root(self):
        self.assertEqual(self.p.root, self.root)
        self.assertEqual(self.p.dirs["cache"], "cache")

    def test_round_trip(self):
        a = self.p.abs("projects/trip/footage/A001.MP4")
        self.assertEqual(a, self.root / "projects" / "trip" / "footage" / "A001.MP4")
        self.assertEqual(self.p.rel(a), "projects/trip/footage/A001.MP4")

    def test_refuses_absolute_and_escape(self):
        for bad in ("/Users/x/a.mp4", "~/a.mp4", "../a.mp4", "projects/../../a", "C:/a.mp4", ""):
            with self.assertRaises(ValueError, msg=bad):
                self.p.abs(bad)
        with self.assertRaises(ValueError):
            self.p.rel("/somewhere/else.mp4")

    def test_names(self):
        self.assertEqual(self.p.style("极夜纪录").name, "极夜纪录")
        self.assertEqual(self.p.style("links/polar-night"), self.root / "styles" / "links" / "polar-night")
        self.assertEqual(self.p.style_refs("misc"), self.root / "styles" / "misc" / "refs")
        for bad in ("a/b/c", "../x", "links/", "/x", ".h/x", "x/..", "links/refs", "links/stills",
                    "..", ".hidden", "", "a b"):
            with self.assertRaises(ValueError, msg=bad):
                self.p.style(bad)
        for bad in ("a/b", "links/polar-night"):  # refs, projects and groups stay flat
            with self.assertRaises(ValueError, msg=bad):
                paths.check_name(bad)
            with self.assertRaises(ValueError, msg=bad):
                self.p.style_refs(bad)
            with self.assertRaises(ValueError, msg=bad):
                self.p.ref(bad)
            with self.assertRaises(ValueError, msg=bad):
                self.p.project(bad)

    def test_style_file_names_existing_packs(self):
        base = Path(self.tmp.name) / "skill"
        for name in ("links/polar-night", "misc/one"):
            d = base / "styles" / name
            d.mkdir(parents=True)
            (d / "style.json").write_text("{}", encoding="utf-8")
        self.assertEqual(paths.style_file("links/polar-night", base),
                         base / "styles" / "links" / "polar-night" / "style.json")
        with self.assertRaises(ValueError) as cm:
            paths.style_file("links", base)  # no creator-level pack yet: list its sub-packs only
        self.assertIn("links/polar-night", str(cm.exception))
        self.assertNotIn("misc/one", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            paths.style_file("polar-night", base)  # an old flat name: list everything
        self.assertIn("links/polar-night", str(cm.exception))
        self.assertIn("misc/one", str(cm.exception))
        with self.assertRaises(ValueError):
            paths.style_file("../x", base)

    def test_moving_root_keeps_relative_paths(self):
        rel = "projects/trip/plan/cut_plan.json"
        before = self.p.abs(rel)
        os.environ[paths.ENV_ROOT] = str(Path(self.tmp.name) / "nas" / "vlog")
        after = paths.load().abs(rel)
        self.assertNotEqual(before, after)
        self.assertTrue(str(after).endswith(rel))

    def test_init_creates_layout(self):
        made = paths.init(self.p, style="s1", project="p1")
        self.assertTrue((self.root / "styles" / "s1" / "luts").is_dir())
        self.assertTrue((self.root / "projects" / "p1" / "footage").is_dir())
        self.assertGreater(len(made), 5)
        self.assertEqual(paths.init(self.p, style="s1", project="p1"), [])

    def test_check_finds_absolute_paths(self):
        d = Path(self.tmp.name)
        (d / "ok.json").write_text(json.dumps({"source": "projects/a.mp4",
                                               "url": "https://www.bilibili.com/video/BV1"}))
        (d / "bad.json").write_text(json.dumps({"clips": [{"source": "/Users/me/a.mp4"}]}))
        (d / "bad.md").write_text("素材在 /Volumes/NAS/vlog/a.mp4\n")
        (d / "ok.md").write_text("见 https://b23.tv/x 与 projects/a/plan/\n")
        hits = paths.check([d])
        self.assertEqual(len(hits), 2, hits)
        self.assertTrue(any("bad.json" in h and "$.clips[0].source" in h for h in hits))
        self.assertTrue(any("bad.md:1" in h for h in hits))

    def test_check_lists_ocr_readings_as_notes(self):
        d = Path(self.tmp.name) / "ocr"
        d.mkdir()
        reading = "/bpm"  # on-screen text in a film, read by OCR
        (d / "measure_video.json").write_text(json.dumps({
            "text": {"tracks": [{"text": reading}], "transcript": [{"text": reading}]},
            "strips": {"titles": [{"text": reading}]},
        }))
        (d / "labels.json").write_text(json.dumps({"titles": [{"text": reading}]}))
        (d / "ocr.json").write_text(json.dumps({"samples": [{"text": reading}]}))
        self.assertEqual(paths.check([d]), [])
        hits, notes = paths.scan([d])
        self.assertEqual(hits, [])
        self.assertEqual(len(notes), 5, notes)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(paths.main(["check", str(d)]), 0)
        self.assertIn("0 absolute path(s) found, 5 OCR note(s)", err.getvalue())

    def test_check_keeps_real_paths_in_ocr_fields_and_other_fields(self):
        d = Path(self.tmp.name) / "mixed"
        d.mkdir()
        (d / "measure_video.json").write_text(json.dumps({
            "text": {"tracks": [{"text": "/Users/me/a.mp4"}, {"text": "~/a.mp4"}, {"text": "C:/a.mp4"}]},
            "video": {"path": "/bpm"},
            "strips": {"events": [{"text": "/bpm"}]},
        }))
        hits, notes = paths.scan([d])
        self.assertEqual(notes, [])
        self.assertEqual(len(hits), 5, hits)
        self.assertEqual(paths.check([d]), hits)
        self.assertEqual(paths.find_absolute(d / "measure_video.json"), hits)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(paths.main(["check", str(d)]), 1)

class SpokenTextTest(unittest.TestCase):
    def test_a_line_starting_with_a_slash_is_a_note_not_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in (("index.json", {"clips": [{"sentences": [[0, 9, "/斜杠开头的一句话"]],
                                                          "shots": [{"sentences": [[0, 9, "/斜杠开头的一句话"]]}]}]}),
                               ("plan.json", {"dialogue": [{"sentences": [[0, 9, "/斜杠开头的一句话"]]}]})):
                f = Path(tmp) / name
                f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                hits, notes = paths.scan([f])
                self.assertEqual(hits, [], name)
                self.assertTrue(notes, name)

    def test_a_real_path_in_a_line_is_still_a_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "index.json"
            f.write_text(json.dumps({"clips": [{"sentences": [[0, 9, "/Users/someone/x.mp4"]]}]}), encoding="utf-8")
            self.assertTrue(paths.scan([f])[0])


if __name__ == "__main__":
    unittest.main()
