#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy", "opencv-python-headless", "pillow"]
# ///
# -*- coding: utf-8 -*-
"""End-to-end measurement test on a rendered clip whose answers are known.

Run: uv run --python 3.13 --script test_synthetic.py   (KEEP=1 keeps the temp root)

The clip (960x540, 30 fps, 16 s) has: hard cuts at frames 90 and 390, a
12-frame dissolve centred on frame 180 (6 s), a 6-frame white flash over the
cut at 270, a camera pan (shot B), a 1.0 -> 1.3 push-in (shot D), the title
「北极圈」 from 7.0 to 9.0 s, a caption line at the bottom that changes every
1.5 s, and a 120 BPM click track. prep.py, vision_ocr.swift and
measure_video.py (and measure_audio.py when present) run on it and must find
those answers within tolerance: cuts +-1 frame, zoom +-0.03, text +-0.25 s, BPM +-1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402
import schema  # noqa: E402

W, H, FPS, N = 960, 540, 30, 480
TITLE = "北极圈"
TITLE_IN, TITLE_OUT = 7.0, 9.0
CAPTIONS = ["清晨六点我们出发去海边", "风很大但是天空特别干净", "这里的冬天几乎没有太阳",
            "远处的山已经完全被雪覆盖", "小镇只有一条安静的主街", "我们在港口等了很久",
            "终于看到了第一缕极光", "所有人都停下来抬头看", "那一刻真的不想说话",
            "明天继续往北走"]
FONT_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_LIGHT = "/System/Library/Fonts/STHeiti Light.ttc"
UV = ["uv", "run", "--python", "3.13", "--script"]


def texture(seed: int, w: int, h: int, tint) -> np.ndarray:
    rng = np.random.default_rng(seed)
    coarse = cv2.resize(rng.random((h // 60 + 2, w // 60 + 2, 3)).astype(np.float32), (w, h),
                        interpolation=cv2.INTER_CUBIC)
    fine = cv2.resize(rng.random((h // 6, w // 6)).astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    img = (0.5 * coarse * np.asarray(tint, np.float32) + 0.35 * fine[..., None] + 0.1) * 255
    img = np.clip(img, 0, 255).astype(np.uint8)
    for _ in range(int(w * h / 6000)):
        x, y = int(rng.integers(0, w - 30)), int(rng.integers(0, h - 30))
        s = int(rng.integers(8, 30))
        cv2.rectangle(img, (x, y), (x + s, y + s), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    return img


def overlay_text(img: np.ndarray, t: float, fonts) -> np.ndarray:
    big, small = fonts
    pil = Image.fromarray(img)
    dr = ImageDraw.Draw(pil, "RGBA")
    k = int((t - 0.5) // 1.5)
    if 0.5 <= t < 0.5 + 1.5 * len(CAPTIONS) and 0 <= k < len(CAPTIONS):
        dr.text((W / 2, H * 0.9), CAPTIONS[k], font=small, fill=(255, 255, 255, 255), anchor="mm",
                stroke_width=2, stroke_fill=(0, 0, 0, 255))
    if TITLE_IN <= t < TITLE_OUT:
        a = min(1.0, (t - TITLE_IN) / 0.2, (TITLE_OUT - t) / 0.2)
        dr.text((W / 2, H * 0.45), TITLE, font=big, fill=(255, 255, 255, int(255 * a)), anchor="mm",
                stroke_width=3, stroke_fill=(20, 20, 20, int(255 * a)))
    return np.asarray(pil.convert("RGB"))


def frame_at(f: int, tex: dict) -> np.ndarray:
    def shot_b(g):
        x = 4 * (g - 90)
        return tex["B"][:, x:x + W]

    def shot_d(g):
        s = 1.0 if g < 300 else 1.3 if g >= 360 else 1.0 + 0.3 * (g - 300) / 60
        m = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        return cv2.warpAffine(tex["D"], m, (W, H), flags=cv2.INTER_LINEAR)

    if f < 90:
        img = tex["A"]
    elif f < 174:
        img = shot_b(f)
    elif f < 186:  # 12-frame dissolve B -> C, centre 180
        a = (f - 173) / 13
        img = ((1 - a) * shot_b(f).astype(np.float32) + a * tex["C"].astype(np.float32)).astype(np.uint8)
    elif f < 270:
        img = tex["C"]
    elif f < 390:
        img = shot_d(f)
    else:
        img = tex["E"]
    img = img.copy()
    if 267 <= f <= 272:  # white flash over the cut at 270
        w = [0.35, 0.7, 1.0, 1.0, 0.7, 0.35][f - 267]
        img = (img.astype(np.float32) * (1 - w) + 255 * w).astype(np.uint8)
    return img


def clicks(path: Path, seconds: float, bpm: float = 120, sr: int = 44100) -> None:
    n = int(seconds * sr)
    sig = np.zeros(n, np.float32)
    beat = 60 / bpm
    t = np.arange(int(0.03 * sr)) / sr
    for k in range(int(seconds / beat)):
        accent = k % 4 == 0
        burst = np.sin(2 * np.pi * (1000 if accent else 2000) * t) * np.exp(-t * 120) * (0.8 if accent else 0.5)
        i = int(k * beat * sr)
        sig[i:i + len(burst)] += burst[: n - i]
    pad = 0.05 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)
    pcm = np.clip((sig + pad) * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def make_clip(out: Path) -> None:
    tex = {"A": texture(1, W, H, (1.0, 0.5, 0.3)), "B": texture(2, W + 4 * 100, H, (0.3, 0.6, 1.0)),
           "C": texture(3, W, H, (0.4, 1.0, 0.4)), "D": texture(4, W, H, (1.0, 0.9, 0.2)),
           "E": texture(5, W, H, (0.8, 0.3, 0.9))}
    fonts = (ImageFont.truetype(FONT_BOLD, 96), ImageFont.truetype(FONT_LIGHT, 30))
    wav = out.with_suffix(".wav")
    clicks(wav, N / FPS)
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", "-crf", "16", "-g", "30", "-c:a", "aac", "-b:a", "192k",
                          "-shortest", str(out)], stdin=subprocess.PIPE)
    for f in range(N):
        p.stdin.write(overlay_text(frame_at(f, tex), f / FPS, fonts).tobytes())
    p.stdin.close()
    if p.wait():
        raise RuntimeError("ffmpeg failed to write the synthetic clip")


def run(cmd: list[str], env: dict) -> None:
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"{' '.join(cmd[:4])}... failed:\n{r.stderr[-2000:]}")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("uv") and shutil.which("xcrun"),
                     "needs ffmpeg, uv and xcrun (macOS)")
class SyntheticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="vlog-synth-"))
        cls.env = {**os.environ, paths.ENV_ROOT: str(cls.tmp / "root")}
        src = cls.tmp / "src" / "synthetic.mp4"
        src.parent.mkdir()
        make_clip(src)
        run([sys.executable, "-I", "-B", str(HERE / "prep.py"), "ref", str(src), "--id", "synthetic"], cls.env)
        cls.ref = cls.tmp / "root" / "cache" / "refs" / "synthetic"
        run(["xcrun", "swift", str(HERE / "vision_ocr.swift"), str(src), "--fps", "4",
             "--out", str(cls.ref / "ocr.json")], cls.env)
        cls.has_audio = (HERE / "measure_audio.py").exists()
        if cls.has_audio:
            run(UV + [str(HERE / "measure_audio.py"), str(cls.ref), "--no-asr"], cls.env)
        run(UV + [str(HERE / "measure_video.py"), str(cls.ref)], cls.env)
        cls.m = json.loads((cls.ref / "measure_video.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("KEEP"):
            print(f"\nkept {cls.tmp}", file=sys.stderr)
        elif shutil.which("trash"):
            subprocess.run(["trash", str(cls.tmp)])
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def cut_near(self, frame: int, tol: int = 1):
        hits = [c for c in self.m["cuts"]["cuts"] if abs(c["frame"] - frame) <= tol]
        self.assertTrue(hits, f"no cut within {tol} of {frame}: {[(c['frame'], c['kind']) for c in self.m['cuts']['cuts']]}")
        return hits[0]

    def test_cuts(self):
        cuts = self.m["cuts"]["cuts"]
        self.assertEqual(len(cuts), 4, [(c["frame"], c["kind"], c["span"]) for c in cuts])
        self.assertEqual(self.m["cuts"]["psd_clock_shift"], 0)  # a clean proxy needs no clock mapping
        self.assertEqual(self.cut_near(90)["kind"], "hard")
        self.assertEqual(self.cut_near(390)["kind"], "hard")
        self.cut_near(270)
        dis = self.cut_near(180)
        self.assertEqual(dis["kind"], "gradual")
        self.assertLessEqual(abs(dis["frames"] - 12), 3, dis)

    def test_transition_candidates(self):
        ev = self.m["scan"]["events"]
        flash = [e for e in ev if "flash_white" in e["candidates"] and abs(e["frame"] - 270) <= 2]
        self.assertTrue(flash, ev)
        self.assertLessEqual(abs(flash[0]["evidence"]["flash"]["frames"] - 6), 2, flash[0])
        self.assertTrue(any(e["kind"] == "dissolve" and abs(e["frame"] - 180) <= 1 for e in ev), ev)
        self.assertIsNone(self.m["scan"]["letterbox"]["aspect"])

    def test_motion(self):
        shots = self.m["scan"]["shots"]
        by_t = {round(s["t0"]): s for s in shots}
        self.assertEqual(by_t[0]["motion"]["type"], "static", by_t[0])
        self.assertEqual(by_t[3]["motion"]["type"], "pan_right", by_t[3])
        push = by_t[9]
        self.assertEqual(push["motion"]["type"], "push_in", push)
        self.assertAlmostEqual(push["motion"]["zoom"], 1.3, delta=0.03)
        self.assertGreaterEqual(push["motion"]["zoom_eff"], 0.95, push)
        self.assertEqual(by_t[13]["motion"]["type"], "static", by_t[13])
        self.assertEqual([m["shot"] for m in self.m["strips"]["moves"]], [push["i"]], self.m["strips"]["moves"])

    def test_text(self):
        text = self.m["text"]
        titles = [t for t in text["tracks"] if t["kind"] == "title"]
        self.assertEqual([t["text"] for t in titles], [TITLE], text["tracks"])
        self.assertAlmostEqual(titles[0]["t_in"], TITLE_IN, delta=0.25)
        self.assertAlmostEqual(titles[0]["t_out"], TITLE_OUT, delta=0.25)
        self.assertGreaterEqual(text["counts"].get("subtitle", 0), 8, text["counts"])
        self.assertTrue(text["subtitle_band"] and abs(text["subtitle_band"]["y"] - 0.9) < 0.03)
        colour = self.m["strips"]["titles"][0]["colour"]
        self.assertGreater(colour["fill_L"], 85, colour)

    def test_outputs_exist_and_paths_are_relative(self):
        for name in (self.m["sheets"]["sheets"] + [e["strip"] for e in self.m["strips"]["events"] if "strip" in e]
                     + [m["sheet"] for m in self.m["strips"]["moves"]]):
            self.assertTrue((self.ref / name).exists(), name)
        self.assertGreater(self.m["look"]["shots_used"], 3)
        self.assertEqual(paths.check([self.ref / "measure_video.json", self.ref / "prep.json"]), [])

    def test_audio(self):
        if not self.has_audio:
            self.skipTest("measure_audio.py not written yet")
        a = json.loads((self.ref / "measure_audio.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(a["beats"]["bpm"], 120, delta=1)

    def labelled(self) -> Path:
        """labels.json filled in once, whichever test needs it first."""
        lab_path = self.ref / "labels.json"
        if lab_path.exists():
            return lab_path
        run([sys.executable, "-I", "-B", str(HERE / "aggregate.py"), "labels", str(self.ref)], self.env)
        lab = json.loads(lab_path.read_text(encoding="utf-8"))
        lab["chapters"] = [{"name": "开场", "role": "hook", "t0": 0.0, "t1": 3.0},
                           {"name": "海边", "role": "chapter", "t0": 3.0, "t1": 9.0},
                           {"name": "北极圈", "role": "chapter", "t0": 9.0, "t1": 16.0}]
        for e in lab["events"]:
            e["type"] = "hard_cut" if e["kind"] == "hard_sample" else e["kind"]
        lab["events"].append({"i": 99, "type": "whip"})  # points at no measured event: rejected
        lab["titles"][0].update(role="location", font_class="黑体")
        self.assertEqual(len(lab["shots"]), 1, lab["shots"])  # the push-in, prefilled for a move verdict
        lab["shots"][0].update(move="digital", sure=True)
        lab_path.write_text(json.dumps(lab, ensure_ascii=False), encoding="utf-8")
        return lab_path

    def test_aggregate_draft_feeds_plan_cut(self):
        py = [sys.executable, "-I", "-B"]
        lab_path = self.labelled()
        draft = self.tmp / "style.draft.json"
        run(py + [str(HERE / "aggregate.py"), "draft", "--name", "synth", str(self.ref), "--out", str(draft)], self.env)
        style = json.loads(draft.read_text(encoding="utf-8"))
        src = style["sources"][0]  # a local file: no url, the file name stands in for the title
        self.assertEqual((src["id"], src["url"], src["title"]), ("synthetic", "", "synthetic.mp4"))
        self.assertEqual(src["measured"], self.m["measured"])
        types = {t["type"]: t for t in style["transitions"]}
        self.assertEqual(set(types), {"hard_cut", "dissolve", "flash_white"}, types)
        self.assertLessEqual(abs(types["flash_white"]["frames"] - 6), 1, types)
        self.assertEqual(types["flash_white"]["where"], "chapter_boundary")
        self.assertEqual(style["evidence"]["rejected_labels"], ["synthetic event 99: whip"])
        self.assertAlmostEqual(style["audio"]["bpm"]["median"], 120, delta=1)
        self.assertEqual(style["text"]["title"]["font_class"], "黑体")
        self.assertEqual(style["evidence"]["moves"]["push_in"], {"measured": 1, "judged": 1, "digital": 1})
        self.assertGreater(style["motion"]["push_in"]["share"], 0)
        self.assertAlmostEqual(style["motion"]["push_in"]["scale_end"]["median"], 1.3, delta=0.03)
        plan = self.tmp / "cut_plan.json"
        fx = HERE / "fixtures"
        run(py + [str(HERE / "plan_cut.py"), "--style-file", str(draft), "--index", str(fx / "footage.json"),
                  "--brief", str(fx / "brief.json"), "--beats", str(fx / "beats.json"), "--out", str(plan)], self.env)
        self.assertEqual(schema.validate(json.loads(plan.read_text(encoding="utf-8")), schema.PLAN), [])
        self.assertEqual(paths.check([draft, plan, lab_path]), [])
        # the reference's own keyframes must match the look card drafted from them
        look = self.tmp / "look.json"
        run(UV + [str(HERE / "compare.py"), "look", *map(str, sorted((self.ref / "frames").glob("*.jpg"))),
                  "--style-file", str(draft), "--out", str(look)], self.env)
        self.assertTrue(json.loads(look.read_text(encoding="utf-8"))["ok"])

    def test_keep_then_pool_two_references(self):
        py = [sys.executable, "-I", "-B"]
        self.labelled()
        run(py + [str(HERE / "cleanup.py"), "keep", "synthetic", "--style", "misc", "--yes"], self.env)
        kept = self.tmp / "root" / "styles" / "misc" / "refs" / "synthetic"
        text = "".join(p.read_text(encoding="utf-8") for p in kept.glob("*.json"))
        for line in CAPTIONS:
            self.assertNotIn(line, text)
        self.assertIn(TITLE, text)
        mv = json.loads((kept / "measure_video.json").read_text(encoding="utf-8"))
        self.assertEqual({t["text"] for t in mv["text"]["tracks"] if t["kind"] != "title"}, {""})
        self.assertEqual(paths.check([kept]), [])
        # the kept copy re-drafts exactly what the cache drafts
        a, b = self.tmp / "from_cache.json", self.tmp / "from_kept.json"
        run(py + [str(HERE / "aggregate.py"), "draft", "--name", "synth", str(self.ref), "--out", str(a)], self.env)
        run(py + [str(HERE / "aggregate.py"), "draft", "--name", "synth", str(kept), "--out", str(b)], self.env)
        self.assertEqual(json.loads(a.read_text(encoding="utf-8")), json.loads(b.read_text(encoding="utf-8")))
        # two kept references pool across references, and say who made each
        twin = kept.parent / "synthetic-twin"
        shutil.copytree(kept, twin)
        for d, mid in ((kept, "1"), (twin, "2")):
            (d / "meta.json").write_text(json.dumps({"bvid": f"BV{mid}synth", "title": f"合成片 {mid}",
                                                     "author": "测试", "mid": mid}, ensure_ascii=False), encoding="utf-8")
        pooled = self.tmp / "pooled.json"
        r = subprocess.run(py + [str(HERE / "aggregate.py"), "draft", "--name", "misc/synth", str(kept), str(twin),
                                 "--out", str(pooled)], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 uploaders", r.stderr)
        style = json.loads(pooled.read_text(encoding="utf-8"))
        self.assertEqual(style["basis"], "multi_reference")
        self.assertEqual([(s["title"], s["author"]) for s in style["sources"]], [("合成片 1", "测试"), ("合成片 2", "测试")])
        self.assertEqual(style["sources"][1]["url"], "https://www.bilibili.com/video/BV2synth")
        self.assertEqual(schema.check_style(style), [])
        plan = self.tmp / "pooled_plan.json"
        fx = HERE / "fixtures"
        run(py + [str(HERE / "plan_cut.py"), "--style-file", str(pooled), "--index", str(fx / "footage.json"),
                  "--brief", str(fx / "brief.json"), "--beats", str(fx / "beats.json"), "--out", str(plan)], self.env)
        self.assertEqual(schema.validate(json.loads(plan.read_text(encoding="utf-8")), schema.PLAN), [])


class LumaEventTest(unittest.TestCase):
    """Dips and flashes must come back on both sides; a cut into a dark or bright shot is neither."""

    def setUp(self):
        import measure_video
        self.ev = measure_video.luma_event

    def test_fade_through_black_is_a_dip(self):
        Y = np.full(120, 0.4)
        Y[50:70] = np.concatenate([np.linspace(0.4, 0.0, 10), np.linspace(0.0, 0.4, 10)])
        self.assertIn("dip", self.ev(Y, 55, 65, 12))

    def test_cut_into_a_dark_shot_is_no_dip(self):
        Y = np.concatenate([np.full(60, 0.4), np.full(60, 0.03)])
        self.assertNotIn("dip", self.ev(Y, 60, 61, 12))

    def test_cut_into_snow_is_no_flash(self):
        Y = np.concatenate([np.full(60, 0.2), np.full(60, 0.85)])
        self.assertNotIn("flash", self.ev(Y, 60, 61, 12))

    def test_short_flash_is_a_flash(self):
        Y = np.full(120, 0.3)
        Y[57:63] = 0.95
        self.assertEqual(self.ev(Y, 60, 61, 12)["flash"]["frames"], 6)



@unittest.skipUnless(os.environ.get("ASR"), "set ASR=1 to run whisper (needs the 1.6 GB model)")
class AsrSentencesTest(unittest.TestCase):
    """measure_audio --sentences on spoken audio: pieces under 30 s, punctuation, language per piece."""

    ZH, EN = "今天我们终于到了海边的小镇。", "The lighthouse is at the end of this road."

    def test_sentences_from_real_speech(self):
        tmp = Path(tempfile.mkdtemp(prefix="vlog-asr-"))
        env = {**os.environ, paths.ENV_ROOT: str(tmp / "root")}
        try:
            voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
            run(["say", "-v", "Tingting" if "Tingting" in voices else "Flo", "-o", str(tmp / "zh.aiff"), self.ZH], env)
            run(["say", "-v", "Samantha", "-o", str(tmp / "en.aiff"), self.EN], env)
            src = tmp / "src" / "talk.mp4"
            src.parent.mkdir()
            # zh at 2 s, en at 31 s: 36 s in all, so whisper sees at least two pieces
            run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=36",
                 "-i", str(tmp / "zh.aiff"), "-i", str(tmp / "en.aiff"), "-filter_complex",
                 "[1:a]adelay=2000|2000,aresample=48000[a];[2:a]adelay=31000|31000,aresample=48000[b];"
                 "[a][b]amix=inputs=2:normalize=0,apad,atrim=0:36[out]",
                 "-map", "0:v", "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                 "-shortest", str(src)], env)
            run([sys.executable, "-I", "-B", str(HERE / "prep.py"), "ref", str(src), "--id", "talk"], env)
            ref = tmp / "root" / "cache" / "refs" / "talk"
            run(UV + [str(HERE / "measure_audio.py"), str(ref), "--sentences", "--language", "auto"], env)
            a = json.loads((ref / "measure_audio.json").read_text(encoding="utf-8"))
            rows = a["transcript"]["segments"]
            self.assertEqual(a["transcript"]["asr"], {"language": "auto", "sentences": True})
            zh = [r for r in rows if r.get("lang") == "zh"]
            en = [r for r in rows if r.get("lang") == "en"]
            self.assertTrue(zh and en, rows)
            self.assertIn("小镇", "".join(r["text"] for r in zh))
            self.assertTrue(zh[-1]["text"].endswith(("。", "！", "？")), zh)
            self.assertIn("lighthouse", " ".join(r["text"] for r in en).lower())
            self.assertTrue(1.5 <= zh[0]["t_in"] <= 3.0, zh[0])
            self.assertTrue(30.5 <= en[0]["t_in"] <= 32.0, en[0])
        finally:
            if os.environ.get("KEEP"):
                print(f"\nkept {tmp}", file=sys.stderr)
            elif shutil.which("trash"):
                subprocess.run(["trash", str(tmp)])
            else:
                shutil.rmtree(tmp, ignore_errors=True)

class ReviewSheetTest(unittest.TestCase):
    """story.py review draws one sheet per section from proxy frames, and survives a missing proxy."""

    def test_sheets_per_section(self):
        import story
        import test_plan_cut as tpc

        tc = tpc.StoryTest("test_tracks_and_roles")
        tc.setUp()
        plan, index = tc.build(), tpc.trip()
        tmp = Path(tempfile.mkdtemp(prefix="vlog-review-"))
        try:
            clips = sorted({v["clip"] for v in plan["video"]})
            longest = max(c["src_s"] for c in story.cells(plan, index)) + 2
            one = tmp / "one.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            f"testsrc2=size=480x270:rate=10:duration={longest:.1f}", "-pix_fmt", "yuv420p",
                            "-preset", "ultrafast", str(one)], check=True)
            for cid in clips[1:]:
                (tmp / "proxies" / cid).mkdir(parents=True)
                shutil.copy(one, tmp / "proxies" / cid / "proxy.mp4")
            out = story.review(plan, index, tmp, tmp / "qa" / "review")
            names = [s["name"] for s in plan["sections"] if any(v["section"] == s["name"] for v in plan["video"])]
            self.assertEqual([p.name.split("_", 1)[1] for p in out], [f"{n}.jpg" for n in names])
            img = Image.open(out[0])
            self.assertEqual(img.width, story.COLS * story.CELL_W)
            px = np.asarray(img.convert("RGB"))[34 + 90, 160]
            self.assertGreater(int(px.sum()), 0)
        finally:
            if os.environ.get("KEEP"):
                print(f"\nkept {tmp}", file=sys.stderr)
            elif shutil.which("trash"):
                subprocess.run(["trash", str(tmp)])
            else:
                shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
