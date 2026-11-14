#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure-stdlib statistics shared by the vlog-style scripts.

Robust summaries (median + MAD, never mean + std: editing and colour data are
right-skewed), cut-to-beat alignment against a shifted-cut baseline, and
on-screen text tracking/classification. No I/O here, so it is unit-tested with
`python3 -I -B test_stats.py` and imported by both the stdlib scripts and the
uv scripts.
"""

from __future__ import annotations

import bisect
import math
import random
from collections import Counter
from difflib import SequenceMatcher


# ---------------------------------------------------------------- summaries

def percentile(xs, q: float) -> float | None:
    """Linear-interpolated percentile, q in [0, 100]."""
    xs = sorted(x for x in xs if x is not None and not math.isnan(x))
    if not xs:
        return None
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def median(xs) -> float | None:
    return percentile(xs, 50)


def mad(xs) -> float | None:
    """Median absolute deviation (unscaled)."""
    m = median(xs)
    if m is None:
        return None
    return median([abs(x - m) for x in xs if x is not None])


def summary(xs, ndigits: int = 3) -> dict:
    xs = [float(x) for x in xs if x is not None and not math.isnan(x)]
    if not xs:
        return {"n": 0}
    r = lambda v: None if v is None else round(v, ndigits)  # noqa: E731
    return {
        "n": len(xs),
        "median": r(median(xs)),
        "mad": r(mad(xs)),
        "p10": r(percentile(xs, 10)),
        "p90": r(percentile(xs, 90)),
        "min": r(min(xs)),
        "max": r(max(xs)),
    }


def consistency(groups: dict, rel_tol: float = 0.25, min_n: int = 2) -> dict:
    """Do several groups (references or chapters) agree on one quantity?

    Compares group medians: spread (max - min) relative to the median of the
    medians. Groups with fewer than `min_n` values are ignored. Returns
    {"verdict": consistent|varies|single_source, "medians": {...}, "spread": x}.
    """
    meds = {k: median(v) for k, v in groups.items() if len([x for x in v if x is not None]) >= min_n}
    meds = {k: v for k, v in meds.items() if v is not None}
    if len(meds) < 2:
        return {"verdict": "single_source", "medians": meds, "spread": None}
    center = median(list(meds.values()))
    spread = max(meds.values()) - min(meds.values())
    rel = spread / abs(center) if center else math.inf
    return {
        "verdict": "consistent" if rel <= rel_tol else "varies",
        "medians": {k: round(v, 4) for k, v in meds.items()},
        "spread": round(rel, 4) if rel != math.inf else None,
    }


def shot_lengths(cut_times: list[float], start: float, end: float) -> list[float]:
    """Durations between consecutive cuts inside [start, end]."""
    pts = [start] + sorted(t for t in cut_times if start < t < end) + [end]
    return [b - a for a, b in zip(pts, pts[1:]) if b > a]


def cfr_index(pts: list[float], fps: float) -> list[int]:
    """k-th decoded frame -> its index on a constant-rate grid starting at the first frame.

    ffmpeg's rawvideo pipe is constant-rate (it repeats a frame where timestamps skip),
    while OpenCV-based readers just count frames; this maps the count onto ffmpeg's clock.
    """
    if not pts:
        return []
    ts = sorted(pts)
    return [round((t - ts[0]) * fps) for t in ts]


# ---------------------------------------------------------------- beats

def _inside(t: float, spans) -> bool:
    return any(a <= t <= b for a, b in spans)


def nearest_offset(t: float, beats: list[float]) -> float | None:
    """Signed distance from t to the nearest beat (negative = cut before beat)."""
    if not beats:
        return None
    i = bisect.bisect_left(beats, t)
    cands = [beats[j] for j in (i - 1, i) if 0 <= j < len(beats)]
    b = min(cands, key=lambda x: abs(x - t))
    return t - b


# Fewer cuts than this in the music stretches say nothing about cutting on the beat: at a ~6 %
# baseline, 13 cuts expect less than one hit by chance (learnings 2026-11-13; was 8).
MIN_BEAT_CUTS = 20
ON_BEAT_LIFT = 1.5  # hit rate over the shifted-cut baseline that counts as cutting on the beat


def beat_alignment(cuts: list[float], beats: list[float], tol: float,
                   spans=None, downbeats=None, shuffles: int = 200, seed: int = 0) -> dict:
    """How often cuts land on beats, compared with chance.

    Only cuts inside `spans` (e.g. non-speech stretches where beats were
    tracked) and inside the beat-covered range count. The baseline shifts every
    cut by one random offset in [0, median inter-beat interval) and re-measures,
    `shuffles` times; `lift` = hit_rate / baseline.
    """
    beats = sorted(beats)
    if len(beats) < 4:
        return {"n_cuts": 0, "verdict": "no_beats"}
    lo, hi = beats[0], beats[-1]
    use = [c for c in cuts if lo <= c <= hi and (spans is None or _inside(c, spans))]
    if not use:
        return {"n_cuts": 0, "verdict": "no_cuts_in_beat_spans"}
    ibi = median([b - a for a, b in zip(beats, beats[1:])])
    offs = [nearest_offset(c, beats) for c in use]
    hits = sum(1 for o in offs if abs(o) <= tol)
    rng = random.Random(seed)
    base = []
    for _ in range(shuffles):
        s = rng.uniform(0, ibi)
        base.append(sum(1 for c in use if abs(nearest_offset(c + s, beats)) <= tol) / len(use))
    baseline = sum(base) / len(base)
    out = {
        "n_cuts": len(use),
        "tol_s": tol,
        "ibi_s": round(ibi, 4),
        "bpm": round(60.0 / ibi, 2) if ibi else None,
        "hit_rate": round(hits / len(use), 4),
        "baseline": round(baseline, 4),
        "lift": round(hits / len(use) / baseline, 3) if baseline else None,
        "offset_s": summary([o for o in offs if abs(o) <= ibi / 2], 4),
    }
    if downbeats:
        db = sorted(downbeats)
        out["downbeat_hit_rate"] = round(
            sum(1 for c in use if abs(nearest_offset(c, db)) <= tol) / len(use), 4)
    if len(use) < MIN_BEAT_CUTS:
        out["verdict"] = "too_few_cuts"
    elif out["lift"] is not None and out["lift"] >= ON_BEAT_LIFT and out["hit_rate"] >= 0.3:
        out["verdict"] = "on_beat"
    else:
        out["verdict"] = "not_on_beat"
    return out


def tempo_agrees(bpm: float | None, other: float | None) -> bool | None:
    """Do two tempo estimates agree within 4 %? Half and double time count as agreeing.
    None when either is missing."""
    if not bpm or not other:
        return None
    return any(abs(other * k - bpm) / bpm < 0.04 for k in (0.5, 1, 2))


def bpm_from_beats(beats: list[float], spans=None) -> float | None:
    beats = sorted(beats)
    ibis = [b - a for a, b in zip(beats, beats[1:])
            if spans is None or (_inside(a, spans) and _inside(b, spans))]
    m = median(ibis)
    if not m:
        return None
    # beat trackers report frame-quantised times (20-23 ms): the median gap snaps
    # to a whole frame, the mean of the in-tempo gaps does not
    near = [x for x in ibis if abs(x - m) <= 0.15 * m]
    if not near:  # a few gaps far apart (an even count's median falls between them): no steady tempo
        return None
    return round(60.0 * len(near) / sum(near), 2)


# ---------------------------------------------------------------- text tracks

def iou(a, b) -> float:
    """Boxes are [x, y, w, h], normalised, origin top-left."""
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def text_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def link_text(samples: list[dict], step: float, iou_min: float = 0.3,
              sim_min: float = 0.6, max_gap: int = 2) -> list[dict]:
    """Chain per-frame OCR hits into tracks.

    samples: [{"t": s, "text": str, "box": [x, y, w, h], "conf": c}], sampled
    every `step` seconds. A hit joins the open track whose last box overlaps it
    and whose text is similar; a track closes after `max_gap` missed samples.
    """
    tracks: list[dict] = []
    open_tracks: list[dict] = []
    for s in sorted(samples, key=lambda x: x["t"]):
        t = s["t"]
        open_tracks = [tr for tr in open_tracks if t - tr["hits"][-1]["t"] <= step * (max_gap + 1) + 1e-6]
        best, best_score = None, 0.0
        for tr in open_tracks:
            last = tr["hits"][-1]
            if last["t"] >= t - 1e-9:
                continue  # one hit per track per sample time
            ov, sim = iou(last["box"], s["box"]), text_sim(last["text"], s["text"])
            if ov >= iou_min and sim >= sim_min and ov + sim > best_score:
                best, best_score = tr, ov + sim
        if best is None:
            best = {"hits": []}
            tracks.append(best)
            open_tracks.append(best)
        best["hits"].append(s)
    return [_summarise_track(hs, step) for tr in tracks for hs in _split_runs(tr["hits"])]


def _split_runs(hits: list[dict], min_run: int = 2) -> list[list[dict]]:
    """Split a track where the text changes and the new text holds.

    Consecutive captions at the same spot can differ by one character, so the
    linker joins them. A run of >= `min_run` samples with different text is a
    new line; shorter runs are OCR noise and fold into the line around them.
    """
    runs: list[list] = []
    for h in hits:
        if runs and runs[-1][0] == h["text"]:
            runs[-1][1].append(h)
        else:
            runs.append([h["text"], [h]])
    out: list[list] = []
    for text, hs in runs:
        if out and (len(hs) < min_run or text_sim(out[-1][0], text) >= 0.95):
            out[-1][1].extend(hs)
        else:
            out.append([text, hs])
    if len(out) > 1 and len(out[0][1]) < min_run:
        out[1][1] = out[0][1] + out[1][1]
        out.pop(0)
    return [hs for _, hs in out]


def _summarise_track(hits: list[dict], step: float) -> dict:
    boxes = [h["box"] for h in hits]
    text = Counter(h["text"] for h in hits).most_common(1)[0][0]
    box = [median([b[i] for b in boxes]) for i in range(4)]
    first, last = boxes[0], boxes[-1]
    c0 = (first[0] + first[2] / 2, first[1] + first[3] / 2)
    c1 = (last[0] + last[2] / 2, last[1] + last[3] / 2)
    heights = [b[3] for b in boxes]
    return {
        "t_in": round(hits[0]["t"], 3),
        "t_out": round(hits[-1]["t"] + step, 3),
        "text": text,
        "box": [round(v, 4) for v in box],
        "height": round(median(heights), 4),
        "center": [round(box[0] + box[2] / 2, 4), round(box[1] + box[3] / 2, 4)],
        "n": len(hits),
        "conf": round(median([h.get("conf", 1.0) for h in hits]), 3),
        "drift": round(math.dist(c0, c1), 4),
        "grow": round(heights[-1] / heights[0], 3) if heights[0] else None,
    }


def _overlap(a: float, b: float, spans) -> float:
    if b <= a:
        return 0.0
    got = sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)
    return got / (b - a)


def classify_text(tracks: list[dict], speech=None, band_min: int = 5) -> dict:
    """Label each track subtitle / title / other, in place.

    - subtitle: bottom-centred band that recurs at least `band_min` times with a
      stable text height (burned-in captions). Speech overlap is recorded and
      rescues a band too short to recur.
    - other: scene text that drifts with the camera, tiny text, or one char.
    - title: what remains with display size (taller than captions) and a
      plausible on-screen time.
    """
    speech = speech or []
    for tr in tracks:
        tr["speech_overlap"] = round(_overlap(tr["t_in"], tr["t_out"], speech), 3) if speech else None
    cand = [tr for tr in tracks
            if tr["center"][1] > 0.7 and abs(tr["center"][0] - 0.5) < 0.2 and tr["drift"] < 0.03]
    band = None
    if cand:
        # Anchor on the densest row, not on the median of every candidate: much scene text low in
        # the frame (signs, a web page on screen) pulled the median off the caption line in one
        # film (Svalbard, BV1H5xie8EJZ) and its captions were sorted as titles.
        ys = sorted(tr["center"][1] for tr in cand)
        dense = max(range(len(ys)), key=lambda i: bisect.bisect_right(ys, ys[i] + 0.02)
                    - bisect.bisect_left(ys, ys[i] - 0.02))
        y0 = median([y for y in ys if abs(y - ys[dense]) <= 0.02])
        in_band = [tr for tr in cand if abs(tr["center"][1] - y0) <= 0.05]
        h0 = median([tr["height"] for tr in in_band])
        in_band = [tr for tr in in_band if 0.6 * h0 <= tr["height"] <= 1.6 * h0]
        speechy = bool(speech and in_band) and (median([tr["speech_overlap"] for tr in in_band]) or 0) >= 0.5
        if len(in_band) >= band_min or (in_band and speechy):
            band = {"y": round(y0, 4), "height": round(h0, 4), "n": len(in_band)}
            for tr in in_band:
                tr["kind"] = "subtitle"
    sub_h = band["height"] if band else None
    for tr in tracks:
        if tr.get("kind"):
            continue
        dur = tr["t_out"] - tr["t_in"]
        if tr["drift"] > 0.05 or tr["height"] < 0.015 or len(tr["text"].strip()) <= 1:
            tr["kind"] = "other"
        elif 0.5 <= dur <= 15 and (tr["height"] >= (1.2 * sub_h if sub_h else 0.04)):
            tr["kind"] = "title"
        else:
            tr["kind"] = "other"
    counts = Counter(tr["kind"] for tr in tracks)
    return {"subtitle_band": band, "counts": dict(counts)}


def flat_shots(under: list[tuple[int, int]], over: list[tuple[int, int]]) -> list[int]:
    """Lengths of the shots a viewer sees with `over` laid on top of `under`: every edge on either is a cut."""
    edges = sorted({x for span in [*under, *over] for x in span})
    return [b - a for a, b in zip(edges, edges[1:])]


SENTENCE_END = tuple("。！？!?…；;")


def quiet_cuts(db: list[float], hop_s: float, max_s: float = 28.0, min_s: float = 10.0,
               smooth_s: float = 0.3, near_db: float = 3.0) -> list[float]:
    """Cut times (s) that split a recording into pieces no longer than max_s.

    Whisper reads 30 s windows and drops its initial prompt after the first
    one, so a long clip is transcribed piece by piece. Each cut goes at the
    quietest moment (level averaged over smooth_s) between min_s and max_s
    after the previous cut; among moments within near_db of that minimum the
    latest wins, which keeps pieces long.
    """
    n = len(db)
    if n * hop_s <= max_s:
        return []
    w = max(1, round(smooth_s / hop_s))
    pref = [0.0]
    for v in db:
        pref.append(pref[-1] + v)
    sm = []
    for i in range(n):
        a = max(0, i - w // 2)
        b = min(n, a + w)
        sm.append((pref[b] - pref[a]) / (b - a))
    lo_f, hi_f = max(1, round(min_s / hop_s)), round(max_s / hop_s)
    cuts, start = [], 0
    while n - start > hi_f:
        lo, hi = start + lo_f, start + hi_f
        floor = min(sm[lo:hi])
        i = max(j for j in range(lo, hi) if sm[j] <= floor + near_db)
        cuts.append(round(i * hop_s, 3))
        start = i
    return cuts


def sentences(words: list[dict], gap_s: float = 0.8) -> list[dict]:
    """Regroup whisper words ({word, start, end[, lang]}) into sentences.

    A sentence ends at sentence-final punctuation, at a silence longer than
    gap_s, or where the language changes. Whisper's own segments end wherever
    a window or the decoder stopped, so one can hold several sentences or end
    mid-sentence. Text keeps whisper's spacing (English words carry a leading
    space, Chinese ones none).
    """
    ws = sorted((w for w in words if w["word"].strip()), key=lambda w: w["start"])
    out, cur = [], []
    for i, w in enumerate(ws):
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        if (nxt is None or w["word"].strip().endswith(SENTENCE_END) or nxt["start"] - w["end"] > gap_s
                or nxt.get("lang") != w.get("lang")):
            row = {"t_in": round(cur[0]["start"], 3), "t_out": round(cur[-1]["end"], 3),
                   "text": "".join(x["word"] for x in cur).strip()}
            if w.get("lang"):
                row["lang"] = w["lang"]
            out.append(row)
            cur = []
    return out
