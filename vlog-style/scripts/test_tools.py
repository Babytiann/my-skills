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
import datetime as dt
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import aggregate  # noqa: E402
import cleanup  # noqa: E402
import compare  # noqa: E402
import duck  # noqa: E402
import grade  # noqa: E402
import paths  # noqa: E402
import photo_clips  # noqa: E402
import plan_cut  # noqa: E402
import prep  # noqa: E402
import story  # noqa: E402
import subs  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"

# Stands in for opencli: JSON for metadata calls; `download` records the timeout it was given.
FAKE_OPENCLI = """#!/bin/sh
[ "$2" = download ] || { echo '{}'; exit 0; }
echo "$OPENCLI_BROWSER_COMMAND_TIMEOUT" > "$FAKE_SEEN"
[ "$FAKE_DOWNLOAD" = fail ] || { : > "$5/a.mp4"; exit 0; }
echo progress >&2
echo '{"error":{"code":"TIMEOUT"}}'
exit 75
"""


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


class DownloadTest(TempRoot):
    def fetch(self, **env):
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        fake = bin_dir / "opencli"
        fake.write_text(FAKE_OPENCLI)
        fake.chmod(0o755)
        self.seen = Path(self.tmp.name) / "seen"
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_SEEN": str(self.seen), **env}
        with mock.patch.dict(os.environ, env):
            if prep.OPENCLI_TIMEOUT_ENV not in env:
                os.environ.pop(prep.OPENCLI_TIMEOUT_ENV, None)
            return prep.fetch_bilibili(self.P, "BV1test", self.P.ref("BV1test"), "1080p")

    def test_download_outlasts_opencli_command_timeout(self):
        src, _ = self.fetch()
        self.assertEqual(src.name, "a.mp4")
        self.assertEqual(self.seen.read_text().strip(), str(prep.DOWNLOAD_TIMEOUT_S))

    def test_a_leftover_temp_file_is_not_taken_for_the_download(self):
        src = self.P.ref("BV1test") / "src"
        src.mkdir(parents=True)
        (src / "v.mp4").write_bytes(b"x" * 10)
        (src / "v.temp.mp4").write_bytes(b"x" * 11)  # interrupted while embedding the cover
        found, _ = self.fetch()
        self.assertEqual(found.name, "v.mp4")
        self.assertFalse(self.seen.exists())  # the finished file is kept, no second download

    def test_a_timeout_set_by_the_user_wins(self):
        self.fetch(**{prep.OPENCLI_TIMEOUT_ENV: "7200"})
        self.assertEqual(self.seen.read_text().strip(), "7200")

    def test_failed_download_reports_exit_code_and_both_streams(self):
        with self.assertRaises(SystemExit) as ctx:
            self.fetch(FAKE_DOWNLOAD="fail")
        for want in ("exit 75", "TIMEOUT", "progress"):
            self.assertIn(want, str(ctx.exception))


class ClipIdTest(unittest.TestCase):
    def test_unique_stems_keep_their_name_wherever_they_sit(self):
        self.assertEqual(prep.assign_ids(["C0001.MP4", "day1/C0002.MP4"], {}),
                         {"C0001.MP4": "C0001", "day1/C0002.MP4": "C0002"})

    def test_same_name_on_two_cards_takes_the_folder(self):
        self.assertEqual(prep.assign_ids(["cardA/C0001.MP4", "cardB/C0001.MP4"], {}),
                         {"cardA/C0001.MP4": "cardA_C0001", "cardB/C0001.MP4": "cardB_C0001"})

    def test_a_file_directly_in_footage_keeps_the_bare_stem(self):
        self.assertEqual(prep.assign_ids(["C0001.MP4", "cardB/C0001.MP4"], {}),
                         {"C0001.MP4": "C0001", "cardB/C0001.MP4": "cardB_C0001"})

    def test_live_photo_pair_in_one_folder_takes_the_extension(self):
        self.assertEqual(prep.assign_ids(["IMG_0001.HEIC", "IMG_0001.MOV"], {}),
                         {"IMG_0001.HEIC": "IMG_0001_heic", "IMG_0001.MOV": "IMG_0001_mov"})

    def test_case_only_difference_is_a_collision(self):
        ids = prep.assign_ids(["a/clip.mp4", "b/CLIP.mp4"], {})
        self.assertEqual(ids, {"a/clip.mp4": "a_clip", "b/CLIP.mp4": "b_CLIP"})

    def test_nested_cards_with_one_folder_name_stay_distinct(self):
        ids = prep.assign_ids(["x/card/C1.MP4", "y/card/C1.MP4"], {})
        self.assertEqual(len({i.casefold() for i in ids.values()}), 2, ids)

    def test_an_existing_proxy_keeps_its_id_when_a_second_card_arrives(self):
        ids = prep.assign_ids(["cardA/C0001.MP4", "cardB/C0001.MP4"], {"C0001": "cardA/C0001.MP4"})
        self.assertEqual(ids, {"cardA/C0001.MP4": "C0001", "cardB/C0001.MP4": "cardB_C0001"})

    def test_a_proxy_whose_file_is_gone_keeps_its_id_reserved(self):
        ids = prep.assign_ids(["new/C0001.MP4"], {"C0001": "old/C0001.MP4"})
        self.assertEqual(ids, {"new/C0001.MP4": "new_C0001"})


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class FootageScanTest(TempRoot):
    def clip(self, rel, seconds=0.2):
        path = self.footage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size=160x90:rate=30:duration={seconds}", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(path)], check=True)

    def scan(self):
        code, out, err = self.run_main(prep.main, ["footage", "--project", "t"])
        self.assertEqual(code, 0, err)
        return {c["id"]: c["source"] for c in json.loads(out)["clips"]}

    def setUp(self):
        super().setUp()
        paths.init(self.P, project="t")
        self.footage = self.P.project("t") / "footage"

    def test_same_name_clips_on_two_cards_are_both_kept(self):
        self.clip("cardA/C0001.MP4", 0.2)
        self.clip("cardB/C0001.MP4", 0.4)
        clips = self.scan()
        self.assertEqual(clips, {"cardA_C0001": "projects/t/footage/cardA/C0001.MP4",
                                 "cardB_C0001": "projects/t/footage/cardB/C0001.MP4"})
        lengths = {}
        for clip_id in clips:
            one = json.loads((self.P.project("t") / "proxies" / clip_id / "prep.json").read_text(encoding="utf-8"))
            lengths[clip_id] = one["probe"]["duration_s"]
        self.assertLess(lengths["cardA_C0001"], lengths["cardB_C0001"])  # each proxy is its own clip

    def test_hidden_files_and_folders_are_skipped(self):
        self.clip("cardA/C0001.MP4")
        (self.footage / "cardA" / "._C0001.MP4").write_bytes(b"\x00\x05\x16\x07 AppleDouble, not a video")
        (self.footage / ".Spotlight-V100").mkdir()
        (self.footage / ".Spotlight-V100" / "store.mp4").write_bytes(b"not a video")
        self.assertEqual(list(self.scan()), ["C0001"])

    def test_adding_a_card_later_keeps_the_ids_already_in_use(self):
        self.clip("cardA/C0001.MP4")
        self.assertEqual(list(self.scan()), ["C0001"])
        self.clip("cardB/C0001.MP4", 0.4)
        self.assertEqual(self.scan(), {"C0001": "projects/t/footage/cardA/C0001.MP4",
                                       "cardB_C0001": "projects/t/footage/cardB/C0001.MP4"})


