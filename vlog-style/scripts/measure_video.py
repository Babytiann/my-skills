#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = [
#   "numpy",
#   "opencv-python-headless",
#   "pillow",
#   "scenedetect-headless",
#   "torch",
#   "transnetv2-pytorch",
# ]
# ///
# -*- coding: utf-8 -*-
"""Measure one proxied video: cuts, transitions, motion, look, text, strips, sheets.

DIR is a folder made by prep.py (prep.json + proxy.mp4): a reference under
<root>/cache/refs/<id>/ or a footage clip under <root>/projects/<p>/proxies/<clip>/.
Optional inputs in DIR: ocr.json (vision_ocr.swift on the source) and
measure_audio.json (speech spans, used to classify captions).

Stages, each stored in DIR/measure_video.json and re-run when asked or when
an input stage re-ran:
  cuts    TransNetV2 (CPU) on 48x27 frames, hard cut frame refined by the
          frame difference; PySceneDetect AdaptiveDetector as a cross-check
  text    OCR samples -> tracks -> subtitle / title / other (stats.py)
  scan    one pass at 320 px: luma, sharpness, clipping, LK affine motion;
          letterbox, per-shot motion/exposure, transition and effect candidates,
          keyframes (frames/)
  look    Lab stats per shot keyframe, letterbox and text masked
  strips  event strips at native fps around candidates, titles, sampled cuts;
          start/middle/end frames of sampled push/pull shots (strips/moves_*.jpg)
  sheets  contact sheets, one row of labelled keyframes per shot

Usage:
  uv run --python 3.13 --script measure_video.py DIR [--redo cuts,scan] [--skip strips]
      [--per-shot N] [--no-psd]
All paths written are relative to DIR. Times are seconds, frames are proxy
frames (native fps), spans are [start, end) with an exclusive end.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats  # noqa: E402

OUT = "measure_video.json"
STAGES = ["cuts", "text", "scan", "look", "strips", "sheets"]
DEPS = {"cuts": [], "text": [], "scan": ["cuts"], "look": ["scan", "text"],
        "strips": ["scan", "text"], "sheets": ["scan"]}

THR = 0.5            # TransNetV2 probability for a transition frame
HARD_MAX = 3         # transition runs up to this many frames are hard cuts
AW = 320             # analysis width of the scan pass
ROI = (0.15, 0.78)   # rows for motion/sharpness: clear of 2.39 bars and the caption band
LONG_S = 600         # longer videos get one keyframe per shot
ZOOM_MIN = 1.04      # total scale change that counts as a push-in / pull-out
ZOOM_EFF = 0.95      # ... and it must be one steady move (net / gross log-zoom)
MOVES_MAX = 24       # push/pull shots sampled into strips/moves_*.jpg for labelling
PAN_MIN = 0.04       # frame widths per second
STATIC_SHAKE = 0.12  # jitter RMS, % of frame width
STRIP_HALF = 0.5     # seconds either side of an event
STRIP_W, STRIP_COLS, STRIP_MAX = 192, 12, 36
TILE_W, SHEET_COLS, SHEET_ROWS = 256, 6, 8
CJK_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def log(msg: str) -> None:
    print(f"[measure_video] {msg}", file=sys.stderr, flush=True)


def rnd(x, k: int = 4):
    return None if x is None or not math.isfinite(float(x)) else round(float(x), k)


def fmed(x) -> float | None:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return float(np.median(x)) if x.size else None


def med_mad(x) -> dict | None:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if not x.size:
        return None
    m = float(np.median(x))
    return {"median": rnd(m, 3), "mad": rnd(float(np.median(np.abs(x - m))), 3), "n": int(x.size)}


def mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def runs(mask) -> list[tuple[int, int]]:
    """Inclusive [a, b] runs of True."""
    m = np.concatenate([[0], np.asarray(mask, np.int8), [0]])
    d = np.diff(m)
    return [(int(a), int(b) - 1) for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))]


def smooth(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) < 3 or k < 2:
        return x.astype(float).copy()
    k = min(k, len(x))
    xp = np.pad(x.astype(float), (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(xp, np.ones(k) / k, mode="valid")


def font(size: int):
    try:
        return ImageFont.truetype(CJK_FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


# ---------------------------------------------------------------- decoding

def ffmpeg_cmd(src: Path, w: int, h: int, ss: float | None = None, n: int | None = None,
               flags: str = "area") -> list[str]:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if ss is not None:
        cmd += ["-ss", f"{max(0.0, ss):.4f}"]
    cmd += ["-i", str(src)]
    if n:
        cmd += ["-frames:v", str(n)]
    return cmd + ["-map", "0:v:0", "-vf", f"scale={w}:{h}:flags={flags}",
                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


def read_frames(src: Path, w: int, h: int, **kw) -> np.ndarray:
    r = subprocess.run(ffmpeg_cmd(src, w, h, **kw), capture_output=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg decode failed: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w, 3)


def stream_frames(src: Path, w: int, h: int):
    size = w * h * 3
    p = subprocess.Popen(ffmpeg_cmd(src, w, h), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while len(buf := p.stdout.read(size)) == size:
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


# ---------------------------------------------------------------- cuts

def transnet(small: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import torch
    from transnetv2_pytorch import TransNetV2

    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
    with contextlib.redirect_stdout(sys.stderr):
        model = TransNetV2(device="cpu")
    model.eval()
    with torch.no_grad():
        single, many = model.predict_frames(torch.from_numpy(small.copy()), quiet=True)
    return single.numpy().astype(np.float32), many.numpy().astype(np.float32)


def psd_cuts(proxy: Path) -> list[int]:
    from scenedetect import AdaptiveDetector, detect

    with contextlib.redirect_stdout(sys.stderr):
        scenes = detect(str(proxy), AdaptiveDetector(), start_in_scene=True)
    return [int(start.frame_num) for start, _ in scenes[1:]]


def proxy_clock(proxy: Path, fps: float) -> list[int] | None:
    """Decoded-frame count -> ffmpeg's constant-rate frame index, from the proxy's packet timestamps."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                        "-of", "csv=p=0", str(proxy)], capture_output=True, text=True)
    pts = []
    for x in r.stdout.split():
        try:
            pts.append(float(x.split(",")[0]))
        except ValueError:
            continue
    return stats.cfr_index(pts, fps) if r.returncode == 0 and pts else None


