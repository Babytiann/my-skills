#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for music_lib.py without models or numpy. Run: python3 -I -B test_music.py

Library tests need ffprobe and ffmpeg on PATH and are skipped without them. yt-dlp and trash are faked.
Measuring with librosa, demucs and CLAP is covered by test_synthetic.py (MODELS=1 for the models).
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import music_lib as ml  # noqa: E402
import paths  # noqa: E402

HAS_FF = bool(shutil.which("ffprobe") and shutil.which("ffmpeg"))

# Stands in for yt-dlp: records its arguments, drops $FAKE_AUDIO and $FAKE_INFO next to the -o template.
FAKE_YTDLP = """#!/bin/sh
echo "$@" > "$FAKE_SEEN"
[ -n "$FAKE_EXIT" ] && { echo "ERROR: boom" >&2; exit "$FAKE_EXIT"; }
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
dir=$(dirname "$out")
cp "$FAKE_AUDIO" "$dir/a.wav"
cp "$FAKE_INFO" "$dir/a.info.json"
"""
# Stands in for trash: records what it was given and removes it.
FAKE_TRASH = """#!/bin/sh
echo "$@" >> "$FAKE_TRASHED"
rm -rf "$@"
"""


def sine_wav(path: Path, seconds: float, freq: float = 440.0, sr: int = 22050, amp: float = 0.3) -> Path:
    n = int(seconds * sr)
    frames = b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / sr))) for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return path


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        os.environ[paths.ENV_ROOT] = str(self.root)
        self.P = paths.load()

    def tearDown(self):
        os.environ.pop(paths.ENV_ROOT, None)
        self.tmp.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ml.main(argv)
        return code, out.getvalue(), err.getvalue()


class LicenseTest(unittest.TestCase):
    def test_status_from_license_text(self):
        cases = {
            "": "unknown",
            None: "unknown",
            "Creative Commons Attribution license (reuse allowed)": "attribution",
            "https://creativecommons.org/licenses/by/4.0/": "attribution",
            "cc-by-sa": "attribution",
            "CC BY-NC 3.0": "needs_license",
            "https://creativecommons.org/licenses/by-nc/4.0/": "needs_license",
            "Attribution-NonCommercial-ShareAlike": "needs_license",
            "Attribution 4.0 International": "attribution",
            "the 2nd edition": "unknown",
            "Creative Commons Attribution-NonCommercial": "needs_license",
            "cc-by-nd": "needs_license",
            "CC0 1.0": "cleared",
            "https://creativecommons.org/publicdomain/zero/1.0/": "cleared",
            "no-rights-reserved": "cleared",
            "Pixabay Content License": "cleared",
            "all-rights-reserved": "needs_license",
            "Standard YouTube License": "needs_license",
            "Restricted License": "needs_license",
            "FMA-Limited: Download Only": "needs_license",
            "Sampling+": "unknown",
        }
        for text, want in cases.items():
            self.assertEqual(ml.license_status(text), want, text)

    def test_explicit_status_wins_and_bad_status_is_refused(self):
        self.assertEqual(ml.make_license("needs_license", name="CC0")["status"], "needs_license")
        with self.assertRaises(SystemExit):
            ml.make_license("free")

    def test_incompetech_credit_uses_its_own_wording(self):
        text = ml.default_attribution("Snowfall", "Kevin MacLeod", {"source_url": "https://incompetech.com/x"})
        self.assertTrue(text.startswith("Snowfall Kevin MacLeod (incompetech.com)\n"))
        self.assertIn("creativecommons.org/licenses/by/4.0", text)


class SlugTest(unittest.TestCase):
    def test_slug_keeps_cjk_and_is_a_valid_name(self):
        s = ml.slug("北海道 · 冬の旅 (Remix)!")
        self.assertEqual(s, "北海道-冬の旅-remix")
        paths.check_name(f"{s}-abc123")
        self.assertEqual(ml.slug("___"), "track")
        self.assertLessEqual(len(ml.slug("x" * 100)), 40)