class CleanupTest(TempRoot):
    def test_list_and_dry_run(self):
        ref = self.P.ref("BV1test")
        ref.mkdir(parents=True)
        (ref / "proxy.mp4").write_bytes(b"x" * 2048)
        code, out, _ = self.run_main(cleanup.main, ["list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["path"], "cache/refs/BV1test")
        self.assertEqual(json.loads(out)[0]["state"], "unfinished")
        code, _, err = self.run_main(cleanup.main, ["trash", "BV1test"])  # only a proxy: may still be running
        self.assertEqual(code, 2)
        self.assertIn("--unfinished", err)
        code, out, err = self.run_main(cleanup.main, ["trash", "BV1test", "--unfinished"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1test", out)
        self.assertTrue(ref.exists())  # dry run moves nothing

    def test_all_skips_unfinished_references(self):
        self.measured_ref()
        self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links", "--yes"])
        running = self.P.ref("BV1running")
        running.mkdir(parents=True)
        (running / "measure_video.json").write_text("{}", encoding="utf-8")  # measured, not labelled yet
        (running.parent / ".DS_Store").write_bytes(b"x")
        code, out, err = self.run_main(cleanup.main, ["list"])
        self.assertEqual({r["path"]: r["state"] for r in json.loads(out)},
                         {"cache/refs/BV1keep": "kept", "cache/refs/BV1running": "unfinished"})
        code, out, err = self.run_main(cleanup.main, ["trash", "--all"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1keep", out)
        self.assertNotIn("BV1running", out)
        self.assertIn("skip: cache/refs/BV1running", err)
        self.assertNotIn(".DS_Store", out)
        code, out, err = self.run_main(cleanup.main, ["trash", "--all", "--unfinished", "--no-keep"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1keep", out)
        self.assertIn("would trash cache/refs/BV1running", out)

    def test_in_use_is_never_trashed(self):
        ref = self.measured_ref()
        self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links", "--yes"])
        with mock.patch.object(cleanup, "busy", return_value=["123"]):
            code, _, err = self.run_main(cleanup.main, ["trash", "BV1keep", "--unfinished", "--no-keep", "--yes"])
            self.assertEqual(code, 2)
            self.assertIn("pid 123", err)
            code, out, err = self.run_main(cleanup.main, ["trash", "--all", "--unfinished", "--no-keep", "--yes"])
            self.assertEqual(code, 0)
            self.assertIn("skip: cache/refs/BV1keep", err)
            self.assertIn("nothing to trash", out)
            code, out, _ = self.run_main(cleanup.main, ["list"])
            self.assertEqual(json.loads(out)[0]["state"], "busy")
        self.assertTrue(ref.exists())

    def test_rewatch_of_a_kept_reference_is_not_unfinished(self):
        ref = self.measured_ref()
        self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links", "--yes"])
        (ref / "labels.json").unlink()  # downloaded again to look at one section, never labelled
        code, out, _ = self.run_main(cleanup.main, ["trash", "BV1keep"])
        self.assertEqual(code, 0)
        self.assertIn("would trash cache/refs/BV1keep", out)

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


class StoryReadbackTest(unittest.TestCase):
    """V2 and A1 are read back like everything else."""

    @classmethod
    def setUpClass(cls):
        import test_plan_cut as tp
        style = dict(json.loads((FIX / "style.json").read_text(encoding="utf-8")))
        style["audio"] = {**style["audio"], "broll_ambient_db": -18}
        cls.style = style
        cls.plan = plan_cut.build(style, tp.trip(), tp.plan_brief(), None, None, tp.plan_story())

    def readback(self, s0=108000):
        video: dict = {}
        audio: dict = {}
        for v in self.plan["video"]:
            video.setdefault(str(v["track"]), []).append(
                {"start": s0 + v["rec_in"], "end": s0 + v["rec_out"], "source_start": v["src_in"]})
        for t in self.plan["titles"]:
            video.setdefault(str(t["track"]), []).append({"start": s0 + t["rec_in"], "end": s0 + t["rec_out"]})
        for d in [*self.plan["dialogue"], *self.plan["ambient"]]:
            audio.setdefault(str(d["track"]), []).append(
                {"start": s0 + d["rec_in"], "end": s0 + d["rec_out"], "source_start": d["src_in"]})
        return {"start_frame": s0, "video": video, "audio": audio}

    def test_exact_build_passes(self):
        res = compare.check_readback(self.readback(), self.plan, self.style)
        self.assertTrue(res["ok"], res["issues"])
        total = len(self.plan["video"]) + len(self.plan["titles"]) + len(self.plan["dialogue"]) + len(self.plan["ambient"])
        self.assertEqual(res["items_exact"], total)

    def test_a_missing_or_moved_cutaway_fails(self):
        rb = self.readback()
        rb["video"]["2"][0]["start"] += 1
        self.assertTrue(any(i.startswith("V2 #1") for i in compare.check_readback(rb, self.plan)["issues"]))
        rb = self.readback()
        del rb["video"]["2"][0]
        self.assertTrue(any(i.startswith("V2:") for i in compare.check_readback(rb, self.plan)["issues"]))

    def test_dialogue_that_is_missing_moved_or_wrongly_sourced_fails(self):
        rb = self.readback()
        del rb["audio"]["1"]
        self.assertTrue(any(i.startswith("A1:") for i in compare.check_readback(rb, self.plan)["issues"]))
        rb = self.readback()
        rb["audio"]["1"][0]["end"] += 1  # audio has no transition to hide in
        self.assertTrue(any(i.startswith("A1 #1") for i in compare.check_readback(rb, self.plan)["issues"]))
        rb = self.readback()
        rb["audio"]["1"][0]["source_start"] += 10
        self.assertTrue(any("source in" in i for i in compare.check_readback(rb, self.plan)["issues"]))

    def test_ambience_has_its_own_track(self):
        rb = self.readback()
        del rb["audio"]["4"]
        self.assertTrue(any(i.startswith("A4:") for i in compare.check_readback(rb, self.plan)["issues"]))

    def test_rhythm_is_what_the_viewer_sees(self):
        res = compare.check_readback(self.readback(), self.plan, self.style)
        v1 = [v for v in self.plan["video"] if v["track"] == 1]
        self.assertGreater(res["rhythm"]["got"]["n"], len(v1))  # cutaways add cuts
        self.assertEqual(res["rhythm"]["got"]["median"], res["rhythm"]["plan"]["median"])


class SubsTest(unittest.TestCase):
    PLAN = {"timeline": {"fps": 29.97}, "dialogue": [
        {"src_in": 0, "src_out": 1200, "rec_in": 300, "rec_out": 900, "source_fps": 59.94,
         "sentences": [[120, 360, "今天到了小镇。"], [420, 720, "这里的路很窄，但是人特别热情，大家都在外面晒太阳聊天"]]}]}

    def test_59_94_source_on_a_29_97_timeline(self):
        rows = subs.timed_lines(self.PLAN, None)
        self.assertEqual(rows[0][:2], (360.0, 480.0))  # source 120..360 is timeline 300+60..300+180
        cues = subs.cues_from(rows, 29.97, 18, {})
        self.assertAlmostEqual(cues[0]["a"], 360 / 29.97, places=3)
        self.assertAlmostEqual(cues[0]["b"], 480 / 29.97, places=3)
        self.assertEqual(cues[0]["text"], "今天到了小镇")  # the closing 。 goes

    def test_clipped_to_what_the_item_plays(self):
        plan = {"timeline": {"fps": 30.0}, "dialogue": [
            {"src_in": 100, "src_out": 200, "rec_in": 0, "rec_out": 100, "source_fps": 30.0,
             "sentences": [[50, 150, "前半句听不到"], [150, 400, "后半句也听不完"]]}]}
        self.assertEqual(subs.timed_lines(plan, None), [(0.0, 50.0, "前半句听不到"), (50.0, 100.0, "后半句也听不完")])

    def test_without_dialogue_it_reads_kept_sound_from_the_index(self):
        plan = {"timeline": {"fps": 30.0}, "video": [
            {"clip": "A", "src_in": 0, "src_out": 300, "rec_in": 0, "rec_out": 300, "source_fps": 30.0, "audio": "keep"},
            {"clip": "A", "src_in": 300, "src_out": 600, "rec_in": 300, "rec_out": 600, "source_fps": 30.0, "audio": "mute"}]}
        index = {"clips": [{"id": "A", "sentences": [[30, 60, "听得到"], [330, 360, "静音的听不到"]]}]}
        self.assertEqual(subs.timed_lines(plan, index), [(30.0, 60.0, "听得到")])

    def test_long_lines_break_at_punctuation_then_by_length(self):
        self.assertEqual(subs.split_line("这里的路很窄，但是人特别热情，大家都在外面晒太阳聊天", 18),
                         ["这里的路很窄，但是人特别热情", "大家都在外面晒太阳聊天"])
        self.assertEqual(subs.split_line("一" * 40, 18), ["一" * 18, "一" * 18, "一" * 4])
        self.assertEqual(subs.split_line("真的吗？太好了！", 18), ["真的吗？", "太好了！"])  # never run two sentences together

    def test_time_is_shared_by_character_count(self):
        cues = subs.cues_from([(0.0, 300.0, "这里的路很窄，" + "一" * 15)], 30.0, 18, {})
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0]["b"] - cues[0]["a"], 10 * 6 / 21, places=3)

    def test_cues_never_overlap_and_are_readable(self):
        rows = [(0.0, 3.0, "快"), (3.0, 6.0, "很快"), (6.0, 60.0, "正常长度的一句话"), (60.0, 90.0, "下一句")]
        cues = subs.cues_from(rows, 30.0, 18, {})
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["b"], b["a"] + 1e-9)
        for c in cues:
            self.assertGreaterEqual(c["b"] - c["a"], subs.MIN_CUE_S - 1e-9, c)

    def test_corrections_survive_regenerating(self):
        cues = subs.cues_from([(0.0, 90.0, "欢迎来到特罗姆色")], 30.0, 18, {"特罗姆色": "特罗姆瑟"})
        self.assertEqual(cues[0]["text"], "欢迎来到特罗姆瑟")

    def test_foreign_lines_take_translations_and_the_rest_are_listed(self):
        plan = {"timeline": {"fps": 30.0}, "video": [], "dialogue": [
            {"src_in": 0, "src_out": 300, "rec_in": 0, "rec_out": 300, "source_fps": 30.0,
             "sentences": [[0, 60, "今天到了小镇。"], [90, 150, "How old is this shop?", "en"],
                           [180, 240, "Fifty years.", "en"]]}]}
        tr = {"How old is this shop?": "这家店开了多久？"}
        self.assertEqual([t for _, _, t in subs.timed_lines(plan, None, tr)],
                         ["今天到了小镇。", "这家店开了多久？", "Fifty years."])
        self.assertEqual(subs.untranslated(plan, None, tr), [("en", "Fifty years.")])
        with tempfile.TemporaryDirectory() as tmp:
            pj, out, fx = Path(tmp) / "plan.json", Path(tmp) / "subs.srt", Path(tmp) / "fixes.json"
            pj.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            fx.write_text(json.dumps({"translate": tr}, ensure_ascii=False), encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(subs.main(["--plan", str(pj), "--out", str(out), "--fixes", str(fx)]), 0)
            self.assertIn("这家店开了多久", out.read_text(encoding="utf-8"))
            self.assertIn("[en] Fifty years.", err.getvalue())

    def test_srt_text(self):
        text = subs.to_srt([{"a": 1.0, "b": 2.5, "text": "一"}, {"a": 3661.25, "b": 3662.0, "text": "二"}])
        self.assertEqual(text, "1\n00:00:01,000 --> 00:00:02,500\n一\n\n2\n01:01:01,250 --> 01:01:02,000\n二\n\n")

    def test_hand_edits_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, out = Path(tmp) / "plan.json", Path(tmp) / "subs.srt"
            plan.write_text(json.dumps(self.PLAN, ensure_ascii=False), encoding="utf-8")
            argv = ["--plan", str(plan), "--out", str(out)]
            with redirect_stderr(io.StringIO()):
                self.assertEqual(subs.main(argv), 0)
                out.write_text("改过", encoding="utf-8")
                self.assertEqual(subs.main(argv), 1)
                self.assertEqual(out.read_text(encoding="utf-8"), "改过")
                self.assertEqual(subs.main([*argv, "--force"]), 0)
            self.assertIn("今天到了小镇", out.read_text(encoding="utf-8"))


class StoryDumpTest(unittest.TestCase):
    def test_lists_clips_by_day_with_real_time_and_breaks(self):
        import test_plan_cut as tp
        index = tp.trip()
        text = story.dump(index)
        self.assertLess(text.index("第 1 天"), text.index("第 3 天"))
        self.assertLess(text.index("### TALK1"), text.index("### TALK3"))
        self.assertRegex(text, r"\[00:02\.00–00:06\.\d\d\] 今天到了小镇")
        self.assertIn("02-11 09:00:00", text)

    def test_marks_lines_that_are_not_chinese(self):
        import test_plan_cut as tp
        index = tp.trip()
        index["clips"][0]["sentences"].append([400, 460, "How far is the lake?", "en"])
        self.assertRegex(story.dump(index), r"\] \[en\] How far is the lake\?")

    def test_says_when_times_are_missing(self):
        import test_plan_cut as tp
        index = tp.trip()
        index["clips"][0].update(shot_at=None, day=None)
        self.assertIn("没有拍摄时间", story.dump(index))
        self.assertIn("时间未知", story.dump(index))


class FootageTimeTest(TempRoot):
    def test_tags_and_offsets_shape_shot_at_order_and_days(self):
        day1, day2 = "2026-02-11T09:00:00+00:00", "2026-02-12T09:00:00+00:00"
        clock = {"device": "DJI", "probe": {"shot_at": day2, "shot_at_source": "format.creation_time", "device": "DJI"}}
        self.assertEqual(aggregate.clip_time(clock, {}, {})["shot_at"], day2)
        moved = aggregate.clip_time(clock, {}, {"DJI": 3600})
        self.assertEqual(moved["shot_at"], "2026-02-12T10:00:00+00:00")
        typed = aggregate.clip_time(clock, {"shot_at": day1}, {"DJI": 3600})
        self.assertEqual((typed["shot_at"], typed["shot_at_source"]), (day1, "tags"))  # the user's word, no offset
        none = aggregate.clip_time({"probe": {"shot_at": None, "shot_at_source": None}}, {}, {})
        self.assertEqual((none["shot_at"], none["shot_at_source"]), (None, "filename"))
        clips = [{"source": "b", "shot_at": day2, "frames": 300, "fps": 30.0},
                 {"source": "z", "shot_at": None, "frames": 300, "fps": 30.0},
                 {"source": "a", "shot_at": day1, "frames": 300, "fps": 30.0},
                 {"source": "c", "shot_at": "2026-02-11T09:30:00+00:00", "frames": 300, "fps": 30.0}]
        aggregate.order_clips(clips)
        self.assertEqual([c["source"] for c in clips], ["b", "z", "a", "c"])  # the array itself stays put
        self.assertEqual({c["source"]: c["order"] for c in clips}, {"a": 0, "c": 1, "b": 2, "z": 3})
        self.assertEqual({c["source"]: c["day"] for c in clips}, {"a": 1, "c": 1, "b": 2, "z": None})

    def test_sentences_reach_the_index_in_source_frames(self):
        paths.init(self.P, project="t")
        d = self.P.project("t") / "proxies" / "A"
        d.mkdir(parents=True)
        shot = lambda i, a, b: {"i": i, "start": a, "end": b, "motion": {"type": "static", "shake": 0.1},  # noqa: E731
                                "sharp_raw": 1.0, "exposure_ok": True, "best": a}
        files = {
            "prep.json": {"source": {"path": "projects/t/footage/A.MP4"},
                          "probe": {"fps": 59.94, "width": 3840, "height": 2160, "hdr": False,
                                    "shot_at": "2026-02-11T09:00:00+00:00", "shot_at_source": "format.creation_time"}},
            "measure_video.json": {"scan": {"shots": [shot(0, 0, 300), shot(1, 300, 600)]}, "cuts": {"n_frames": 600}},
            "measure_audio.json": {"speech": [[1.0, 4.0], [4.5, 9.0]], "transcript": {"segments": [
                {"t_in": 1.0, "t_out": 4.0, "text": "今天到了小镇。"},
                {"t_in": 4.5, "t_out": 8.0, "text": "How old is this shop?", "lang": "en"},
                {"t_in": 8.1, "t_out": 9.0, "text": "很好。", "lang": "zh"}]}}}
        for name, data in files.items():
            (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        code, _, err = self.run_main(aggregate.main, ["footage", "--project", "t"])
        self.assertEqual(code, 0, err)
        clip = json.loads((self.P.project("t") / "index" / "footage.json").read_text(encoding="utf-8"))["clips"][0]
        self.assertEqual(clip["sentences"], [[60, 240, "今天到了小镇。"], [270, 480, "How old is this shop?", "en"],
                                             [486, 539, "很好。"]])
        self.assertEqual(clip["shots"][0]["sentences"], [[60, 240, "今天到了小镇。"], [270, 300, "How old is this shop?", "en"]])
        self.assertEqual(clip["shots"][1]["sentences"], [[300, 480, "How old is this shop?", "en"], [486, 539, "很好。"]])

    def test_camera_with_no_clock_counts_as_undated(self):
        self.assertIsNone(prep.parse_time("1904-01-01T00:00:00Z"))
        self.assertIsNone(prep.parse_time("1970-01-01T00:00:00.000000Z"))
        self.assertEqual(prep.parse_time("2026-02-11T08:13:21+0100"), "2026-02-11T07:13:21+00:00")
        fmt = {"creation_time": "2026-02-11T09:00:00.000000Z", "com.apple.quicktime.creationdate": "2026-02-11T08:00:00+0100"}
        self.assertEqual(prep.shot_time(fmt, {}), ("2026-02-11T07:00:00+00:00", "quicktime.creationdate"))
        self.assertEqual(prep.shot_time({}, {"creation_time": "2026-02-11T09:00:00Z"})[1], "stream.creation_time")
        self.assertEqual(prep.shot_time({}, {}), (None, None))

    def test_device_ranges_side_by_side(self):
        clips = [{"device": "A", "shot_at": "2026-02-11T09:00:00+00:00", "shot_at_source": "format.creation_time"},
                 {"device": "A", "shot_at": "2026-02-11T10:00:00+00:00", "shot_at_source": "format.creation_time"},
                 {"device": "B", "shot_at": None, "shot_at_source": None}]
        a, b = prep.device_ranges(clips)
        self.assertEqual((a["clips"], a["first"], a["last"], a["undated"]),
                         (2, "2026-02-11T09:00:00+00:00", "2026-02-11T10:00:00+00:00", 0))
        self.assertEqual((b["first"], b["undated"], b["sources"]), (None, 1, []))


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
def exif_jpeg(path: Path, when: str | None, offset: str | None = None, orientation: int = 1,
              make: str = "Apple", model: str = "iPhone 16 Pro", size: str = "160x120") -> Path:
    """A JPEG carrying the EXIF a camera writes: make, model, orientation, DateTimeOriginal, OffsetTimeOriginal."""
    plain = path.with_suffix(".plain.jpg")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}",
                    "-frames:v", "1", str(plain)], check=True)

    def ascii(tag, text):
        b = text.encode() + b"\0"
        return tag, 2, len(b), b

    def ifd(entries, base):
        n, data_at = len(entries), base + 2 + len(entries) * 12 + 4
        body, extra = b"", b""
        for tag, typ, cnt, val in sorted(entries):
            if isinstance(val, int):
                body += struct.pack(">HHII", tag, typ, cnt, val)
            elif len(val) <= 4:
                body += struct.pack(">HHI", tag, typ, cnt) + val.ljust(4, b"\0")
            else:
                body += struct.pack(">HHII", tag, typ, cnt, data_at + len(extra))
                extra += val
        return struct.pack(">H", n) + body + b"\0\0\0\0" + extra

    sub = ([ascii(0x9003, when)] if when else []) + ([ascii(0x9011, offset)] if offset else [])
    top = [ascii(0x010F, make), ascii(0x0110, model), (0x0112, 3, 1, struct.pack(">H", orientation))]
    size0 = len(ifd(top + [(0x8769, 4, 1, 0)], 8))
    tiff = b"MM\0*" + struct.pack(">I", 8) + ifd(top + [(0x8769, 4, 1, 8 + size0)], 8) + ifd(sub, 8 + size0)
    app1 = b"Exif\0\0" + tiff
    jpg = plain.read_bytes()
    path.write_bytes(jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpg[2:])
    plain.unlink()
    return path


class PhotoScanTest(TempRoot):
    def setUp(self):
        super().setUp()
        paths.init(self.P, project="t")
        self.footage = self.P.project("t") / "footage"
        (self.footage / "cardA").mkdir(parents=True)
        (self.footage / "trip").mkdir()
        tagged = exif_jpeg(self.footage / "cardA" / "src.jpg", "2026:02:11 21:40:00", "+01:00", orientation=6)
        subprocess.run(["sips", "-s", "format", "heic", str(tagged), "--out", str(self.footage / "cardA" / "IMG_0001.HEIC")],
                       check=True, capture_output=True)
        tagged.unlink()
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=30:duration=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(self.footage / "cardA" / "IMG_0001.MOV")], check=True)
        exif_jpeg(self.footage / "trip" / "IMG_0002.JPG", "2026:02:11 09:00:00", make="SONY", model="ILCE-7M4")
        exif_jpeg(self.footage / "trip" / "IMG_0004.JPG", None, "+08:00")  # exported: zone kept, time stripped
        (self.footage / "trip" / "DSC0003.ARW").write_bytes(b"raw")
        exif_jpeg(self.footage / "trip" / ".hidden.jpg", "2026:02:11 10:00:00")

    def test_photos_get_time_zone_device_preview_and_upright_size(self):
        code, out, err = self.run_main(prep.main, ["footage", "--project", "t"])
        self.assertEqual(code, 0, err)
        rep = json.loads(out)
        kinds = {c["id"]: c.get("kind") for c in rep["clips"]}
        self.assertEqual(kinds, {"IMG_0001_heic": "photo", "IMG_0001_mov": None, "IMG_0002": "photo",
                                 "IMG_0004": "photo"})
        one = {c["id"]: c for c in rep["clips"]}
        self.assertEqual(one["IMG_0001_heic"]["shot_at"], "2026-02-11T20:40:00+00:00")
        self.assertEqual(one["IMG_0001_heic"]["shot_at_source"], "exif.DateTimeOriginal")
        self.assertEqual(one["IMG_0001_heic"]["device"], "Apple iPhone 16 Pro")
        local = dt.datetime(2026, 2, 11, 9, 0).astimezone().astimezone(dt.timezone.utc).isoformat()
        self.assertEqual((one["IMG_0002"]["shot_at"], one["IMG_0002"]["shot_at_source"]), (local, "exif.no_zone"))
        self.assertEqual(one["IMG_0002"]["device"], "SONY ILCE-7M4 (no time zone)")
        self.assertIn("no time zone", err)
        self.assertEqual(rep["raw_skipped"], ["trip/DSC0003.ARW"])
        self.assertEqual((one["IMG_0004"]["shot_at"], one["IMG_0004"]["shot_at_source"]), (None, None))
        self.assertIn("no shooting time", err)
        self.assertIn("IMG_0004", err)
        heic = json.loads((self.P.project("t") / "proxies" / "IMG_0001_heic" / "prep.json").read_text(encoding="utf-8"))
        self.assertEqual((heic["probe"]["width"], heic["probe"]["height"]), (120, 160))  # stored sideways
        self.assertTrue((self.P.project("t") / "proxies" / "IMG_0001_heic" / "preview.jpg").exists())
        devices = {d["device"] for d in rep["devices"]}
        self.assertIn("SONY ILCE-7M4 (no time zone)", devices)

    def test_photos_reach_the_index_with_tags_and_clock_offsets(self):
        self.run_main(prep.main, ["footage", "--project", "t"])
        proj = self.P.project("t")
        (proj / "index" / "tags.json").write_text(json.dumps({"clips": {
            "IMG_0002": {"tags": ["harbour"], "shot_type": "wide", "light": "day", "movable": True}}}), encoding="utf-8")
        (proj / "plan").mkdir(exist_ok=True)
        (proj / "plan" / "brief.json").write_text(json.dumps({"clock_offsets_s": {"SONY ILCE-7M4 (no time zone)": 3600}}),
                                                 encoding="utf-8")
        code, _, err = self.run_main(aggregate.main, ["footage", "--project", "t"])
        self.assertEqual(code, 0, err)
        index = json.loads((proj / "index" / "footage.json").read_text(encoding="utf-8"))
        self.assertEqual(index["clips"], [])  # the Live Photo's video has no measure_video.json yet
        photos = {x["id"]: x for x in index["photos"]}
        sony = photos["IMG_0002"]
        local = dt.datetime(2026, 2, 11, 10, 0).astimezone().astimezone(dt.timezone.utc)
        self.assertEqual(dt.datetime.fromisoformat(sony["shot_at"]), local)
        self.assertEqual((sony["tags"], sony["shot_type"], sony["light"], sony["movable"]), (["harbour"], "wide", "day", True))
        self.assertEqual(sony["preview"], "projects/t/proxies/IMG_0002/preview.jpg")
        self.assertEqual({x["kind"] for x in index["photos"]}, {"photo"})
        self.assertEqual(sorted(x["order"] for x in index["photos"]), [0, 1, 2])
        self.assertIsNone(photos["IMG_0004"]["day"])


class PhotoClipsTest(TempRoot):
    @staticmethod
    def probe(path: Path) -> dict:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                            "stream=width,height,nb_read_frames,r_frame_rate,color_transfer,color_primaries,color_space,"
                            "color_range", "-of", "json", str(path)], capture_output=True, text=True, check=True)
        return json.loads(r.stdout)["streams"][0]

    @staticmethod
    def picture_width(path: Path, w: int, h: int) -> int:
        raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        lit = [x for x in range(w) if max(raw[y * w + x] for y in range(h)) > 40]
        return lit[-1] - lit[0] + 1

    @staticmethod
    def item(pid, src, rec_in, n):
        return {"id": f"v{rec_in}", "section": "x", "kind": "photo", "clip": pid, "photo": f"projects/t/footage/{src}",
                "source": f"projects/t/proxies/{pid}/photo.mp4", "source_fps": 29.97, "src_in": 0, "src_out": n,
                "rec_in": rec_in, "rec_out": rec_in + n, "speed": 1.0, "track": 1}

    def test_clips_fit_the_timeline_stand_upright_and_are_kept_while_current(self):
        paths.init(self.P, project="t")
        proj = self.P.project("t")
        footage = proj / "footage"
        exif_jpeg(footage / "side.jpg", "2026:02:11 21:40:00", "+01:00", orientation=6, size="320x240")
        plain = exif_jpeg(footage / "w.jpg", "2026:02:11 21:41:00", "+01:00", size="320x240")
        subprocess.run(["sips", "-s", "format", "heic", str(plain), "--out", str(footage / "wide.HEIC")],
                       check=True, capture_output=True)
        plain.unlink()
        plan = {"timeline": {"fps": 29.97, "width": 640, "height": 360},
                "video": [self.item("S", "side.jpg", 0, 45), self.item("W", "wide.HEIC", 45, 30),
                          self.item("W", "wide.HEIC", 75, 60)]}
        done = photo_clips.make(self.P, plan)
        self.assertEqual({d["clip"]: (d["frames"], d["made"]) for d in done}, {"S": (45, True), "W": (60, True)})
        for pid, frames, width in (("S", 45, 270), ("W", 60, 480)):  # 3:4 upright and 4:3, fitted to 360 high
            clip = proj / "proxies" / pid / "photo.mp4"
            info = self.probe(clip)
            self.assertEqual((info["width"], info["height"], int(info["nb_read_frames"]), info["r_frame_rate"]),
                             (640, 360, frames, "30000/1001"))
            self.assertEqual((info["color_transfer"], info["color_primaries"], info["color_space"], info["color_range"]),
                             ("bt709", "bt709", "bt709", "tv"))
            self.assertLessEqual(abs(self.picture_width(clip, 640, 360) - width), 4, pid)
        again = photo_clips.make(self.P, plan)
        self.assertEqual({d["clip"]: d["made"] for d in again}, {"S": False, "W": False})
        plan["video"][2]["src_out"] = plan["video"][2]["rec_out"] - plan["video"][2]["rec_in"] + 30
        again = photo_clips.make(self.P, plan)
        self.assertEqual({d["clip"]: (d["frames"], d["made"]) for d in again}, {"S": (45, False), "W": (90, True)})
        self.assertTrue((footage / "side.jpg").exists() and (footage / "wide.HEIC").exists())