def stage_cuts(d: Path, proxy: Path, fps: float, use_psd: bool) -> dict:
    small = read_frames(proxy, 48, 27, flags="bicubic")
    n = len(small)
    diff = np.zeros(n, np.float32)
    for i in range(1, n, 4096):
        j = min(n, i + 4096)
        diff[i:j] = np.abs(small[i:j].astype(np.int16) - small[i - 1:j - 1]).mean(axis=(1, 2, 3))
    log(f"transnet on {n} frames")
    single, many = transnet(small)
    np.savez_compressed(d / "cuts_raw.npz", single=single, many=many, diff=diff)
    found = []
    for a, b in runs((single > THR) | (many > THR)):
        score = float(max(single[a:b + 1].max(), many[a:b + 1].max()))
        if b - a + 1 <= HARD_MAX:
            lo, hi = max(1, a - 1), min(n - 1, b + 2)
            f = lo + int(np.argmax(diff[lo:hi + 1]))
            found.append({"frame": f, "kind": "hard", "span": [f, f], "score": score, "src": "transnet"})
        else:
            found.append({"frame": a + (b + 1 - a) // 2, "kind": "gradual", "span": [a, b + 1],
                          "score": score, "src": "transnet"})
    rejected, shift = [], None
    if use_psd:
        log("pyscenedetect cross-check")
        # PySceneDetect counts decoded frames; the frames above come from ffmpeg's constant-rate pipe,
        # which repeats a frame where timestamps skip, so a long proxy drifts a frame every few minutes
        clock = proxy_clock(proxy, fps)
        shift = clock[-1] - (len(clock) - 1) if clock else None
        if shift:
            log(f"proxy timestamps skip {shift} frame(s); psd frames mapped onto the constant-rate clock")
        for f in psd_cuts(proxy):
            if clock and f < len(clock):
                f = clock[f]
            hit = next((c for c in found if c["span"][0] - 2 <= f <= max(c["span"][1], c["frame"]) + 2), None)
            if hit:
                hit["src"] = "both"
                continue
            lo, hi = max(1, f - 2), min(n - 1, f + 2)
            g = lo + int(np.argmax(diff[lo:hi + 1]))
            local = float(np.median(diff[max(1, f - 15):min(n, f + 16)]))
            weak = float(single[lo:hi + 1].max())
            if weak > 0.1 or diff[g] > 4 * max(local, 0.5):
                found.append({"frame": g, "kind": "hard", "span": [g, g], "score": weak, "src": "psd"})
            else:
                rejected.append(f)
    found.sort(key=lambda c: c["frame"])
    cuts = []
    for c in found:
        if c["frame"] < 2 or c["frame"] > n - 2:
            continue
        if cuts and c["frame"] - cuts[-1]["frame"] < 3:
            if (c["src"] == "both", c["score"]) > (cuts[-1]["src"] == "both", cuts[-1]["score"]):
                cuts[-1] = c
            continue
        cuts.append(c)
    for c in cuts:
        c["t"] = rnd(c["frame"] / fps, 3)
        c["frames"] = c["span"][1] - c["span"][0]
        c["score"] = rnd(c["score"], 3)
    bounds = [0] + [c["frame"] for c in cuts] + [n]
    shots = [{"i": i, "start": a, "end": b} for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]
    return {"n_frames": n, "fps": fps, "cuts": cuts, "shots": shots, "psd_rejected": rejected,
            "psd_clock_shift": shift,
            "counts": {"hard": sum(c["kind"] == "hard" for c in cuts),
                       "gradual": sum(c["kind"] == "gradual" for c in cuts),
                       "agree": sum(c["src"] == "both" for c in cuts)}}


# ---------------------------------------------------------------- text

def stage_text(d: Path) -> dict:
    ocr = d / "ocr.json"
    if not ocr.exists():
        return {"skipped": "no ocr.json: run vision_ocr.swift on the source with --out DIR/ocr.json"}
    o = json.loads(ocr.read_text(encoding="utf-8"))
    step = 1.0 / float(o["fps_sampled"])
    speech = None
    audio = d / "measure_audio.json"
    if audio.exists():
        speech = json.loads(audio.read_text(encoding="utf-8")).get("speech")
    tracks = stats.link_text(o["samples"], step)
    info = stats.classify_text(tracks, speech)
    tracks.sort(key=lambda t: t["t_in"])
    for i, t in enumerate(tracks):
        t["i"] = i
    transcript = [{"t_in": t["t_in"], "t_out": t["t_out"], "text": t["text"]}
                  for t in tracks if t["kind"] == "subtitle"]
    return {"ocr": ocr.name, "step": step, "speech_used": bool(speech), **info,
            "tracks": tracks, "transcript": transcript}


# ---------------------------------------------------------------- scan

def affine(prev: np.ndarray, cur: np.ndarray):
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=150, qualityLevel=0.01, minDistance=6, blockSize=7)
    if pts is None or len(pts) < 12:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
    ok = st.ravel() == 1
    if ok.sum() < 12:
        return None
    p0, p1 = pts[ok].reshape(-1, 2), nxt[ok].reshape(-1, 2)
    m, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if m is None or inl is None or inl.sum() < 8:
        return None
    s = math.hypot(m[0, 0], m[1, 0])
    cx, cy = prev.shape[1] / 2, prev.shape[0] / 2  # translation of the ROI centre
    tx = m[0, 0] * cx + m[0, 1] * cy + m[0, 2] - cx
    ty = m[1, 0] * cx + m[1, 1] * cy + m[1, 2] - cy
    mag = float(np.median(np.linalg.norm(p1 - p0, axis=1)))
    return s, math.atan2(m[1, 0], m[0, 0]), tx, ty, mag