@unittest.skipUnless(HAS_FF, "needs ffprobe and ffmpeg")
class AddTest(TempRoot):
    def test_add_copies_and_never_touches_the_original(self):
        src = sine_wav(self.base / "My Song.wav", 12)
        before = ml.sha1_of(src)
        track, new = ml.add(self.P, src, "雪路 Snow Road", "某人", ml.make_license(name="CC BY 4.0"), "2026-11-14")
        self.assertTrue(new)
        self.assertEqual(ml.sha1_of(src), before)
        self.assertTrue(src.exists())
        self.assertTrue(track["id"].startswith("雪路-snow-road-"))
        self.assertEqual(track["file"], f"music/{track['id']}/{track['id']}.wav")
        self.assertTrue(self.P.abs(track["file"]).exists())
        self.assertEqual(track["license"]["status"], "attribution")
        self.assertTrue(track["license"]["attribution_auto"])
        self.assertAlmostEqual(track["duration_s"], 12, delta=0.05)
        on_disk = json.loads((self.P.music(track["id"]) / "track.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, track)
        self.assertEqual(paths.check([self.P.dir("music")]), [])

    def test_the_same_file_twice_is_one_track(self):
        src = sine_wav(self.base / "a.wav", 12)
        first, _ = ml.add(self.P, src, "A")
        again, new = ml.add(self.P, src, "Another title")
        self.assertFalse(new)
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(ml.library(self.P)), 1)

    def test_formats_resolve_may_not_read_are_decoded_to_wav(self):
        flac = self.base / "b.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(sine_wav(self.base / "b.wav", 12)), str(flac)],
                       check=True)
        track, _ = ml.add(self.P, flac, "B")
        self.assertTrue(track["file"].endswith(".wav"))
        self.assertEqual(ml.probe_audio(self.P.abs(track["file"]))["video"], False)

    def test_refuses_non_audio_and_odd_lengths(self):
        txt = self.base / "notes.txt"
        txt.write_text("hello")
        for bad in (txt, sine_wav(self.base / "short.wav", 4), self.base / "missing.wav"):
            with self.assertRaises(SystemExit, msg=bad.name):
                ml.add(self.P, bad)
        self.assertEqual(ml.library(self.P), [])

    def test_list_shows_status_and_unmeasured(self):
        ml.add(self.P, sine_wav(self.base / "c.wav", 12), "C", lic=ml.make_license("needs_license"))
        code, out, _ = self.run_main(["list"])
        self.assertEqual(code, 0)
        self.assertIn("needs_license", out)
        self.assertIn("未测", out)
        self.assertIn("\t0:12\t", out)
        code, out, _ = self.run_main(["list", "--json"])
        rows = json.loads(out)
        self.assertEqual(rows[0]["measured"], False)


@unittest.skipUnless(HAS_FF, "needs ffprobe and ffmpeg")
class FetchTest(TempRoot):
    def fetch(self, info: dict, audio_s: float = 12, **kw):
        bin_dir = self.base / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name, body in (("yt-dlp", FAKE_YTDLP), ("trash", FAKE_TRASH)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        info_file = self.base / "info.json"
        info_file.write_text(json.dumps(info), encoding="utf-8")
        self.seen, self.trashed = self.base / "seen", self.base / "trashed"
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_SEEN": str(self.seen),
               "FAKE_TRASHED": str(self.trashed), "FAKE_AUDIO": str(sine_wav(self.base / "dl.wav", audio_s)),
               "FAKE_INFO": str(info_file)}
        env.update(kw.pop("env", {}))
        with mock.patch.dict(os.environ, env):
            return ml.fetch(self.P, "https://example.org/snow", today="2026-11-14", **kw)

    def assert_download_trashed(self):
        trashed = self.trashed.read_text().split()
        self.assertEqual(len(trashed), 1)
        self.assertIn("music-dl", trashed[0])
        self.assertEqual(list((self.P.dir("cache") / "music-dl").iterdir()), [])

    def test_fetch_reads_title_artist_and_license(self):
        info = {"title": "Snow Road", "uploader": "Someone", "license": "Creative Commons Attribution license",
                "webpage_url": "https://example.org/snow", "duration": 12, "format_id": "251"}
        track, new = self.fetch(info, referer="https://example.org/page")
        self.assertTrue(new)
        self.assertEqual((track["title"], track["artist"]), ("Snow Road", "Someone"))
        lic = track["license"]
        self.assertEqual((lic["status"], lic["source_url"], lic["fetched"]),
                         ("attribution", "https://example.org/snow", "2026-11-14"))
        self.assertIn("Snow Road", lic["attribution"])
        seen = self.seen.read_text()
        self.assertIn("--add-headers Referer:https://example.org/page", seen)
        self.assertIn("-- https://example.org/snow", seen)
        self.assertNotIn("cookies", seen)
        self.assert_download_trashed()

    def test_previews_drm_and_bandcamp_streams_are_refused(self):
        for info, word in (({"format_id": "preview-http", "duration": 12}, "preview"),
                           ({"has_drm": True, "duration": 12}, "DRM"),
                           ({"format_id": "hls-aes-128", "duration": 12}, "encrypted"),
                           ({"extractor_key": "Bandcamp", "format_id": "mp3-128", "duration": 12}, "Bandcamp")):
            with self.subTest(word=word):
                with self.assertRaises(SystemExit) as ctx:
                    self.fetch(info)
                self.assertIn(word, str(ctx.exception))
                self.assertEqual(ml.library(self.P), [])
                self.assert_download_trashed()
                self.trashed.unlink()

    def test_a_download_much_shorter_than_the_track_is_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            self.fetch({"title": "Long", "duration": 240})
        self.assertIn("cut short", str(ctx.exception))
        self.assert_download_trashed()

    def test_yt_dlp_failure_is_reported_and_cleaned_up(self):
        with self.assertRaises(SystemExit) as ctx:
            self.fetch({}, env={"FAKE_EXIT": "1"})
        self.assertIn("boom", str(ctx.exception))
        self.assert_download_trashed()

    def test_a_status_given_by_hand_wins(self):
        track, _ = self.fetch({"title": "T", "license": "Standard YouTube License", "duration": 12},
                              status="cleared")
        self.assertEqual(track["license"]["status"], "cleared")