class ShotTimeScanTest(TempRoot):
    def clip(self, rel, *meta):
        path = self.P.project("t") / "footage" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=30:duration=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        *meta, str(path)], check=True)
        return path

    def setUp(self):
        super().setUp()
        paths.init(self.P, project="t")

    def test_time_and_device_reach_prep_json_and_the_summary(self):
        self.clip("dji/D1.MP4", "-metadata", "creation_time=2026-02-11T09:00:00Z")
        self.clip("sony/S1.MP4")
        code, out, err = self.run_main(prep.main, ["footage", "--project", "t"])
        self.assertEqual(code, 0, err)
        rep = json.loads(out)
        d1 = next(c for c in rep["clips"] if c["id"] == "D1")
        self.assertEqual((d1["shot_at"], d1["shot_at_source"]), ("2026-02-11T09:00:00+00:00", "format.creation_time"))
        self.assertEqual({d["device"] for d in rep["devices"]}, {"dji", "sony"})  # no make or model: the card's folder
        self.assertIn("device dji: 1 clip(s)", err)

    def test_an_older_prep_json_gets_its_time_without_a_new_proxy(self):
        self.clip("A.MP4", "-metadata", "creation_time=2026-02-11T09:00:00Z")
        self.run_main(prep.main, ["footage", "--project", "t"])
        d = self.P.project("t") / "proxies" / "A"
        old = json.loads((d / "prep.json").read_text(encoding="utf-8"))
        for k in ("shot_at", "shot_at_source", "device"):
            del old["probe"][k]
        del old["device"]
        (d / "prep.json").write_text(json.dumps(old), encoding="utf-8")
        made = (d / "proxy.mp4").stat().st_mtime_ns
        self.run_main(prep.main, ["footage", "--project", "t"])
        now = json.loads((d / "prep.json").read_text(encoding="utf-8"))
        self.assertEqual(now["probe"]["shot_at"], "2026-02-11T09:00:00+00:00")
        self.assertEqual((d / "proxy.mp4").stat().st_mtime_ns, made)


