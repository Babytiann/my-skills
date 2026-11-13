#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for cleanup.py, duck.py, mix.py and compare.py readback, normalize and final. Run: python3 -I -B test_tools.py

The render and film tests need ffmpeg on PATH and are skipped without it.
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
import mix  # noqa: E402
import paths  # noqa: E402
import photo_clips  # noqa: E402
import plan_cut  # noqa: E402
import prep  # noqa: E402
import schema  # noqa: E402
import stats  # noqa: E402
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


def stream_tags(path: Path) -> list[str]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_tag_string,nb_frames",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return r.stdout.split()


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class Hvc1RemuxTest(unittest.TestCase):
    """Bilibili's HEVC tagged hev1 can't be opened by AVFoundation, so vision_ocr read nothing."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name: str, *codec: str) -> Path:
        out = self.dir / name
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
                        *codec, str(out)], check=True)
        return out

    def test_hev1_download_is_retagged_with_the_same_frames(self):
        src = self.make("a.mp4", "-c:v", "libx265", "-x265-params", "log-level=none", "-tag:v", "hev1")
        self.assertEqual(stream_tags(src), ["hev1,10"])
        self.assertEqual(prep.hvc1_remux(src), "hev1->hvc1")
        self.assertEqual(stream_tags(src), ["hvc1,10"])
        self.assertEqual([p.name for p in self.dir.iterdir()], ["a.mp4"])  # no temp file left behind
        self.assertIsNone(prep.hvc1_remux(src))  # second run: nothing to do

    def test_other_files_are_left_alone(self):
        avc = self.make("b.mp4", "-c:v", "libx264")
        before = avc.read_bytes()
        self.assertIsNone(prep.hvc1_remux(avc))
        self.assertEqual(avc.read_bytes(), before)
        junk = self.dir / "c.mp4"
        junk.write_bytes(b"x" * 10)  # like the fake downloads in DownloadTest
        self.assertIsNone(prep.hvc1_remux(junk))


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

    def test_keep_blanks_captions_that_ocr_sorted_as_titles(self):
        # learnings 2026-11-06: a caption line that OCR called a title kept its text in three places
        # (labels titles, text.tracks, strips.titles) because keep only blanked non-title tracks.
        ref = self.measured_ref()
        mv = json.loads((ref / "measure_video.json").read_text(encoding="utf-8"))
        mv["text"]["tracks"].append({"i": 2, "kind": "title", "text": "这一句其实是字幕", "t_in": 9.0})
        mv["strips"] = {"titles": [{"track": 0, "text": "北极圈"}, {"track": 2, "text": "这一句其实是字幕"}]}
        (ref / "measure_video.json").write_text(json.dumps(mv, ensure_ascii=False), encoding="utf-8")
        labels = {"ref": "BV1keep", "chapters": [], "titles": [
            {"track": 0, "text": "北极圈", "role": "location", "note": None},
            {"track": 2, "text": "这一句其实是字幕", "role": "other", "note": "subtitle_misread"}]}
        (ref / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        code, _, _ = self.run_main(cleanup.main, ["keep", "BV1keep", "--style", "links", "--yes"])
        self.assertEqual(code, 0)
        kept = self.P.style_refs("links") / "BV1keep"
        blob = "".join(p.read_text(encoding="utf-8") for p in kept.iterdir())
        self.assertNotIn("这一句其实是字幕", blob)
        self.assertNotIn("旁白第一句", blob)
        self.assertEqual(blob.count("北极圈"), 3)  # a real title keeps its text everywhere
        kl = json.loads((kept / "labels.json").read_text(encoding="utf-8"))
        self.assertEqual([t["text"] for t in kl["titles"]], ["北极圈", ""])
        self.assertEqual(kl["titles"][1]["note"], "subtitle_misread")  # the label itself stays

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


class WiderPictureTest(unittest.TestCase):
    """A reference wider than the 16:9 deliverable: a 2:1 file, or 16:9 with bars baked in."""

    @staticmethod
    def ref(source_size, baked=None):
        return {"mv": {"video": {"source_size": source_size}, "scan": {"letterbox": {"aspect": baked}}}}

    def test_a_wider_file_maps_onto_the_picture_between_the_bars(self):
        k, pad = aggregate.frame_map(self.ref([1920, 960]), (3840, 2160))
        self.assertAlmostEqual(k, 8 / 9)
        self.assertAlmostEqual(pad, 1 / 18)
        # a 2:1 subtitle measured at y 0.963, 5% high, sits at 0.912 and 4.4% of the 16:9 frame
        self.assertAlmostEqual(pad + k * 0.963, 0.9116, places=4)
        self.assertAlmostEqual(k * 0.05, 0.0444, places=4)
        self.assertEqual(aggregate.picture_aspect(self.ref([1920, 960]), (3840, 2160)), 2.0)

    def test_a_16_9_file_is_left_alone_and_baked_bars_keep_their_aspect(self):
        for size in ([1920, 1080], [1916, 1080]):  # 1916 wide: rounding, not a wider picture
            self.assertEqual(aggregate.frame_map(self.ref(size), (3840, 2160)), (1.0, 0.0))
            self.assertIsNone(aggregate.picture_aspect(self.ref(size), (3840, 2160)))
        baked = self.ref([1920, 1080], baked=2.35)
        self.assertEqual(aggregate.frame_map(baked, (3840, 2160)), (1.0, 0.0))  # the bars are in the measured frame
        self.assertEqual(aggregate.picture_aspect(baked, (3840, 2160)), 2.35)

    def test_only_a_majority_sets_the_letterbox(self):
        self.assertEqual(aggregate.pooled_letterbox([2.0]), (2.0, None))
        self.assertEqual(aggregate.pooled_letterbox([2.0, 2.0, None]), (2.0, None))
        self.assertEqual(aggregate.pooled_letterbox([None, None]), (None, None))
        lb, note = aggregate.pooled_letterbox([2.0, 2.0, 2.0] + [None] * 9)
        self.assertIsNone(lb)
        self.assertIn("3 of 12", note)


def fake_measurements(dur=60.0, fps=30.0, shot_s=2.0, beat_s=0.5, beat_shift=0.0, agrees=True):
    """measure_video.json / measure_audio.json just complete enough for aggregate.analyse_ref.

    Cuts fall every shot_s seconds; beats every beat_s seconds, shifted by beat_shift, so with
    no shift every cut is on a beat and with a quarter-beat shift none is.
    """
    n = int(round(dur / shot_s))
    shots = [{"i": k, "t0": k * shot_s, "t1": (k + 1) * shot_s, "len_s": shot_s,
              "start": round(k * shot_s * fps), "end": round((k + 1) * shot_s * fps),
              "motion": {"type": "static", "zoom": 1.0, "shake": 0.05}} for k in range(n)]
    cuts = [{"t": k * shot_s, "frame": round(k * shot_s * fps), "kind": "hard", "frames": 1} for k in range(1, n)]
    mv = {"video": {"fps": fps, "duration_s": dur, "source_fps": fps, "source_size": [1920, 1080]},
          "cuts": {"cuts": cuts},
          "scan": {"shots": shots, "events": [], "letterbox": {"top": 0.0, "bottom": 0.0, "aspect": None}},
          "look": {"summary": {}, "per_shot": [], "hue_hist": None},
          "text": {"tracks": [], "subtitle_band": None},
          "strips": {"events": [], "titles": [], "moves": []}}
    beats = [round(k * beat_s + beat_shift, 4) for k in range(int(dur / beat_s))]
    ma = {"beats": {"engine": "beat_this", "beats": beats, "nonspeech_spans": [[0.0, dur]], "nonspeech_s": dur,
                    "confidence": "ok", "bpm": 60 / beat_s},
          "tempo_check": {"librosa_bpm": 60 / beat_s if agrees else 60 / beat_s * 1.3, "agrees": agrees},
          "loudness": {"integrated_lufs": -14.0}, "duck": {"db": 6.0}}
    return mv, ma


def write_ref(d: Path, mv: dict, ma: dict, labels: dict | None = None, meta: dict | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, data in (("measure_video.json", mv), ("measure_audio.json", ma), ("labels.json", labels),
                       ("meta.json", meta)):
        if data is not None:
            (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def whip_event(i: int, cut: int, t: float, whip_dir: str = "right") -> dict:
    return {"i": i, "kind": "whip", "candidates": ["whip"], "cut": cut, "t": t, "frame": round(t * 30),
            "frames": 1, "evidence": {"blur": 0.3, "whip_dir": whip_dir}}


class DirectionTest(unittest.TestCase):
    """learnings 2026-11-13: a whip candidate labelled dissolve carried the whip's direction into the dissolve."""

    def test_only_whips_and_wipes_keep_a_direction(self):
        e = {"evidence": {"whip_dir": "right"}}
        self.assertIsNone(aggregate.cut_direction("dissolve", {"type": "dissolve", "direction": None}, e))
        self.assertIsNone(aggregate.cut_direction("dissolve", {"type": "dissolve", "direction": "left"}, e))
        self.assertEqual(aggregate.cut_direction("whip", {"type": "whip", "direction": "left"}, e), "left")
        self.assertEqual(aggregate.cut_direction("whip", None, e), "right")  # measured, not labelled
        self.assertIsNone(aggregate.cut_direction("wipe", None, e))         # whip_dir is a whip's measurement
        self.assertEqual(aggregate.cut_direction("wipe", {"type": "wipe", "direction": "up"}, e), "up")

    def test_draft_writes_no_direction_on_a_dissolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            mv, ma = fake_measurements()
            mv["strips"]["events"] = [whip_event(0, 0, 2.0), whip_event(1, 1, 4.0, "left"), whip_event(2, 2, 6.0)]
            labels = {"events": [{"i": 0, "type": "dissolve", "frames": 9, "direction": None, "sure": True},
                                 {"i": 1, "type": "whip", "frames": 8, "direction": "left", "sure": True}]}
            ref = aggregate.analyse_ref(write_ref(Path(tmp) / "r", mv, ma, labels))
            style = aggregate.build_style("x", [ref], (3840, 2160))
        types = {t["type"]: t for t in style["transitions"]}
        self.assertNotIn("direction", types["dissolve"])
        self.assertIn(types["whip"]["direction"], ("left", "right"))
        self.assertEqual(schema.check_style(style), [])