def pattern(seconds: float, hop: float = 0.05, period: float = 60 / 90, peak: float = -10.0, base: float = -24.0,
            ramp_to: float = 0.0) -> list[float]:
    """A drum-like envelope: a hit every `period`, decaying linearly to `base`; `ramp_to` fades it out."""
    n = round(seconds / hop)
    out = []
    for i in range(n):
        phase = (i * hop) % period / period
        out.append(peak + (base - peak) * phase + ramp_to * i / max(1, n - 1))
    return out


class AnalysisTest(unittest.TestCase):
    def test_intro_is_where_the_song_gets_going(self):
        energy = [-40.0] * 10 + [-15.0] * 30
        self.assertAlmostEqual(ml.intro_of(energy), 10, delta=1)
        self.assertEqual(ml.intro_of([-15.0] * 30), 0)
        self.assertIsNone(ml.intro_of([-90.0] * 30))

    def test_hard_ending(self):
        e = ml.ending_of(pattern(20) + [-120.0] * 40)
        self.assertEqual(e["type"], "hard")
        self.assertAlmostEqual(e["end_s"], 20, delta=0.1)

    def test_spiky_drums_to_the_last_hit_are_a_hard_ending(self):
        # Deep gaps between hits: a plain dB mean read this as 30 s of fade-out (synthetic drums, 2026-11-14).
        e = ml.ending_of(pattern(30, period=0.6, base=-60) + [-120.0] * 20)
        self.assertEqual(e["type"], "hard")

    def test_fade_while_the_beat_goes_on(self):
        e = ml.ending_of(pattern(20) + pattern(5, ramp_to=-50) + [-120.0] * 20)
        self.assertEqual(e["type"], "fade")
        self.assertGreaterEqual(e["hits_per_s"], 1.0)

    def test_a_last_chord_rings_out(self):
        decay = [-10.0 - 15 * (i * 0.05) for i in range(80)]  # -15 dB/s, no new hits
        e = ml.ending_of(pattern(20) + decay + [-120.0] * 20)
        self.assertEqual(e["type"], "ring")
        self.assertLess(e["hits_per_s"], 1.0)

    def test_a_long_soft_close_counts_as_a_fade(self):
        smooth = [-10.0 - 6 * (i * 0.05) for i in range(200)]  # 10 s, slowly down
        self.assertEqual(ml.ending_of(pattern(20) + smooth)["type"], "fade")

    def test_climax_is_the_last_of_the_loudest_sections(self):
        energy = [-30.0] * 20 + [-14.0] * 30 + [-20.0] * 30 + [-14.3] * 30 + [-10.0] * 2
        sections = ml.sections_from_bounds([20, 50, 80, 110], energy, 112)
        self.assertEqual([s["t0"] for s in sections], [0.0, 20, 50, 80, 110])
        self.assertEqual(ml.climax_of(sections), 80)  # the 2-s burst at 110 is too short to count
        self.assertIsNone(ml.climax_of([]))

    def test_key_from_chroma(self):
        d_major = [ml.MAJOR[(p - 2) % 12] for p in range(12)]
        self.assertEqual({k: v for k, v in ml.key_of(d_major).items() if k in ("tonic", "mode")},
                         {"tonic": "D", "mode": "major"})
        a_minor = [ml.MINOR[(p - 9) % 12] for p in range(12)]
        k = ml.key_of(a_minor)
        self.assertEqual((k["tonic"], k["mode"]), ("A", "minor"))
        self.assertAlmostEqual(k["strength"], 1.0, places=3)
        self.assertIsNone(ml.key_of([0.0] * 12))

    def test_vocal_share_and_spans(self):
        sung = ml.vocal_of([-60.0] * 10 + [-20.0] * 20, [-18.0] * 30)
        self.assertEqual((sung["share"], sung["verdict"], sung["spans"]), (0.667, "vocal", [[10.0, 30.0]]))
        inst = ml.vocal_of([-40.0] * 30, [-15.0] * 30)  # demucs leakage well under the band
        self.assertEqual((inst["share"], inst["verdict"], inst["spans"]), (0.0, "instrumental", []))

    def test_narration_spans_are_left_out(self):
        talk_then_sing = ml.vocal_of([-20.0] * 30, [-30.0] * 30, exclude=[(0, 20)])
        self.assertEqual((talk_then_sing["share"], talk_then_sing["active_s"]), (1.0, 10.0))
        self.assertEqual(talk_then_sing["spans"], [[20.0, 30.0]])
        silent = ml.vocal_of([-90.0] * 5, [-90.0] * 5)
        self.assertEqual((silent["share"], silent["verdict"]), (None, None))


if __name__ == "__main__":
    unittest.main()