class GradeChapterTest(unittest.TestCase):
    """grade.py's stdlib half: which chapter an item takes its grade from, bounds, the Resolve argument."""

    def plan(self):
        sec = lambda name, role, a, b, w=None: {"name": name, "role": role, "rec_in": a, "rec_out": b,  # noqa: E731
                                               **({"window": w} if w else {})}
        item = lambda i, s, a, b, **kw: {"id": i, "section": s, "clip": "c", "rec_in": a, "rec_out": b,  # noqa: E731
                                         "track": 1, **kw}
        return {"sections": [sec("hook", "hook", 0, 100), sec("A", "chapter", 100, 400, [1000, 2000]),
                             sec("B", "chapter", 400, 700, [3000, 4000]), sec("ending", "ending", 700, 800)],
                "video": [item("v001", "hook", 0, 50, t_src=3500), item("v006", "hook", 50, 100, t_src=1500),
                          item("v002", "A", 100, 400), item("v003", "B", 400, 700),
                          item("v004", "ending", 700, 720, t_src=2100), item("v005", "ending", 720, 780)]}

    def test_items_take_their_chapter(self):
        got = grade.chapter_of(self.plan())
        self.assertEqual(got["v002"], "A")  # in a chapter section
        self.assertEqual(got["v003"], "B")
        self.assertEqual(got["v001"], "B")  # hook shot inside B's window
        self.assertEqual(got["v006"], "A")
        self.assertEqual(got["v004"], "A")  # outside every window: nearest in time
        self.assertEqual(got["v005"], "B")  # no time: nearest on the timeline

    def test_no_chapter_sections_is_one_group(self):
        plan = self.plan()
        plan["sections"] = [{"name": "main", "role": "intro", "rec_in": 0, "rec_out": 800}]
        self.assertEqual(set(grade.chapter_of(plan).values()), {"all"})

    def test_stills_skip_photos_and_spread(self):
        plan = {"video": [{"id": f"v{i:03d}", "rec_in": i * 10, "rec_out": i * 10 + 10, "track": 1 + i % 2}
                          for i in range(20)]}
        plan["video"].append({"id": "p001", "kind": "photo", "rec_in": 5, "rec_out": 15, "track": 1})
        owner = {v["id"]: "A" for v in plan["video"]}
        got = [v["id"] for v in grade.pick_items(plan, owner, "A")]
        self.assertEqual(len(got), grade.MAX_STILLS)
        self.assertNotIn("p001", got)
        self.assertEqual((got[0], got[-1]), ("v000", "v019"))

    def test_each_channel_clamped(self):
        p = {**grade.NEUTRAL, "slope": 1.3, "r_slope": 0.06, "offset": 0.05, "r_offset": 0.02}
        cdl, clamped = grade.compose(p)
        self.assertEqual(cdl["slope"][0], 1.3)
        self.assertEqual(cdl["offset"][0], 0.05)
        self.assertEqual(set(clamped), {"slope.R", "offset.R"})
        self.assertEqual(grade.at_bound(p, grade.bounds_for(False, True)), ["slope", "offset", "r_slope", "r_offset"])

    def test_night_only_darkens(self):
        b = grade.bounds_for(True, True)
        self.assertEqual(b["power"], (1.0, 1.0))
        self.assertEqual(b["slope"][1], 1.0)
        self.assertEqual(b["offset"][1], 0.0)
        self.assertTrue(all(b[k] == (0.0, 0.0) for k in grade.CAST_PARAMS))
        cdl, _ = grade.compose({**grade.NEUTRAL, "b_slope": 0.06, "r_offset": 0.02}, night=True)
        self.assertLessEqual(max(cdl["slope"]), 1.0)
        self.assertLessEqual(max(cdl["offset"]), 0.0)
        self.assertEqual(grade.at_bound({**grade.NEUTRAL}, b), [])  # slope 1 is the night cap, not a bound hit
        no_cast = grade.bounds_for(False, False)
        self.assertTrue(all(no_cast[k] == (0.0, 0.0) for k in grade.CAST_PARAMS))
        self.assertEqual(no_cast["slope"], grade.BOUNDS["slope"])

    def test_resolve_argument_strings(self):
        cdl, _ = grade.compose({**grade.NEUTRAL, "slope": 1.12346, "offset": -0.0123, "power": 0.9, "sat": 1.1})
        r = grade.resolve_args(cdl)
        self.assertEqual(set(r), {"NodeIndex", "Slope", "Offset", "Power", "Saturation"})
        self.assertTrue(all(isinstance(v, str) for v in r.values()))
        self.assertEqual(r["NodeIndex"], "1")
        self.assertEqual(r["Slope"], "1.1235 1.1235 1.1235")
        self.assertEqual(r["Offset"], "-0.0123 -0.0123 -0.0123")
        self.assertEqual(r["Power"], "0.9000 0.9000 0.9000")
        self.assertEqual(r["Saturation"], "1.1000")


if __name__ == "__main__":
    unittest.main()