class BeatConfidenceTest(unittest.TestCase):
    """learnings 2026-11-13: 13 cuts and two tempo engines that disagree still drafted confidence ok."""

    @staticmethod
    def judged(n_cuts=40, hit=0.3, base=0.07, conf="ok", agrees=True):
        verdict = "too_few_cuts" if n_cuts < stats.MIN_BEAT_CUTS else "not_on_beat"
        return aggregate.beat_reliable({"align": {"n_cuts": n_cuts, "hit_rate": hit, "baseline": base, "verdict": verdict},
                                        "ma": {"beats": {"confidence": conf, "bpm": 93.17, "nonspeech_s": 50},
                                               "tempo_check": {"librosa_bpm": 107.7, "agrees": agrees}}})

    def test_each_reference_is_judged_on_its_own(self):
        self.assertEqual(self.judged(), (True, "ok"))
        ok, why = self.judged(n_cuts=13, hit=0.0, base=0.055, agrees=False)  # the Lawrence vlog
        self.assertFalse(ok)
        self.assertIn("too_few_cuts", why)
        self.assertIn("disagree", why)
        self.assertFalse(self.judged(conf="uncertain")[0])
        self.assertFalse(self.judged(hit=0.0556, base=0.0673, agrees=False)[0])  # Svalbard: 0.83x
        ok, why = self.judged(hit=0.209, base=0.0743, agrees=False)               # Kula Kangri: 2.8x
        self.assertTrue(ok)
        self.assertIn("follow beat_this", why)

    def test_most_references_must_be_reliable(self):
        with tempfile.TemporaryDirectory() as tmp:
            def ref(name, **kw):
                return aggregate.analyse_ref(write_ref(Path(tmp) / name, *fake_measurements(**kw)))
            on, on2 = ref("on"), ref("on2")
            off = ref("off", beat_shift=0.25, agrees=False)  # misses every beat, engines disagree
            few = ref("few", dur=30.0)                        # 14 cuts
            size = (3840, 2160)
            self.assertEqual(aggregate.build_style("x", [on], size)["beat"]["confidence"], "ok")
            self.assertEqual(aggregate.build_style("x", [few], size)["beat"]["confidence"], "uncertain")
            half = aggregate.build_style("x", [on, off], size)
            self.assertEqual(half["beat"]["confidence"], "uncertain")
            self.assertEqual(half["beat"]["hit_rate"], 0.5)  # uncertain: numbers from every reference
            most = aggregate.build_style("x", [on, on2, off], size)
            self.assertEqual(most["beat"]["confidence"], "ok")
            self.assertEqual(most["beat"]["hit_rate"], 1.0)  # ok: numbers from the reliable ones only
            self.assertEqual([b["reliable"] for b in most["evidence"]["beat"]], [True, True, False])


HK_LOOK = {"L01": [4.905, 2.539], "L50": [40.267, 16.489], "L99": [83.661, 6.363], "Lstd": [19.49, 4.835],
           "shadow": [0.202, 0.161], "chroma": [5.937, 2.498], "sh_a": [0.125, 0.687], "sh_b": [-2.531, 2.593],
           "mid_a": [-0.024, 1.352], "mid_b": [-5.054, 6.101], "hi_a": [-0.586, 0.843], "hi_b": [-5.492, 2.765],
           "vignette": [1.034, 0.323]}  # BV1eoQwYPEeu look.summary (median, mad)