def keyframes_for(shots: list[dict], per_shot: int) -> dict[int, int]:
    out = {}
    for s in shots:
        a, b = s["start"], s["end"]
        out[(a + b) // 2] = s["i"]
        for j in range(per_shot if per_shot > 1 else 0):
            out.setdefault(a + int((b - a) * (j + 0.5) / per_shot), s["i"])
    return out


def letterbox(rows: np.ndarray) -> tuple[int, int]:
    h = rows.shape[1]
    dark = (rows < 20).mean(axis=0)
    top = next((i for i in range(h // 3) if dark[i] <= 0.9), h // 3)
    bot = next((i for i in range(h // 3) if dark[h - 1 - i] <= 0.9), h // 3)
    return (top if top >= 0.03 * h else 0), (bot if bot >= 0.03 * h else 0)


def summarise_shot(sh: dict, fps: float, W: int, A: dict) -> dict:
    s, e = sh["start"], sh["end"]
    idx = np.arange(s + 1, e)
    ok = idx[np.isfinite(A["scale"][idx])] if len(idx) else idx
    out = {"i": sh["i"], "start": s, "end": e, "t0": rnd(s / fps, 3), "t1": rnd(e / fps, 3),
           "len_s": rnd((e - s) / fps, 3), "luma": rnd(fmed(A["Y"][s:e]), 3),
           "clip_hi": rnd(fmed(A["hi"][s:e]), 4), "clip_lo": rnd(fmed(A["lo"][s:e]), 4),
           "sharp_raw": rnd(fmed(A["sharp"][s:e]), 1)}
    out["exposure_ok"] = bool((out["luma"] or 0) >= 0.04 and (out["clip_hi"] or 0) < 0.1)
    inner = slice(s + (e - s) // 10, max(s + (e - s) // 10 + 1, e - (e - s) // 10))
    sm = smooth(A["sharp"][inner], 5)
    out["best"] = int(inner.start + int(np.argmax(sm))) if sm.size else (s + e) // 2
    motion = {"type": "unknown", "zoom": None, "pan": None, "tilt": None, "shake": None, "valid": rnd(len(ok) / max(1, len(idx)), 2)}
    if len(ok) >= max(3, 0.3 * len(idx)):
        sc = A["scale"][ok]
        zoom = float(np.exp(np.log(sc).sum()))
        tx, ty = A["tx"][ok] / W, A["ty"][ok] / W
        k = max(3, int(round(fps * 0.5)))
        px, py = np.cumsum(tx), np.cumsum(ty)
        jit = np.hypot(px - smooth(px, k), py - smooth(py, k))
        shake = float(np.sqrt(np.mean(jit ** 2)) * 100)
        pan = float(-np.median(tx) * fps)   # camera direction, + = right
        tilt = float(np.median(ty) * fps)   # + = camera tilts up (scene moves down)
        ls = np.log(sc)
        active = np.abs(ls) > 5e-4
        # net / gross log-zoom over 0.5 s blocks: ~1 for one steady push or pull;
        # people crossing the frame, turns and parallax make the scale go back and forth
        blocks = np.add.reduceat(ls, np.arange(0, len(ls), max(2, int(round(0.5 * fps)))))
        eff = float(abs(ls.sum()) / (np.abs(blocks).sum() + 1e-12))
        zoomed = zoom >= ZOOM_MIN or zoom <= 1 / ZOOM_MIN
        motion.update(zoom=rnd(zoom), pan=rnd(pan), tilt=rnd(tilt), shake=rnd(shake, 3),
                      zoom_s=rnd(active.sum() / fps, 2), zoom_eff=rnd(eff, 3))
        if zoomed and eff >= ZOOM_EFF:
            motion["type"] = "push_in" if zoom > 1 else "pull_out"
        elif abs(pan) >= PAN_MIN:
            motion["type"] = "pan_right" if pan > 0 else "pan_left"
        elif abs(tilt) >= PAN_MIN:
            motion["type"] = "tilt_up" if tilt > 0 else "tilt_down"
        elif zoomed:
            motion["type"] = "complex"
        elif shake >= STATIC_SHAKE:
            motion["type"] = "handheld"
        else:
            motion["type"] = "static"
        mag = A["mag"][ok]
        steady = ("push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down")
        if (e - s) / fps >= 1.5 and len(mag) >= 10 and motion["type"] in steady:  # a ramp on one camera move
            m = smooth(mag, max(3, int(round(fps * 0.25))))
            p10, p90 = np.percentile(m, 10), np.percentile(m, 90)
            if p90 > 0.8 and p90 / max(p10, 0.05) >= 3:
                out["ramp_candidate"] = {"ratio": rnd(p90 / max(p10, 0.05), 2),
                                         "t_slow": rnd(ok[int(np.argmin(m))] / fps, 2),
                                         "t_fast": rnd(ok[int(np.argmax(m))] / fps, 2)}
        if fmed(mag) and fmed(mag) > 0.5:
            dups = A["diff"][s + 1:e] < 0.3
            if dups.mean() > 0.2:
                out["dup_frames"] = rnd(dups.mean(), 3)
            frz = [r for r in runs(A["diff"][s + 1:e] < 0.15) if r[1] - r[0] + 1 >= 0.3 * fps]
            if frz:
                out["freeze"] = [[s + 1 + a, s + 2 + b] for a, b in frz]
    out["motion"] = motion
    return out


def luma_event(Y: np.ndarray, a: int, b: int, w: int) -> dict:
    """Flash / dip evidence around a cut span [a, b); spans reach 20 % of the excursion."""
    n = len(Y)
    lo, hi = max(0, a - w), min(n, b + w)
    ctx = np.concatenate([Y[max(0, a - 3 * w):lo], Y[hi:min(n, b + 3 * w)]])
    base = float(np.median(ctx)) if ctx.size else float(np.median(Y))
    seg = Y[lo:hi]
    out: dict = {"luma_base": rnd(base, 3)}
    # the run must end inside the window on both sides: a cut into a dark (polar night)
    # or bright (snow) shot stays low or high up to the window edge and is no dip or flash
    pk = int(np.argmax(seg))
    if seg[pk] - base > 0.2 and seg[pk] > 0.6:
        r = next(r for r in runs(seg > base + 0.2 * (seg[pk] - base)) if r[0] <= pk <= r[1])
        if 0 < r[0] and r[1] < len(seg) - 1:
            out["flash"] = {"peak": rnd(seg[pk], 3), "frames": r[1] - r[0] + 1, "span": [lo + r[0], lo + r[1] + 1]}
    tr = int(np.argmin(seg))
    if seg[tr] < 0.06 and base > 0.12:
        r = next(r for r in runs(seg < base - 0.2 * (base - seg[tr])) if r[0] <= tr <= r[1])
        if 0 < r[0] and r[1] < len(seg) - 1:
            out["dip"] = {"min": rnd(seg[tr], 3), "frames": r[1] - r[0] + 1, "span": [lo + r[0], lo + r[1] + 1]}
    return out


def snap_cuts(cuts: dict, Y: np.ndarray, fps: float) -> None:
    """Under a flash or a dip the content switch is invisible: put the edit point
    at the centre of the flash/dip (where Resolve centres the transition) and
    rebuild the shot list. Starts from the detector's cut (`raw`) every time, so
    re-running scan never stacks one snap on another."""
    w = max(2, int(round(0.4 * fps)))
    cs = cuts["cuts"]
    for c in cs:
        c.update(c.setdefault("raw", {k: c[k] for k in ("frame", "t", "span", "frames")}))
        c.pop("snap", None)
    for k, c in enumerate(cs):
        lum = luma_event(Y, c["span"][0], max(c["span"][1], c["span"][0] + 1), w)
        c["luma"] = lum
        ev = lum.get("flash") or lum.get("dip")
        if not ev:
            continue
        a, b = ev["span"]
        mid = a + (b - a) // 2
        lo = cs[k - 1]["frame"] + 2 if k else 2
        hi = cs[k + 1]["frame"] - 2 if k + 1 < len(cs) else len(Y) - 2
        if lo <= mid <= hi:
            c.update(frame=mid, span=[a, b], frames=b - a, t=rnd(mid / fps, 3), snap="flash" if "flash" in lum else "dip")
    bounds = [0] + [c["frame"] for c in cs] + [cuts["n_frames"]]
    cuts["shots"] = [{"i": i, "start": a, "end": b} for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]


def cut_events(cuts: list[dict], shots: list[dict], fps: float, W: int, A: dict) -> list[dict]:
    Y, n = A["Y"], len(A["Y"])
    ev = []
    for ci, c in enumerate(cuts):
        f = c["frame"]
        evid = dict(c.get("luma") or {})
        cands = [k for k, name in (("flash_white", "flash"), ("dip_to_black", "dip")) if name in evid]
        # whip / zoom evidence from frames just outside the transition (inside a
        # dissolve or flash the blur and LK failures are the transition itself)
        a, b = c["span"]
        skip = set(range(a, b)) if b > a else set()
        near = np.asarray([k for k in range(max(1, min(a, f) - 3), min(n, max(b, f + 1) + 3))
                           if k not in skip and 0.08 < Y[k] < 0.9], int)
        ref = fmed([shots[k]["sharp_raw"] for k in (ci, ci + 1) if k < len(shots) and shots[k]["sharp_raw"]])
        blur = float(A["sharp"][near].min() / ref) if ref and near.size else None
        tried = near[A["tried"][near]] if near.size else near
        txn = A["tx"][tried] / W
        fails = int(np.isnan(A["scale"][tried]).sum())
        shift = float(np.nanmax(np.abs(txn))) if np.isfinite(txn).any() else None
        evid.update(blur=rnd(blur, 3), lk_fail=fails, max_shift=rnd(shift))
        if blur is not None and blur < 0.4 and ((shift or 0) > 0.03 or fails >= 3):
            cands.append("whip")
            if np.isfinite(txn).any():
                evid["whip_dir"] = "right" if np.nanmedian(txn) < 0 else "left"
        sc = A["scale"][tried]
        if np.isfinite(sc).any() and np.nanmax(np.abs(np.log(sc))) > math.log(1.04):
            cands.append("zoom")
        if c["kind"] == "gradual" and not cands:
            cands.append("dissolve")
        if cands:
            ev.append({"kind": cands[0], "candidates": cands, "frame": f, "t": c["t"], "cut": ci,
                       "span": c["span"], "frames": c["frames"], "evidence": evid})
    return ev


def other_events(cuts: list[dict], shots: list[dict], fps: float, A: dict) -> list[dict]:
    Y, n = A["Y"], len(A["Y"])
    w = max(2, int(round(0.4 * fps)))
    ev = []
    k = int(fps) | 1
    if n > k:
        base = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(Y, k // 2, mode="edge"), k), axis=1)
        near = np.zeros(n, bool)
        for c in cuts:
            near[max(0, c["span"][0] - w):min(n, max(c["span"][1], c["frame"]) + w + 1)] = True
        for a, b in runs(((Y - base) > 0.2) & ~near):
            if b - a + 1 <= fps:
                pk = a + int(np.argmax(Y[a:b + 1]))
                ev.append({"kind": "flash_in_shot", "candidates": ["flash_in_shot"], "frame": pk,
                           "t": rnd(pk / fps, 3), "span": [a, b + 1], "frames": b - a + 1,
                           "evidence": {"peak": rnd(Y[pk], 3), "base": rnd(base[pk], 3)}})
    first, last = shots[0], shots[-1]
    ref0 = fmed(Y[first["start"]:first["end"]][len(Y[first["start"]:first["end"]]) // 2:]) or 0
    if Y[0] < 0.06 and ref0 > 0.12:
        f = next((i for i in range(first["end"]) if Y[i] >= 0.8 * ref0), first["end"])
        ev.append({"kind": "fade_in", "candidates": ["fade_in"], "frame": 0, "t": 0.0, "span": [0, f],
                   "frames": f, "evidence": {"to_luma": rnd(ref0, 3)}})
    seg = Y[last["start"]:last["end"]]
    ref1 = fmed(seg[: max(1, len(seg) // 2)]) or 0
    if Y[-1] < 0.06 and ref1 > 0.12:
        f = next((i for i in range(n - 1, last["start"] - 1, -1) if Y[i] >= 0.8 * ref1), last["start"])
        ev.append({"kind": "fade_out", "candidates": ["fade_out"], "frame": f + 1, "t": rnd((f + 1) / fps, 3),
                   "span": [f + 1, n], "frames": n - f - 1, "evidence": {"from_luma": rnd(ref1, 3)}})
    return ev


def stage_scan(d: Path, proxy: Path, fps: float, pw: int, ph: int, cuts: dict, per_shot: int) -> dict:
    W, H = AW, max(2, int(round(AW * ph / pw / 2)) * 2)
    r0, r1 = int(H * ROI[0]), int(H * ROI[1])
    n = cuts["n_frames"]
    shots = cuts["shots"]
    no_motion = np.zeros(n + 64, bool)
    for s in shots:
        no_motion[s["start"]] = True
    for c in cuts["cuts"]:
        no_motion[c["span"][0]:c["span"][1] + 1] = True
    keys = keyframes_for(shots, per_shot)
    frames_dir = d / "frames"
    frames_dir.mkdir(exist_ok=True)
    rows, sharp, hi, lo, diff, mot, tried = [], [], [], [], [], [], []
    prev = None
    t0 = time.time()
    for f, rgb in enumerate(stream_frames(proxy, W, H)):
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        rows.append(g.mean(axis=1))
        roi = g[r0:r1]
        sharp.append(cv2.Laplacian(roi, cv2.CV_32F).var())
        hi.append((roi >= 250).mean())
        lo.append((roi <= 5).mean())
        diff.append(np.abs(roi.astype(np.int16) - prev).mean() if prev is not None else 0.0)
        tried.append(prev is not None and not no_motion[f])
        m = affine(prev, roi) if tried[-1] else None
        mot.append(m if m else (np.nan,) * 5)
        if f in keys:
            Image.fromarray(rgb).save(frames_dir / f"k{f:06d}.jpg", quality=88)
        prev = roi
        if (f + 1) % 5000 == 0:
            log(f"scan {f + 1}/{n} frames, {time.time() - t0:.0f} s")
    rows = np.asarray(rows, np.float32)
    mot = np.asarray(mot, np.float64).reshape(-1, 5)
    top, bot = letterbox(rows)
    A = {"Y": rows[:, top:H - bot].mean(axis=1) / 255.0, "sharp": np.asarray(sharp, float),
         "hi": np.asarray(hi), "lo": np.asarray(lo), "diff": np.asarray(diff, float),
         "scale": mot[:, 0], "rot": mot[:, 1], "tx": mot[:, 2], "ty": mot[:, 3], "mag": mot[:, 4],
         "tried": np.asarray(tried, bool)}
    np.savez_compressed(d / "scan_raw.npz", **A)
    if len(A["Y"]) != n:
        log(f"decoded {len(A['Y'])} frames, cuts saw {n}")
        n = min(n, len(A["Y"]))
    snap_cuts(cuts, A["Y"], fps)
    shots = cuts["shots"]
    summaries = [summarise_shot(s, fps, W, A) for s in shots if s["start"] < n]
    events = cut_events(cuts["cuts"], summaries, fps, W, A) + other_events(cuts["cuts"], summaries, fps, A)
    events.sort(key=lambda e: e["frame"])
    aspect = (pw / ph) * H / (H - top - bot)
    return {"analysis_size": [W, H], "roi_rows": list(ROI),
            "letterbox": {"top": rnd(top / H, 3), "bottom": rnd(bot / H, 3),
                          "aspect": rnd(aspect, 2) if top or bot else None},
            "shots": summaries, "events": events,
            "keyframes": [{"frame": f, "shot": i, "file": f"frames/k{f:06d}.jpg"} for f, i in sorted(keys.items())
                          if f < n]}


# ---------------------------------------------------------------- look

def text_boxes_at(text: dict, t: float) -> list[list[float]]:
    if not text or "tracks" not in text:
        return []
    return [tr["box"] for tr in text["tracks"] if tr["t_in"] - 0.25 <= t <= tr["t_out"] + 0.25]


def look_of(rgb: np.ndarray, mask: np.ndarray) -> dict | None:
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)
    L, a, b = lab[..., 0][mask], lab[..., 1][mask], lab[..., 2][mask]
    if L.size < 500:
        return None
    out = {"L01": np.percentile(L, 1), "L50": np.median(L), "L99": np.percentile(L, 99), "Lstd": L.std(),
           "shadow": (L < 20).mean(), "chroma": np.median(np.hypot(a, b))}
    for name, zone in (("sh", L < 33), ("mid", (L >= 33) & (L < 66)), ("hi", L >= 66)):
        ok = zone.mean() >= 0.02
        out[f"{name}_a"] = np.median(a[zone]) if ok else np.nan
        out[f"{name}_b"] = np.median(b[zone]) if ok else np.nan
    hh, ww = mask.shape
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    cy, cx = int((y1 - y0) * 0.15), int((x1 - x0) * 0.15)
    Lf = lab[..., 0]
    corners = np.concatenate([Lf[y0:y0 + cy, x0:x0 + cx].ravel(), Lf[y0:y0 + cy, x1 - cx:x1].ravel(),
                              Lf[y1 - cy:y1, x0:x0 + cx].ravel(), Lf[y1 - cy:y1, x1 - cx:x1].ravel()])
    my, mx = (y0 + y1) // 2, (x0 + x1) // 2
    centre = Lf[my - cy:my + cy, mx - cx:mx + cx]
    out["vignette"] = float(corners.mean() / centre.mean()) if centre.size and centre.mean() > 1 else np.nan
    sat = np.hypot(a, b) > 8
    hue = (np.degrees(np.arctan2(b[sat], a[sat])) + 360) % 360
    out["hue_hist"] = (np.histogram(hue, bins=12, range=(0, 360))[0] / max(1, L.size)).round(4).tolist()
    return {k: (v if isinstance(v, list) else rnd(v, 3)) for k, v in out.items()}


BAR_MAX = 12  # a bar row or column: 99% of its pixels at or below this (bars decode to 0-3)


def _bar_pair(lines: np.ndarray) -> tuple[int, int]:
    """Bars at both ends of one axis; lines is (n, k), one row or column per entry. (0, 0) when none.

    Only a symmetric pair of pure black bands with picture right inside counts, so a night
    shot whose sky or ground is dark at one end, or dark all over, is not taken for bars.
    """
    n = len(lines)
    # mostly black with a black median: subtitles are often set in the bottom bar and must not end it there
    black = ((lines <= BAR_MAX).mean(axis=1) >= 0.6) & (np.median(lines, axis=1) <= 3)
    pure = np.percentile(lines, 99, axis=1) <= BAR_MAX
    a = next((i for i in range(n) if not black[i]), n)
    b = next((i for i in range(n) if not black[n - 1 - i]), n)
    if not (0.03 * n <= a <= 0.35 * n and 0.03 * n <= b <= 0.35 * n) or abs(a - b) > max(2, 0.03 * n):
        return 0, 0
    if pure[:a].mean() < 0.5 or pure[n - b:].mean() < 0.5:  # a bar is plain black for most of its rows
        return 0, 0
    inside = np.percentile(lines[[a, n - 1 - b]], 90, axis=1)
    return (a, b) if (inside > BAR_MAX + 10).any() else (0, 0)


def frame_bars(gray: np.ndarray) -> tuple[int, int, int, int]:
    """Pixel sizes (top, bottom, left, right) of black bars baked into one frame.

    The film-wide letterbox comes from the scan; this catches bars that only some shots
    carry (a 2.35:1 crop on the scenery shots, a 4:3 clip, a photo on black).
    """
    top, bottom = _bar_pair(gray)
    left, right = _bar_pair(gray.T)
    return top, bottom, left, right


def stage_look(d: Path, fps: float, scan: dict, text: dict) -> dict:
    lb = scan["letterbox"]
    band = (text or {}).get("subtitle_band")
    per_shot, keys = [], {}
    for k in scan["keyframes"]:
        keys.setdefault(k["shot"], k)
    for sh in scan["shots"]:
        k = keys.get(sh["i"])
        if not k:
            continue
        rgb = np.asarray(Image.open(d / k["file"]).convert("RGB"))
        h, w = rgb.shape[:2]
        mask = np.zeros((h, w), bool)
        mask[int(lb["top"] * h):h - int(lb["bottom"] * h)] = True
        if band:
            mask[max(0, int((band["y"] - band["height"]) * h)):int((band["y"] + band["height"]) * h)] = False
        for x, y, bw, bh in text_boxes_at(text, k["frame"] / fps):
            mask[max(0, int((y - 0.25 * bh) * h)):int((y + 1.25 * bh) * h),
                 max(0, int((x - 0.1 * bw) * w)):int((x + 1.1 * bw) * w)] = False
        stats_ = look_of(rgb, mask)
        if not stats_ or (stats_["L50"] or 0) <= 3:  # skip black frames (dips, fades, black text cards)
            continue
        # judged on the frame as a whole: with its bars out, a black card would read as its text alone
        bars = frame_bars(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        if any(bars):
            t, b, l, r = bars
            mask[:t] = mask[h - b:] = False
            mask[:, :l] = mask[:, w - r:] = False
            stats_ = look_of(rgb, mask)
        if stats_:
            per_shot.append({"shot": sh["i"], "frame": k["frame"], **stats_,
                             **({"bars": [rnd(bars[0] / h, 3), rnd(bars[1] / h, 3), rnd(bars[2] / w, 3),
                                          rnd(bars[3] / w, 3)]} if any(bars) else {})})
    keys_ = ["L01", "L50", "L99", "Lstd", "shadow", "chroma", "sh_a", "sh_b", "mid_a", "mid_b",
             "hi_a", "hi_b", "vignette"]
    summary = {k: med_mad([p[k] for p in per_shot if p.get(k) is not None]) for k in keys_}
    hue = np.median(np.asarray([p["hue_hist"] for p in per_shot]), axis=0) if per_shot else None
    return {"shots_used": len(per_shot), "summary": summary,
            "hue_hist": None if hue is None else [rnd(v) for v in (hue / max(hue.sum(), 1e-9))],
            "per_shot": per_shot,
            "shots_with_bars": sum(1 for p in per_shot if p.get("bars")),
            "note": "Lab from 320 px keyframes; letterbox (film-wide and per shot), caption band and OCR boxes masked. "
                    "Grain is not measurable after platform re-encoding; judge it from stills."}


# ---------------------------------------------------------------- strips

def strip_offsets(n: int, centre: int) -> list[int]:
    idx = list(range(n))
    if n <= STRIP_MAX:
        return idx
    return [i for i in idx if abs(i - centre) <= 9 or (i - centre) % 3 == 0]


def render_strip(frames: np.ndarray, offsets: list[int], header: str, path: Path) -> None:
    th, tw = frames.shape[1:3]
    cols = min(STRIP_COLS, len(frames))
    nrows = math.ceil(len(frames) / cols)
    lab, head = 16, 24
    img = Image.new("RGB", (cols * tw, head + nrows * (th + lab)), (18, 18, 18))
    dr = ImageDraw.Draw(img)
    dr.text((6, 4), header, font=font(14), fill=(240, 240, 240))
    small = font(11)
    for k, (fr, off) in enumerate(zip(frames, offsets)):
        x, y = (k % cols) * tw, head + (k // cols) * (th + lab)
        img.paste(Image.fromarray(fr), (x, y))
        dr.text((x + 3, y + th + 1), f"{off:+d}", font=small, fill=(255, 210, 0) if off == 0 else (190, 190, 190))
    img.save(path, quality=82)


def grab(proxy: Path, fps: float, first: int, n: int, w: int, h: int) -> np.ndarray:
    return read_frames(proxy, w, h, ss=(first - 0.5) / fps if first > 0 else None, n=n)


def title_colour(proxy: Path, fps: float, tr: dict, pw: int, ph: int) -> dict | None:
    fr = grab(proxy, fps, int((tr["t_in"] + tr["t_out"]) / 2 * fps), 1, pw, ph)
    if not len(fr):
        return None
    rgb = fr[0]
    x, y, w, h = tr["box"]
    X0, Y0, X1, Y1 = int(x * pw), int(y * ph), int((x + w) * pw), int((y + h) * ph)
    box = rgb[Y0:Y1, X0:X1]
    if box.size < 30:
        return None
    L = cv2.cvtColor(box, cv2.COLOR_RGB2Lab)[..., 0]
    _, th = cv2.threshold(L, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = (max(2, int(0.3 * (Y1 - Y0))))
    ring = rgb[max(0, Y0 - m):min(ph, Y1 + m), max(0, X0 - m):min(pw, X1 + m)]
    ring_mask = np.ones(ring.shape[:2], bool)
    ring_mask[Y0 - max(0, Y0 - m):Y0 - max(0, Y0 - m) + (Y1 - Y0), X0 - max(0, X0 - m):X0 - max(0, X0 - m) + (X1 - X0)] = False
    bg_L = float(np.median(cv2.cvtColor(ring, cv2.COLOR_RGB2Lab)[..., 0][ring_mask])) if ring_mask.any() else 128
    bright, dark = th > 0, th == 0
    if not bright.any() or not dark.any():
        return None
    light_text = abs(np.median(L[bright]) - bg_L) >= abs(np.median(L[dark]) - bg_L)
    text_mask = bright if light_text else dark
    # thin strokes at proxy size are mostly anti-aliased edge: take the colour of the stroke core
    tl = L[text_mask]
    core = text_mask & ((L >= np.percentile(tl, 75)) if light_text else (L <= np.percentile(tl, 25)))
    fill = np.median(box[core], axis=0).astype(int)
    edge = cv2.dilate(text_mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~text_mask
    out = {"fill": "#%02x%02x%02x" % tuple(fill), "bg_L": rnd(bg_L * 100 / 255, 1),
           "fill_L": rnd(float(np.median(L[core])) * 100 / 255, 1), "edge": "none"}
    if edge.any():
        eL = float(np.median(L[edge]))
        tL = float(np.median(L[text_mask]))
        if eL < min(tL, bg_L) - 25:
            out["edge"] = "dark"
        elif eL > max(tL, bg_L) + 25:
            out["edge"] = "light"
    return out


def stage_strips(d: Path, proxy: Path, fps: float, pw: int, ph: int, cuts: dict, scan: dict, text: dict) -> dict:
    sdir = d / "strips"
    sdir.mkdir(exist_ok=True)
    tw = STRIP_W
    th = max(2, int(round(tw * ph / pw / 2)) * 2)
    events = [dict(e) for e in scan["events"]]
    used = {e.get("cut") for e in events}
    hard = [(i, c) for i, c in enumerate(cuts["cuts"]) if c["kind"] == "hard" and i not in used]
    want = max(6, math.ceil(len(hard) / 25))
    for i, c in hard[:: max(1, math.ceil(len(hard) / want))] if hard else []:
        events.append({"kind": "hard_sample", "candidates": ["hard_cut"], "frame": c["frame"], "t": c["t"], "cut": i})
    titles = []
    for tr in (text or {}).get("tracks", []):
        if tr["kind"] != "title":
            continue
        for edge, t in (("title_in", tr["t_in"]), ("title_out", tr["t_out"])):
            events.append({"kind": edge, "candidates": [edge], "frame": int(round(t * fps)), "t": t, "track": tr["i"]})
        titles.append({"track": tr["i"], "text": tr["text"], "t_in": tr["t_in"], "t_out": tr["t_out"],
                       "height": tr["height"], "center": tr["center"], "grow": tr["grow"], "drift": tr["drift"],
                       "colour": title_colour(proxy, fps, tr, pw, ph)})
    events.sort(key=lambda e: e["frame"])
    half = int(round(STRIP_HALF * fps))
    for k, e in enumerate(events):
        first = max(0, e["frame"] - half)
        fr = grab(proxy, fps, first, 2 * half + 1, tw, th)
        if not len(fr):
            continue
        centre = e["frame"] - first
        keep = strip_offsets(len(fr), centre)
        name = f"strips/e{k:03d}_{e['kind']}.jpg"
        header = f"e{k:03d} {e['kind']}  t={mmss(e['t'])}  f={e['frame']}  {'/'.join(e['candidates'])}"
        if "track" in e:
            header += f"  「{next(t['text'] for t in titles if t['track'] == e['track'])}」"
        render_strip(fr[keep], [i - centre for i in keep], header, d / name)
        e["strip"] = name
        e["i"] = k
    return {"events": events, "titles": titles, "tile": [tw, th], "moves": render_moves(d, proxy, fps, pw, ph, scan)}


def render_moves(d: Path, proxy: Path, fps: float, pw: int, ph: int, scan: dict) -> list[dict]:
    """10 % / 50 % / 90 % frames of sampled push/pull shots. The scale trend alone cannot tell
    a digital push-in from a drone, car or walking move; the agent labels `move` from these."""
    moves = [s for s in scan["shots"] if s["motion"]["type"] in ("push_in", "pull_out")]
    if len(moves) > MOVES_MAX:
        moves = [moves[int(k * len(moves) / MOVES_MAX)] for k in range(MOVES_MAX)]
    tw = TILE_W
    th = max(2, int(round(tw * ph / pw / 2)) * 2)
    lab, gap, per_row, rows = 18, 10, 2, 6
    f = font(12)
    out = []
    for p in range(0, len(moves), per_row * rows):
        chunk = moves[p:p + per_row * rows]
        name = f"strips/moves_{p // (per_row * rows) + 1:02d}.jpg"
        img = Image.new("RGB", (per_row * (3 * tw + gap) - gap, math.ceil(len(chunk) / per_row) * (th + lab)),
                        (18, 18, 18))
        dr = ImageDraw.Draw(img)
        for j, s in enumerate(chunk):
            x, y = (j % per_row) * (3 * tw + gap), (j // per_row) * (th + lab)
            for q, frac in enumerate((0.1, 0.5, 0.9)):
                fr = grab(proxy, fps, int(s["start"] + frac * (s["end"] - s["start"])), 1, tw, th)
                if len(fr):
                    img.paste(Image.fromarray(fr[0]), (x + q * tw, y))
            m = s["motion"]
            dr.text((x + 3, y + th + 2), f"#{s['i']:03d} {mmss(s['t0'])} {s['len_s']:.1f}s  {m['type']} "
                    f"x{m['zoom']:.2f} eff {m['zoom_eff']:.2f}   10% | 50% | 90%", font=f, fill=(225, 225, 225))
        img.save(d / name, quality=82)
        out += [{"shot": s["i"], "type": s["motion"]["type"], "zoom": s["motion"]["zoom"],
                 "zoom_eff": s["motion"]["zoom_eff"], "t0": s["t0"], "len_s": s["len_s"], "sheet": name}
                for s in chunk]
    return out


# ---------------------------------------------------------------- sheets

def stage_sheets(d: Path, fps: float, scan: dict) -> dict:
    sdir = d / "sheets"
    sdir.mkdir(exist_ok=True)
    shots = {s["i"]: s for s in scan["shots"]}
    code = {"push_in": "P+", "pull_out": "P-", "pan_left": "<", "pan_right": ">", "tilt_up": "^",
            "tilt_down": "v", "complex": "Z~", "handheld": "~", "static": "", "unknown": "?"}
    tiles = []
    for k in scan["keyframes"]:
        s = shots.get(k["shot"])
        if not s:
            continue
        label = f"#{s['i']:03d} {mmss(k['frame'] / fps)} {s['len_s']:.1f}s {code.get(s['motion']['type'], '')}"
        tiles.append((d / k["file"], label))
    if not tiles:
        return {"sheets": []}
    first = Image.open(tiles[0][0])
    th = int(round(TILE_W * first.height / first.width))
    lab = 18
    per = SHEET_COLS * SHEET_ROWS
    f = font(12)
    out = []
    for p in range(0, len(tiles), per):
        chunk = tiles[p:p + per]
        nrows = math.ceil(len(chunk) / SHEET_COLS)
        img = Image.new("RGB", (SHEET_COLS * TILE_W, nrows * (th + lab)), (18, 18, 18))
        dr = ImageDraw.Draw(img)
        for j, (path, label) in enumerate(chunk):
            x, y = (j % SHEET_COLS) * TILE_W, (j // SHEET_COLS) * (th + lab)
            img.paste(Image.open(path).convert("RGB").resize((TILE_W, th), Image.Resampling.BILINEAR), (x, y))
            dr.text((x + 3, y + th + 2), label, font=f, fill=(225, 225, 225))
        name = f"sheets/sheet_{p // per + 1:02d}.jpg"
        img.save(d / name, quality=82)
        out.append(name)
    return {"sheets": out, "tiles": len(tiles), "per_sheet": per}


# ---------------------------------------------------------------- main

def outputs_of(stage: dict | None) -> list[str]:
    """Image files a previous strips/sheets run recorded (relative to DIR)."""
    stage = stage or {}
    return ([e["strip"] for e in stage.get("events", []) if e.get("strip")]
            + sorted({m["sheet"] for m in stage.get("moves", [])}) + list(stage.get("sheets", [])))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--redo", default="", help="comma list of stages to re-run")
    ap.add_argument("--skip", default="", help="comma list of stages to leave out")
    ap.add_argument("--per-shot", type=int, default=None, help="keyframes per shot (default: 3 for <=10 min, else 1)")
    ap.add_argument("--no-psd", action="store_true", help="skip the PySceneDetect cross-check")
    args = ap.parse_args(argv)
    d = args.dir.expanduser().resolve()
    prep = json.loads((d / "prep.json").read_text(encoding="utf-8"))
    if prep.get("kind") == "photo":  # its preview.jpg is the sheet; aggregate.py footage indexes it as it is
        log(f"{d.name}: a photo, nothing to measure")
        return 0
    proxy = d / prep["proxy"]["file"]
    fps, pw, ph = float(prep["proxy"]["fps"]), int(prep["proxy"]["width"]), int(prep["proxy"]["height"])
    per_shot = args.per_shot or (3 if prep["proxy"]["duration_s"] <= LONG_S else 1)
    out_path = d / OUT
    res = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    res.update({"schema": "measure_video/1", "source": prep.get("source"), "bvid": prep.get("bvid"),
                "video": {"fps": fps, "width": pw, "height": ph, "duration_s": prep["proxy"]["duration_s"],
                          "source_size": [prep["probe"]["width"], prep["probe"]["height"]],
                          "source_fps": prep["probe"]["fps"]}})
    redo = {s for s in args.redo.split(",") if s}
    skip = {s for s in args.skip.split(",") if s}
    bad = (redo | skip) - set(STAGES)
    if bad:
        ap.error(f"unknown stage(s): {', '.join(sorted(bad))}")
    ran: set[str] = set()
    timings = res.get("timings", {})
    for st in STAGES:
        if st in skip:
            continue
        if st not in redo and st in res and not (set(DEPS[st]) & ran):
            continue
        t0 = time.time()
        log(f"stage {st}")
        for name in outputs_of(res.get(st)):  # event ids shift between runs: old strips would mislead
            (d / name).unlink(missing_ok=True)
        if st == "cuts":
            res[st] = stage_cuts(d, proxy, fps, not args.no_psd)
        elif st == "text":
            res[st] = stage_text(d)
        elif st == "scan":
            res[st] = stage_scan(d, proxy, fps, pw, ph, res["cuts"], per_shot)
        elif st == "look":
            res[st] = stage_look(d, fps, res["scan"], res.get("text"))
        elif st == "strips":
            res[st] = stage_strips(d, proxy, fps, pw, ph, res["cuts"], res["scan"], res.get("text"))
        else:
            res[st] = stage_sheets(d, fps, res["scan"])
        ran.add(st)
        timings[st] = round(time.time() - t0, 1)
        res["timings"] = timings
        res["measured"] = time.strftime("%Y-%m-%d")
        out_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    c = res.get("cuts", {}).get("counts", {})
    log(f"done: {len(res.get('cuts', {}).get('shots', []))} shots, cuts {c}, "
        f"{len(res.get('scan', {}).get('events', []))} events -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