JP_LOOK = {"L01": [4.37, 1.916], "L50": [38.33, 13.37], "L99": [85.749, 4.339], "Lstd": [21.668, 3.082],
           "shadow": [0.242, 0.13], "chroma": [4.364, 1.506], "sh_a": [0.078, 0.969], "sh_b": [0.016, 2.454],
           "mid_a": [-0.891, 0.921], "mid_b": [-1.047, 3.797], "hi_a": [-1.141, 0.937], "hi_b": [-1.586, 2.898],
           "vignette": [0.959, 0.29]}  # BV1Rj421S7c4


def summary_of(look: dict) -> dict:
    return {k: {"median": m, "mad": d} for k, (m, d) in look.items()}


class MergeTest(unittest.TestCase):
    """learnings 2026-11-13: pooling took one film's words, wrote stroke true for every pack and mixed card kinds."""

    def test_two_films_keep_the_look_keys_they_agree_on(self):
        look, dropped = aggregate.agreeing_look({"hk": summary_of(HK_LOOK), "jp": summary_of(JP_LOOK)})
        # the keys the hand-merged lawrence pack kept on 2026-11-12
        self.assertEqual(set(look), {"L01", "L50", "L99", "Lstd", "shadow", "vignette", "sh_a", "mid_a", "hi_a"})
        self.assertEqual(set(dropped), {"chroma", "sh_b", "mid_b", "hi_b"})
        self.assertEqual(dropped["mid_b"]["values"], {"hk": -5.054, "jp": -1.047})
        self.assertAlmostEqual(look["L50"]["median"], 39.299, delta=0.0011)
        self.assertAlmostEqual(look["L50"]["mad"], 14.93, delta=0.0011)
        alone, none = aggregate.agreeing_look({"hk": summary_of(HK_LOOK)})
        self.assertEqual((len(alone), none), (13, {}))  # one film keeps its whole card

    def test_four_or_more_films_use_the_band(self):
        films = {f"f{i}": summary_of({"L50": [40 + i, 5.0], "mid_b": [-2.0 + i, 1.0]}) for i in range(4)}
        films["f4"] = summary_of({"L50": [60.0, 5.0], "mid_b": [0.0, 1.0]})  # one bright film
        look, dropped = aggregate.agreeing_look(films)
        self.assertNotIn("L50", look)  # 60 sits outside 42 ± 5
        self.assertIn("median ± MAD", dropped["L50"]["rule"])
        self.assertNotIn("mid_b", look)  # -2..2 around 0 with a MAD of 1
        films = {f"f{i}": summary_of({"L50": [40 + i, 5.0], "mid_b": [-2.0 + i, 3.0]}) for i in range(4)}
        films["f4"] = summary_of({"L50": [44.0, 5.0], "mid_b": [0.0, 3.0]})  # a wider MAD takes them all in
        look, _ = aggregate.agreeing_look(films)
        self.assertEqual(set(look), {"L50", "mid_b"})

    def test_free_text_is_kept_only_when_the_films_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            def ref(name, summary, reject):
                labels = {"grade": {"summary": summary, "grain": "light"}, "audio": {"mood": "慢板钢琴"},
                          "selection": {"opening": "航拍远景", "reject": reject},
                          "effects": [{"type": "grain", "global": True}]}
                return aggregate.analyse_ref(write_ref(Path(tmp) / name, *fake_measurements(), labels))
            a, b = ref("a", "冷蓝低饱和", ["自拍", "口播"]), ref("b", "近中性偏暖", ["自拍", "地图"])
            err = io.StringIO()
            with redirect_stderr(err):
                pooled = aggregate.build_style("x", [a, b], (3840, 2160))
            single = aggregate.build_style("x", [a], (3840, 2160))
        self.assertEqual(pooled["grade"]["summary"], "")
        self.assertEqual(pooled["grade"]["grain"], {"strength": "light"})
        self.assertEqual(pooled["audio"]["mood"], "慢板钢琴")
        self.assertEqual(pooled["selection"]["opening"], "航拍远景")
        self.assertEqual(pooled["selection"]["reject"], ["自拍", "口播", "地图"])
        self.assertTrue(any(u.startswith("grade.summary") for u in pooled["evidence"]["unverified"]))
        self.assertNotIn("冷蓝低饱和", json.dumps(pooled, ensure_ascii=False))  # the words go to stderr only
        self.assertIn("冷蓝低饱和", err.getvalue())
        self.assertEqual([e["ref"] for e in pooled["effects"]], ["a", "b"])
        self.assertEqual(schema.check_style(pooled), [])
        self.assertEqual(single["grade"]["summary"], "冷蓝低饱和")
        self.assertNotIn("ref", single["effects"][0])

    @staticmethod
    def card(rid, track, y, h, x=0.5, fill="#ffffff"):
        return {"sec": (rid, 0), "track": track, "t_in": float(track), "center": [x, y], "height": h,
                "colour": {"fill": fill, "edge": "none"}}

    def test_one_film_mixing_card_kinds_is_flagged(self):
        jp = [self.card("jp", 0, 0.326, 0.0991, fill="#1e4d86"), self.card("jp", 111, 0.510, 0.2772),
              self.card("jp", 757, 0.243, 0.2427, x=0.72)]  # film title, song-name title, its reprise
        self.assertEqual(aggregate.card_spread(jp), {"jp": {"n": 3, "height": [0.0991, 0.2772], "y": [0.243, 0.51]}})
        alike = [self.card("lk", 1, 0.50, 0.062), self.card("lk", 2, 0.52, 0.060), self.card("hk", 3, 0.46, 0.18)]
        self.assertEqual(aggregate.card_spread(alike), {})  # different films may differ; one film's cards agree

    def test_each_film_is_one_voice(self):
        many = [self.card("a", k, 0.5, 0.1) for k in range(5)] + [self.card("b", 9, 0.5, 0.3)]
        self.assertAlmostEqual(aggregate.ref_median(many, lambda t: t["height"]), 0.2)
        many.append(self.card("c", 10, 0.5, 0.3))  # 5 small cards from one film, 2 big ones from two films
        self.assertIs(aggregate.ref_vote(many, lambda t: t["height"] > 0.2), True)
        unverified = []
        self.assertIsNone(aggregate.pooled_colour({"hk": [1.0, 0.267, 0.0], "jp": [1.0, 1.0, 1.0]}, "t", unverified))
        self.assertIn("#ff4400", unverified[0])
        self.assertEqual(aggregate.pooled_colour({"a": [1.0, 1.0, 1.0], "b": [0.98, 0.98, 0.96]}, "t", []),
                         [0.99, 0.99, 0.98])

    def test_subtitle_stroke_and_plate_are_measured_not_assumed(self):
        def ref(rid, n, plate=True, edge="none", fill="#fefad0"):
            samples = [{"track": k, "colour": {"fill": fill, "edge": edge, "plate": plate}} for k in range(n)]
            return {"id": rid, "mv": {"strips": {"subtitle_samples": samples}}}
        unverified = []
        out = aggregate.subtitle_look([ref("jp", 10), ref("misc", 8, fill="#ddd2ab")], unverified)
        self.assertEqual((out["plate"], out["stroke"]), (True, False))
        self.assertEqual(len(out["color"]), 3)
        self.assertEqual(unverified, [])
        out = aggregate.subtitle_look([ref("hk", 10, plate=False), ref("jp", 10)], unverified)
        self.assertNotIn("plate", out)  # the films disagree: the pack writer decides
        self.assertTrue(any("plate" in u for u in unverified))
        dark = ref("night", 10)  # six of ten over a scene too dark to show a plate: they abstain
        for s in dark["mv"]["strips"]["subtitle_samples"][:6]:
            s["colour"]["plate"] = None
        self.assertIs(aggregate.subtitle_look([dark], [])["plate"], True)
        unverified = []
        self.assertEqual(aggregate.subtitle_look([{"id": "old", "mv": {"strips": {}}}], unverified), {})
        self.assertIn("没有字幕样本", unverified[0])

    def test_a_thin_card_says_what_it_leaves_out(self):
        pooled, _ = aggregate.agreeing_look({"hk": summary_of(HK_LOOK), "jp": summary_of(JP_LOOK)})
        self.assertEqual(compare.missing_look_keys(pooled), ["chroma", "sh_b", "mid_b", "hi_b"])
        warn = grade.thin_card("lawrence", pooled, cast=True)
        for k in ("chroma", "sh_b", "mid_b", "hi_b"):
            self.assertIn(k, warn)
        self.assertIn("look_ref", warn)
        self.assertNotIn("mid_b", grade.thin_card("lawrence", pooled, cast=False))  # cast keys are not fitted
        self.assertIsNone(grade.thin_card("hk", summary_of(HK_LOOK), cast=True))


class PackOnlyEffectsTest(unittest.TestCase):
    """learnings 2026-11-13: nine effects added straight into japan-winter's style.json never reached the creator pack."""

    def test_effects_only_in_the_sub_pack_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            labelled = [{"type": "grain", "global": True}, {"type": "photo_insert", "shot": 3}]
            ref = write_ref(tmp / "refs" / "BVx", *fake_measurements(), {"effects": labelled})
            packs = tmp / "styles"

            def pack(name, ids, effects):
                (packs / name).mkdir(parents=True, exist_ok=True)
                (packs / name / "style.json").write_text(json.dumps(
                    {"sources": [{"id": i} for i in ids], "effects": effects}), encoding="utf-8")
            pack("grp/sub", ["BVx"], labelled + [{"type": "time_stamp", "shot": 1},
                                                 {"type": "picture_in_picture", "shot": 3}])
            pack("grp", ["BVx", "BVy"], [{"type": "end_card", "shot": 9}])  # a creator pack: not this ref's own
            out = tmp / "draft.json"
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = aggregate.main(["draft", "--name", "x", str(ref), "--out", str(out), "--packs", str(packs)])
            self.assertEqual(code, 0, err.getvalue())
            self.assertIn("2 effect(s) in its pack but not in labels.json", err.getvalue())
            self.assertIn("grp/sub: time_stamp @ shot 1", err.getvalue())
            self.assertIn("picture_in_picture @ shot 3  (labels call it photo_insert)", err.getvalue())
            self.assertNotIn("end_card", err.getvalue())
            style = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(any("2 条特效" in u for u in style["evidence"]["unverified"]))
            # once labels.json holds them, the draft is quiet
            (ref / "labels.json").write_text(json.dumps({"effects": labelled + [
                {"type": "time_stamp", "shot": 1}, {"type": "picture_in_picture", "shot": 3}]}), encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                aggregate.main(["draft", "--name", "x", str(ref), "--out", str(out), "--force", "--packs", str(packs)])
            self.assertNotIn("effect(s) in its pack", err.getvalue())


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


class MixItemsTest(unittest.TestCase):
    def test_story_plans_level_dialogue_and_leave_clips_silent(self):
        import test_plan_cut as tp
        style = dict(json.loads((FIX / "style.json").read_text(encoding="utf-8")))
        style["audio"] = {**style["audio"], "broll_ambient_db": -18}
        plan = plan_cut.build(style, tp.trip(), tp.plan_brief(), None, None, tp.plan_story())
        items = mix.items_of(plan)
        kinds = {k: [i for i in items if i["kind"] == k] for k in ("dialogue", "ambient", "music", "clip")}
        self.assertEqual(len(kinds["dialogue"]), len(plan["dialogue"]))
        self.assertEqual({i["track"] for i in kinds["dialogue"]}, {1})
        self.assertEqual({(i["track"], i["base_db"]) for i in kinds["ambient"]}, {(4, -18.0)})
        self.assertEqual(kinds["clip"], [])  # the clips' own sound is not on the timeline

    def test_without_a_story_the_kept_clips_are_the_sound(self):
        load = lambda n: json.loads((FIX / n).read_text(encoding="utf-8"))  # noqa: E731
        plan = plan_cut.build(load("style.json"), load("footage.json"), load("brief.json"), load("beats.json"))
        items = mix.items_of(plan)
        self.assertEqual(sorted(i["id"] for i in items if i["kind"] == "clip"),
                         sorted(v["id"] for v in plan["video"] if v["audio"] == "keep"))
        self.assertEqual([i["track"] for i in items if i["kind"] == "music"], [2])


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class MixRenderTest(TempRoot):
    def setUp(self):
        super().setUp()
        self.proj = self.P.project("p")
        for d in ("footage", "plan", "render"):
            (self.proj / d).mkdir(parents=True)

    def tone(self, name, expr, secs=6):
        """A stereo wav from an aevalsrc expression per channel ('L|R')."""
        out = self.proj / "footage" / name
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"aevalsrc={expr}:s=48000:d={secs}", "-c:a", "pcm_s24le", str(out)], check=True)
        return out

    def plan(self, dialogue):
        plan = {"timeline": {"fps": 30, "duration_frames": 180}, "video": [], "titles": [], "ambient": [],
                "audio": [{"id": "m001", "kind": "music", "source": "x", "rec_in": 0, "rec_out": 180, "track": 2}],
                "dialogue": [{"id": f"d{n:03d}", "source": f"projects/p/footage/{name}", "source_fps": 30,
                              "src_in": 30, "src_out": 150, "rec_in": 60 * (n - 1), "rec_out": 60 * n, "track": 1}
                             for n, name in enumerate(dialogue, 1)]}
        path = self.proj / "plan" / "cut_plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return plan, path

    def test_dialogue_is_brought_to_the_target(self):
        s = "sin(2*PI*300*t)"
        self.tone("loud.wav", f"0.5*{s}|0.5*{s}")
        self.tone("soft.wav", f"0.1*{s}|0.1*{s}")
        self.tone("faint.wav", f"0.0005*{s}|0.0005*{s}")
        self.tone("onemic.wav", f"0.1*{s}|0.005*{s}")
        plan, _ = self.plan(["loud.wav", "soft.wav", "faint.wav", "onemic.wav"])
        res = mix.level_dialogue(plan, self.P, -16.0, 12.0, 6.0)
        d = {i["id"]: i for i in res["items"]}
        for k in ("d001", "d002"):
            self.assertAlmostEqual(d[k]["measured_lufs"] + d[k]["base_db"], -16.0, delta=0.11)
            src = self.P.abs(plan["dialogue"][int(k[1:]) - 1]["source"])
            got = mix.measure(src, 1.0, 4.0)  # the same range, played at the new volume
            self.assertAlmostEqual(got["lufs"] + d[k]["volume_db"], -16.0, delta=0.2)
        self.assertEqual(d["d003"]["base_db"], 12.0)
        self.assertTrue(any(w.startswith("d003") and "clamped" in w for w in res["warnings"]), res["warnings"])
        self.assertTrue(any(w.startswith("d004") and "mic" in w for w in res["warnings"]), res["warnings"])
        self.assertFalse(any(w.startswith(("d001", "d002")) for w in res["warnings"]), res["warnings"])
        self.assertEqual(d["m001"]["volume_db"], 0.0)

    def test_master_lands_on_the_target_and_refines(self):
        s = "sin(2*PI*300*t)"
        self.tone("v.wav", f"0.05*{s}|0.05*{s}")
        plan, path = self.plan(["v.wav"])
        render = self.proj / "render" / "a.wav"
        shutil.copy(self.tone("mixdown.wav", f"0.05*{s}|0.05*{s}"), render)
        code, _, err = self.run_main(mix.main, ["dialogue", "--project", "p"])
        self.assertEqual(code, 0, err)
        code, _, err = self.run_main(mix.main, ["master", "--project", "p", "--render", str(render)])
        self.assertEqual(code, 0, err)
        out = json.loads((path.parent / "mix.json").read_text(encoding="utf-8"))
        self.assertEqual(out["master"]["expected"]["lufs"], -14.0)
        self.assertEqual(out["master"]["render"], "projects/p/render/a.wav")
        first = out["master"]["master_db"]
        self.assertEqual({i["id"]: i["volume_db"] for i in out["items"]}["m001"], first)
        self.assertEqual(paths.check([path.parent / "mix.json"]), [])
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(self.proj / "footage" / "mixdown.wav"),
                        "-af", f"volume={first}dB", "-c:a", "pcm_s24le", str(render)], check=True)
        self.run_main(mix.main, ["master", "--project", "p", "--render", str(render)])  # rendered with the new values
        out = json.loads((path.parent / "mix.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(out["master"]["master_db"], first, delta=0.2)
        self.assertAlmostEqual(out["master"]["measured"]["lufs"], -14.0, delta=0.2)

    def test_the_peak_wins_over_loudness(self):
        self.tone("v.wav", "0.05*sin(2*PI*300*t)|0.05*sin(2*PI*300*t)")
        plan, _ = self.plan(["v.wav"])
        hit = r"0.003*sin(2*PI*300*t)+between(t\,3\,3.02)*0.9*sin(2*PI*300*t)"  # commas escaped for lavfi
        render = self.tone("peaky.wav", f"{hit}|{hit}")
        res = mix.apply_master(mix.level_dialogue(plan, self.P, -16.0, 12.0, 6.0), plan, render, self.P, -14.0, -1.0)
        self.assertEqual(res["master"]["expected"]["true_peak_dbtp"], -1.0)
        self.assertLess(res["master"]["expected"]["lufs"], -14.0)
        self.assertTrue(any(w.startswith("master: the true peak") for w in res["warnings"]), res["warnings"])

    def test_a_peak_held_film_close_to_the_target_is_quiet(self):
        # probe #17, second master round: -14.1 LUFS at -1.0 dBTP holds the film 0.1 LU short; that is on target
        self.tone("v.wav", "0.05*sin(2*PI*300*t)|0.05*sin(2*PI*300*t)")
        plan, _ = self.plan(["v.wav"])
        start = mix.level_dialogue(plan, self.P, -16.0, 12.0, 6.0)
        for lufs, warned in ((-14.1, False), (-14.4, False), (-14.6, True)):
            got = {"lufs": lufs, "true_peak_dbtp": -1.0, "lra_lu": 1.8, "channel_rms_db": []}
            with mock.patch.object(mix, "measure", return_value=got), \
                    mock.patch.object(mix, "probe_duration", return_value=6.0):
                res = mix.apply_master(json.loads(json.dumps(start)), plan, Path("mix_r2.wav"), self.P, -14.0, -1.0)
            self.assertEqual(res["master"]["expected"], {"lufs": lufs, "true_peak_dbtp": -1.0})
            self.assertEqual(any(w.startswith("master:") for w in res["warnings"]), warned, (lufs, res["warnings"]))

    def test_a_changed_plan_or_a_short_render_is_caught(self):
        self.tone("v.wav", "0.05*sin(2*PI*300*t)|0.05*sin(2*PI*300*t)")
        plan, _ = self.plan(["v.wav"])
        res = mix.level_dialogue(plan, self.P, -16.0, 12.0, 6.0)
        short = self.tone("short.wav", "0.05*sin(2*PI*300*t)|0.05*sin(2*PI*300*t)", secs=3)
        self.assertTrue(any("render the whole timeline" in w
                            for w in mix.apply_master(res, plan, short, self.P, -14.0, -1.0)["warnings"]))
        plan["dialogue"][0]["rec_out"] += 1
        with self.assertRaises(mix.MixError):
            mix.apply_master(res, plan, short, self.P, -14.0, -1.0)


class ReadbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load = lambda n: json.loads((FIX / n).read_text(encoding="utf-8"))  # noqa: E731
        cls.style = load("style.json")
        cls.plan = plan_cut.build(cls.style, load("footage.json"), load("brief.json"), load("beats.json"))

    def readback(self, s0=86400, out_delta=None):
        """out_delta: also read back source_end, this many frames off each clip's planned source out."""
        video: dict = {}
        for v in self.plan["video"]:
            item = {"start": s0 + v["rec_in"], "end": s0 + v["rec_out"], "source_start": v["src_in"]}
            if out_delta is not None:
                item["source_end"] = v["src_out"] + out_delta
            video.setdefault(str(v["track"]), []).append(item)
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

    def test_source_out_may_read_back_one_short(self):
        res = compare.check_readback(self.readback(out_delta=-1), self.plan)  # 59.94 media does
        self.assertTrue(res["ok"], res["issues"])
        rb = self.readback(out_delta=-1)
        j = next(i for i, v in enumerate(self.plan["video"]) if v["track"] == 1 and not v["transition_out"]
                 and float(v.get("speed", 1)) == 1.0)
        rb["video"]["1"][j]["source_end"] -= 1  # two short at a hard cut: fails
        issues = compare.check_readback(rb, self.plan)["issues"]
        self.assertEqual([i for i in issues if "source out" in i and f"#{j + 1} " in i], issues)
        self.assertEqual(len(issues), 1, issues)


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
        d = self.plan["dialogue"][0]
        for off, ok in ((-1, True), (2, False)):
            rb = self.readback()
            rb["audio"]["1"][0]["source_end"] = d["src_out"] + off
            issues = compare.check_readback(rb, self.plan)["issues"]
            self.assertEqual(not issues, ok, issues)
            self.assertTrue(ok or any(i.startswith("A1 #1") and "source out" in i for i in issues), issues)

    def test_ambience_has_its_own_track(self):
        rb = self.readback()
        del rb["audio"]["4"]
        self.assertTrue(any(i.startswith("A4:") for i in compare.check_readback(rb, self.plan)["issues"]))

    def test_rhythm_is_what_the_viewer_sees(self):
        res = compare.check_readback(self.readback(), self.plan, self.style)
        v1 = [v for v in self.plan["video"] if v["track"] == 1]
        self.assertGreater(res["rhythm"]["got"]["n"], len(v1))  # cutaways add cuts
        self.assertEqual(res["rhythm"]["got"]["median"], res["rhythm"]["plan"]["median"])


class NormalizeTest(TempRoot):
    """clip_where_p13.json is a live reply (probe #13 timeline: V1 x3, V2, A1 x3, two subtitles)."""

    def setUp(self):
        super().setUp()
        self.reply = json.loads((FIX / "clip_where_p13.json").read_text(encoding="utf-8"))

    def test_groups_tracks_and_drops_what_readback_does_not_use(self):
        rb, warns = compare.normalize([self.reply], 108000)
        self.assertEqual(warns, [])
        self.assertEqual(rb["start_frame"], 108000)
        self.assertEqual({k: {t: len(v) for t, v in rb[k].items()} for k in compare.KINDS},
                         {"video": {"1": 3, "2": 1}, "audio": {"1": 3}, "subtitle": {"1": 2}})
        self.assertEqual([g["start"] for g in rb["video"]["1"]], [108000, 108300, 108600])
        self.assertEqual(rb["video"]["1"][0], {"start": 108000, "end": 108300, "name": "p5994.mp4",
                                               "source_start": 600, "source_end": 1199, "source_fps": 59.94})
        self.assertEqual(rb["subtitle"]["1"][0], {"start": 108060, "end": 108120, "name": "两秒"})
        self.assertNotIn("timeline_item_id", json.dumps(rb))

    def test_reply_shapes_and_merging(self):
        whole, _ = compare.normalize([self.reply], 108000)
        clips = self.reply["result"]["clips"]
        self.assertEqual(compare.normalize([self.reply["result"]], 108000)[0], whole)
        self.assertEqual(compare.normalize([clips], 108000)[0], whole)
        part = {**self.reply["result"], "clips": clips[:4], "match_count": 4}
        self.assertEqual(compare.normalize([part, self.reply], 108000), (whole, []))  # an item read twice counts once
        rb, warns = compare.normalize([part], 108000)
        self.assertEqual(sum(len(v) for k in compare.KINDS for v in rb[k].values()), 4)
        self.assertTrue(any("filtered" in w for w in warns), warns)
        with self.assertRaises(ValueError):
            compare.normalize([{"result": {"success": True, "items": []}}], 108000)

    def test_the_live_readback_passes_against_what_was_built(self):
        def clip(n, track, rec, src, fps, frames=300):
            return {"id": f"v{n:03d}", "track": track, "rec_in": rec, "rec_out": rec + frames, "src_in": src,
                    "src_out": src + round(frames * fps / 29.97), "source_fps": fps, "speed": 1.0, "transition_out": None}
        plan = {"timeline": {"fps": 29.97, "duration_frames": 900}, "titles": [],
                "video": [clip(1, 1, 0, 600, 59.94), clip(2, 1, 300, 600, 59.94), clip(3, 1, 600, 300, 29.97),
                          clip(4, 2, 100, 1500, 59.94, 60)],
                "dialogue": [clip(5, 1, 0, 480, 59.94), clip(6, 1, 300, 600, 59.94), clip(7, 1, 600, 300, 29.97)],
                "ambient": []}
        rb, _ = compare.normalize([self.reply], 108000)
        res = compare.check_readback(rb, plan)
        self.assertTrue(res["ok"], res["issues"])  # 59.94 source ends read back one short, and pass
        self.assertEqual(res["items_exact"], 7)
        plan["video"][3]["src_out"] += 2
        self.assertTrue(any("V2 #1 v004: source out" in i for i in compare.check_readback(rb, plan)["issues"]))

    def test_a_start_frame_past_the_first_item_warns(self):
        _, warns = compare.normalize([self.reply], 108300)
        self.assertTrue(any("before --start-frame 108300" in w for w in warns), warns)

    def test_cli_writes_a_readback_the_check_reads(self):
        out = self.root / "readback.json"
        self.root.mkdir(parents=True)
        code, stdout, err = self.run_main(compare.main, ["normalize", str(FIX / "clip_where_p13.json"),
                                                         "--start-frame", "108000", "--out", str(out)])
        self.assertEqual((code, stdout, err), (0, "", ""))
        rb = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rb, compare.normalize([self.reply], 108000)[0])
        self.assertEqual(paths.check([out]), [])
        bad = self.root / "bad.json"
        bad.write_text('{"x": 1}', encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self.run_main(compare.main, ["normalize", str(bad), "--start-frame", "0"])
        self.assertIn("not a timeline clip_where reply", str(cm.exception))


def v1(n, clip, a, b, src_in=0, track=1, **kw):
    return {"id": f"v{n:03d}", "clip": clip, "track": track, "rec_in": a, "rec_out": b, "src_in": src_in,
            "src_out": src_in + b - a, "transition_out": None, **kw}


class FinalPlanTest(unittest.TestCase):
    """compare.py final: the checks that read the plan and the measurements, not the film."""

    def plan(self):
        return {"timeline": {"fps": 30, "duration_frames": 300, "width": 640, "height": 360},
                "video": [v1(1, "a", 0, 120, transition_out={"kind": "dissolve", "frames": 12}),
                          v1(2, "b", 120, 210), v1(3, "c", 210, 270), v1(4, "c", 270, 300, src_in=60),
                          v1(5, "d", 200, 240, track=2), v1(6, "e", 280, 300, track=2)]}

    @staticmethod
    def cuts(*frames):
        return {"cuts": [{"frame": f, "t": round(f / 30, 3)} for f in frames]}

    def test_planned_cuts_are_what_the_viewer_sees(self):
        got = [(c["frame"], c["kind"], c["tol"]) for c in compare.planned_cuts(self.plan())]
        # 210 is under the V2 cutaway, 270 carries clip c on, 300 is the end
        self.assertEqual(got, [(120, "dissolve", 7), (200, "cutaway", 1), (240, "cutaway", 1), (280, "cutaway", 1)])

    def test_cuts_found_moved_missed_and_extra(self):
        plan = self.plan()
        res = compare.check_cuts(self.cuts(120, 200, 240, 280), plan)
        self.assertEqual((res["ok"], res["found"], res["missed"], res["extra"]), (True, 4, [], []))
        res = compare.check_cuts(self.cuts(125, 202, 240, 280, 160), plan)  # a dissolve's middle may sit off
        self.assertFalse(res["ok"])
        self.assertEqual(res["issues"], ["cutaway at frame 200 (0:06.67) is at 202 (+2)"])
        self.assertEqual((res["found"], res["moved"], res["extra"]), (3, 1, [160]))
        res = compare.check_cuts(self.cuts(120, 200, 240), plan)
        self.assertFalse(res["ok"])
        self.assertIn("1 of 4 planned cuts not found", res["issues"][0])
        self.assertTrue(compare.check_cuts(self.cuts(120, 200, 240), plan, missed_share=0.5)["ok"])
        self.assertFalse(compare.check_cuts({"cuts": []}, plan)["ok"])

    def test_spec(self):
        plan = self.plan()
        info = {"codec": "h264", "width": 640, "height": 360, "fps": 30.0, "duration_s": 10.0,
                "audio": {"codec": "aac", "channels": 2, "sample_rate": 48000}}
        self.assertEqual(compare.check_spec(info, plan)["issues"], [])
        bad = {**info, "width": 1920, "height": 1080, "fps": 29.97, "duration_s": 10.1, "audio": None}
        issues = compare.check_spec(bad, plan)["issues"]
        self.assertEqual(len(issues), 4, issues)
        self.assertTrue(issues[0].startswith("1920x1080") and issues[-1] == "no audio stream", issues)

    def test_srt_reads_back_what_subs_writes(self):
        cues = [{"a": 0.3, "b": 1.5, "text": "今天我们去海边"}, {"a": 3661.25, "b": 3663.0, "text": "第二行"}]
        self.assertEqual(compare.parse_srt(subs.to_srt(cues)), cues)
        hand = "1\r\n00:00:01.000 --> 00:00:02,500\r\n两行\r\n字幕\r\n\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nb\r\n"
        self.assertEqual(compare.parse_srt(hand), [{"a": 1.0, "b": 2.5, "text": "两行 字幕"},
                                                   {"a": 3.0, "b": 4.0, "text": "b"}])

    def test_subtitles_on_screen_at_their_time_and_place(self):
        cues = [{"a": 1.0, "b": 2.5, "text": "今天我们去海边"}, {"a": 4.0, "b": 5.0, "text": "这是一张老照片。"}]

        def track(a, b, text, y=0.9, h=0.05):
            return {"t_in": a, "t_out": b, "text": text, "center": [0.5, y], "height": h}
        want = {"position": [0.5, 0.9], "height_frac": 0.05}
        text = {"step": 0.25, "tracks": [track(1.1, 2.4, "今天我们去海边"), track(4.0, 5.0, "这是一张老照片")]}
        res = compare.check_subs(text, cues, want)
        self.assertEqual((res["ok"], res["read"], res["center_y"]), (True, 2, 0.9))
        text = {"step": 0.25, "tracks": [track(1.6, 2.4, "今天我们去海边", y=0.85, h=0.07), track(4.0, 5.0, "别的字")]}
        issues = compare.check_subs(text, cues, want)["issues"]
        self.assertEqual(len(issues), 4, issues)
        self.assertIn("on screen 1.60–2.40 s, planned 1.00–2.50 s", issues[0])
        self.assertIn("not read on screen (closest 「别的字」)", issues[1])
        self.assertTrue(issues[2].startswith("subtitle centre y 0.850") and "size" in issues[3], issues)
        self.assertTrue(compare.check_subs({"tracks": []}, cues, None)["issues"][0].endswith("not read on screen"))

    def test_subtitle_size_from_lines_without_low_ink(self):
        # probe #17: Simple White, PingFang SC 0.04 on 1080p; a comma makes the OCR box taller and lower
        want = {"position": [0.5, 0.955], "height_frac": 0.0444}
        rows = [(0.2, 3.07, "大家好,我们今天到了特罗姆瑟", 0.9585, 0.0521),
                (3.637, 5.672, "这里的天黑得特别早", 0.956, 0.0472),
                (5.672, 7.708, "下午两点就开始暗了", 0.956, 0.0472),
                (15.883, 19.152, "听说今晚的云不多,运气应该不错", 0.9581, 0.0560),
                (19.82, 23.223, "朋友们,接下来一起看看这里的风景", 0.9585, 0.0521)]
        cues = [{"a": a, "b": b, "text": s} for a, b, s, _, _ in rows]
        text = {"step": 0.25, "tracks": [{"t_in": a + 0.1, "t_out": b + 0.1, "text": s, "center": [0.5, y], "height": h}
                                         for a, b, s, y, h in rows]}
        res = compare.check_subs(text, cues, want)
        self.assertEqual((res["ok"], res["measured_on"], res["height"], res["center_y"]), (True, 2, 0.0472, 0.956), res)
        # the same lines one size up still fail
        big = {**text, "tracks": [{**t, "height": t["height"] * 1.25} for t in text["tracks"]]}
        self.assertIn("change the preset's size", compare.check_subs(big, cues, want)["issues"][0])
        # with commas only, all lines count and the issue says why the box may read tall
        odd = [0, 3, 4]
        res = compare.check_subs({**text, "tracks": [text["tracks"][i] for i in odd]}, [cues[i] for i in odd], want)
        self.assertEqual((res["measured_on"], res["height"]), ("all", 0.0521))
        self.assertIn("every line has a comma", res["issues"][0])

    def test_speech_against_the_subtitles(self):
        cues = [{"a": a, "b": a + 1.5, "text": t} for a, t in ((1, "今天我们去海边"), (4, "这是一张老照片"),
                                                              (7, "天气很好"), (10, "我们回家了"))]
        rows = lambda d: [{"t_in": c["a"] + d, "text": c["text"] + "，然后"} for c in cues]  # noqa: E731
        res = compare.check_sync(rows(0.1), cues)
        self.assertEqual((res["ok"], res["lines"], res["median_offset_s"]), (True, 4, 0.1))
        res = compare.check_sync(rows(-0.5), cues)
        self.assertFalse(res["ok"])
        self.assertIn("speech starts -0.50 s from its subtitle", res["issues"][0])
        res = compare.check_sync(rows(0.1)[:2] + [{"t_in": 7.0, "text": "完全不同的话"}], cues)
        self.assertIsNone(res["ok"])
        self.assertIn("only 2 heard line(s)", res["skipped"])

    def test_whisper_rows_never_the_burned_in_captions(self):
        seg = [{"t_in": 1.0, "t_out": 2.0, "text": "x"}]
        self.assertEqual(compare.whisper_rows({"transcript": {"source": "captions", "segments": seg, "whisper": seg}}),
                         seg)
        self.assertEqual(compare.whisper_rows({"transcript": {"source": "captions", "segments": seg, "whisper": None}}),
                         [])
        self.assertEqual(compare.whisper_rows({"transcript": {"source": "whisper", "segments": seg, "whisper": None}}),
                         seg)
        self.assertEqual(compare.whisper_rows(None), [])

    def test_style_report(self):
        style = json.loads((FIX / "style.json").read_text(encoding="utf-8"))
        cuts = {"fps": 30, "n_frames": 300, "cuts": [{"frame": 90}, {"frame": 210}], "counts": {"hard": 1, "gradual": 1}}
        res = compare.style_report(cuts, {"speech_share": 0.4}, style)
        self.assertEqual(res["shot_len_s"]["got"]["n"], 3)
        self.assertEqual(res["shot_len_s"]["style"], style["rhythm"]["shot_len_s"])
        self.assertEqual(res["hard_cut_share"], {"got": 0.5, "style": 0.8})
        self.assertEqual(res["speech_share"]["got"], 0.4)
        self.assertEqual(compare.style_report(None, None, None), {})


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class FinalFilmTest(TempRoot):
    """An 8 s 640x360 film whose faults are known: black 2-3 s, a still 3-6 s, silence 3-5.5 s."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.film = Path(cls.dir.name) / "film.mov"
        tone = r"0.22*sin(2*PI*440*t)*not(between(t\,3\,5.5))"  # commas escaped for lavfi
        pic = ["testsrc2=d=2", "color=c=black:d=1", "color=c=gray:d=3", "testsrc2=d=2"]
        inputs = [x for src in pic for x in ("-f", "lavfi", "-i", f"{src}:s=640x360:r=30")]
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *inputs,
                        "-f", "lavfi", "-i", f"aevalsrc={tone}|{tone}:s=48000",
                        "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-map", "4:a",
                        "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "pcm_s16le",
                        str(cls.film)], check=True, timeout=60)  # pcm: AAC would smear the silence's edges
        cls.spans = compare.detect(cls.film, 8.0)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    @staticmethod
    def plan(dip=True, photo=True, music=((0, 240),)):
        """A dips to black into B, a photo holds 3-6 s, C ends it; music where given."""
        return {"timeline": {"fps": 30, "duration_frames": 240, "width": 640, "height": 360}, "titles": [],
                "video": [v1(1, "a", 0, 75, transition_out={"kind": "dip_to_black", "frames": 30} if dip else None),
                          v1(2, "b", 75, 90), v1(3, "p", 90, 180, **({"kind": "photo"} if photo else {})),
                          v1(4, "c", 180, 240)],
                "audio": [{"id": f"m{n:03d}", "kind": "music", "track": 2, "rec_in": a, "rec_out": b}
                          for n, (a, b) in enumerate(music, 1)], "ambient": []}

    def test_detection_finds_the_planted_faults(self):
        self.assertEqual(self.spans, {"black": [(2.0, 3.0)], "freeze": [(3.0, 6.0)], "silence": [(3.0, 5.5)]})

    def test_the_plan_explains_black_stills_and_silence(self):
        res = compare.check_gaps(self.spans, self.plan(), None, 8.0)
        self.assertEqual(res["issues"], ["silence 0:03.00–0:05.50 where the plan has sound"])
        self.assertTrue(compare.check_gaps(self.spans, self.plan(music=((0, 85), (170, 240))), None, 8.0)["ok"])
        issues = compare.check_gaps(self.spans, self.plan(dip=False, photo=False, music=()), None, 8.0)["issues"]
        self.assertEqual(issues, ["black 0:02.00–0:03.00 (no grade.json: night chapters unknown)",
                                  "frozen picture 0:03.00–0:06.00"])
        night = {"chapters": [{"name": "dusk"}, {"name": "night", "night": True}],
                 "items": {"v001": "night", "v002": "night", "v004": "dusk"}}
        self.assertTrue(compare.check_gaps(self.spans, self.plan(dip=False, music=()), night, 8.0)["ok"])
        night["items"]["v002"] = "dusk"  # 2.5-3 s is no longer night
        self.assertEqual(compare.check_gaps(self.spans, self.plan(dip=False, music=()), night, 8.0)["issues"],
                         ["black 0:02.00–0:03.00"])

    def test_loudness(self):
        res = compare.check_loudness(self.film, -14.0, -1.0)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["lufs"], -14.0, delta=0.5)
        res = compare.check_loudness(self.film, -23.0, -20.0)
        self.assertEqual(len(res["issues"]), 2, res)
        self.assertTrue(res["issues"][0].endswith("want -23.0 ± 1: run mix.py master on an audio-only render"))
        self.assertIn("true peak", res["issues"][1])

    def test_cli_is_incomplete_until_the_film_is_measured(self):
        plan_dir = self.root / "plan"
        plan_dir.mkdir(parents=True)
        plan_path = plan_dir / "cut_plan.json"
        plan_path.write_text(json.dumps(self.plan(music=((0, 85), (170, 240)))), encoding="utf-8")
        cues = [{"a": a, "b": a + 1.2, "text": t} for a, t in ((0.3, "今天我们去海边"), (3.2, "这是一张老照片"),
                                                               (6.1, "天气很好"))]
        (plan_dir / "subs.srt").write_text(subs.to_srt(cues), encoding="utf-8")
        out = self.root / "final.json"
        argv = ["final", str(self.film), "--plan", str(plan_path), "--out", str(out)]
        code, _, err = self.run_main(compare.main, argv)
        res = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((code, res["ok"], res["complete"]), (0, True, False), err)
        self.assertEqual({k for k, c in res["checks"].items() if c["ok"] is None}, {"cuts", "subtitles", "sync"})
        self.assertIn("not complete", err)

        measured = self.root / "measured"
        measured.mkdir()
        mv = {"cuts": {"fps": 30, "n_frames": 240, "counts": {"hard": 2, "gradual": 1},
                       "cuts": [{"frame": f, "t": round(f / 30, 3)} for f in (75, 90, 180)]},
              "text": {"step": 0.25, "tracks": [{"t_in": c["a"], "t_out": c["b"], "text": c["text"],
                                                 "center": [0.5, 0.9], "height": 0.05} for c in cues]}}
        au = {"speech_share": 0.45, "transcript": {"source": "captions", "segments": [],
                                                   "whisper": [{"t_in": c["a"] + 0.05, "text": c["text"]} for c in cues]}}
        (measured / "measure_video.json").write_text(json.dumps(mv), encoding="utf-8")
        (measured / "measure_audio.json").write_text(json.dumps(au), encoding="utf-8")
        code, _, err = self.run_main(compare.main, [*argv, "--measured", str(measured)])
        res = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((code, res["ok"], res["complete"]), (0, True, True), err)
        self.assertEqual(res["checks"]["sync"]["lines"], 3)
        self.assertEqual(res["style"]["hard_cut_share"]["got"], 0.667)
        self.assertEqual(paths.check([out]), [])

        plan_path.write_text(json.dumps(self.plan()), encoding="utf-8")  # music over the silence
        code, _, err = self.run_main(compare.main, [*argv, "--measured", str(measured), "--lufs", "-23"])
        self.assertEqual(code, 1)
        self.assertIn("silence 0:03.00–0:05.50 where the plan has sound", err)
        self.assertIn("want -23.0 ± 1", err)


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
