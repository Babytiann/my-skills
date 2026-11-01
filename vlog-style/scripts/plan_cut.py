#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a style pack + footage index + brief (+ beat grid) into cut_plan.json.

Deterministic for a given seed. Every number in the plan is an integer frame:
source frames for src_in/src_out (end exclusive), timeline frames for
rec_in/rec_out (end exclusive, 0 = timeline start; apply adds the start frame
read back from Resolve). Paths stay relative to root.

Inputs (defaults resolve under <root>/projects/<project>/):
  style.json      vlog-style/styles/<style>/style.json (repo)
  index/footage.json   shots of the user's footage, see references/apply.md
  plan/brief.json      duration, chapters, music, preferences
  index/beats.json     optional beat grid of the music (seconds from music start)

Usage:
  python3 -I -B plan_cut.py --style NAME --project NAME [--seed N] [--explain]
  python3 -I -B plan_cut.py --style-file S --index I --brief B [--beats J] --out O
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402
import schema  # noqa: E402

# Neutral transition vocabulary -> Resolve AddTransition options. Keep in sync
# with references/resolve-routes.md; names marked probe there are unverified.
RESOLVE_TRANSITIONS = {
    "dissolve": {"type": "Cross Dissolve", "category": "simple"},
    "dip_to_black": {"type": "Dip To Color Dissolve", "category": "simple"},
    "smooth_cut": {"type": "Smooth Cut", "category": "simple"},
    "flash_white": {"type": "Brightness Flash", "category": "fusion"},
    "whip": {"type": "Pan Left", "category": "fusion"},
    "zoom": {"type": "Crash Zoom", "category": "fusion"},
}
DEFAULT_FRAMES = {"dissolve": 12, "dip_to_black": 12, "smooth_cut": 4, "flash_white": 6,
                  "whip": 8, "zoom": 8, "wipe": 12, "other": 8}
ROLE_ORDER = ["hook", "intro", "chapter", "interlude", "ending"]


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def ntsc_fps(style_fps: float, clip_fps: list[float]) -> float:
    """30 -> 29.97 (etc.) when most footage runs at NTSC rates."""
    frac = [f for f in clip_fps if abs(f - round(f)) > 0.01]
    if style_fps in (24, 30, 60) and clip_fps and len(frac) * 2 > len(clip_fps):
        return round(style_fps * 1000 / 1001, 3)
    return float(style_fps)


def sample_len(rng: random.Random, dist: dict, lo: float, hi: float) -> float:
    m = float(dist["median"])
    sigma = math.log1p(1.4826 * float(dist.get("mad") or 0.3 * m) / m) if m > 0 else 0.3
    return min(hi, max(lo, m * math.exp(rng.gauss(0, sigma))))


def weighted(rng: random.Random, items: list[tuple[object, float]]):
    total = sum(w for _, w in items if w > 0)
    if total <= 0:
        return items[0][0]
    x = rng.uniform(0, total)
    for item, w in items:
        if w <= 0:
            continue
        x -= w
        if x <= 0:
            return item
    return items[-1][0]


def free_gaps(start: int, end: int, used: list[tuple[int, int]]) -> list[tuple[int, int]]:
    gaps, cur = [], start
    for a, b in sorted(used):
        if b <= cur or a >= end:
            continue
        if a > cur:
            gaps.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        gaps.append((cur, end))
    return gaps


def fceil(x: float) -> int:
    """ceil() that ignores float noise: 2.0000000001 frames is 2 frames."""
    return math.ceil(x - 1e-9)


def nearest(values: list[float], t: float) -> float | None:
    if not values:
        return None
    i = bisect.bisect_left(values, t)
    cands = [values[j] for j in (i - 1, i) if 0 <= j < len(values)]
    return min(cands, key=lambda v: abs(v - t))


# ---------------------------------------------------------------- planner

class Planner:
    def __init__(self, style: dict, index: dict, brief: dict, beats: dict | None, seed: int):
        errs = schema.check_style(style)
        if errs:
            raise PlanError("style.json invalid: " + "; ".join(errs[:5]))
        self.style, self.index, self.brief = style, index, brief
        self.rng = random.Random(seed)
        self.seed = seed
        self.warnings: list[str] = []
        clips = index.get("clips") or []
        if not clips:
            raise PlanError("footage index has no clips")
        self.clips = {c["id"]: c for c in clips}
        style_fps = brief.get("fps") or style["deliverable"]["fps"]
        self.fps = ntsc_fps(style_fps, [c["fps"] for c in clips]) if not brief.get("fps") else float(style_fps)
        self.min_shot = float(style["rhythm"].get("min_shot_s") or 0.5)
        self.used: dict[str, list[tuple[int, int]]] = {cid: [] for cid in self.clips}
        music = (brief.get("music") or [{}])[0]
        offset = float(music.get("start_s") or 0)
        self.beats = sorted(b - offset for b in (beats or {}).get("beats", []) if b >= offset)
        self.downbeats = sorted(b - offset for b in (beats or {}).get("downbeats", []) if b >= offset)

    # -- layout --------------------------------------------------------
    def sections(self) -> list[dict]:
        total = round(float(self.brief["duration_s"]) * self.fps)
        chapters = self.brief.get("chapters") or [{"name": "main", "tags": []}]
        by_role: dict[str, list[dict]] = {}
        for s in self.style["structure"]["sections"]:
            by_role.setdefault(s["role"], []).append(s)
        layout = []
        for role in ROLE_ORDER:
            if role not in by_role or (role == "interlude" and not self.brief.get("interlude_tags")):
                continue
            entries = by_role[role]
            share = sum(e["share"] for e in entries)
            ref = max(entries, key=lambda e: e["share"])
            if role == "chapter":
                weights = [float(c.get("weight") or 1) for c in chapters]
                for c, w in zip(chapters, weights):
                    layout.append({"role": role, "name": c.get("name") or f"chapter{len(layout)}",
                                   "share": share * w / sum(weights), "style": ref, "chapter": c,
                                   "tags": list(c.get("tags") or [])})
            else:
                tags = list(self.brief.get(f"{role}_tags") or [])
                if not tags and role == "intro":
                    tags = list(chapters[0].get("tags") or [])
                if not tags and role == "ending":
                    tags = list(chapters[-1].get("tags") or [])
                layout.append({"role": role, "name": role, "share": share, "style": ref,
                               "chapter": None, "tags": tags})
        norm = sum(s["share"] for s in layout)
        t = 0
        for i, s in enumerate(layout):
            n = total - t if i == len(layout) - 1 else round(total * s["share"] / norm)
            if self.downbeats and 0 < i:
                snapped = nearest(self.downbeats, t / self.fps)
                if snapped is not None and abs(snapped * self.fps - t) <= self.fps:
                    shift = round(snapped * self.fps) - t
                    if i > 0 and layout[i - 1]["frames"] + shift >= self.min_shot * self.fps:
                        layout[i - 1]["frames"] += shift
                        t += shift
                        n -= shift
            s["start"], s["frames"] = t, n
            t += n
        for s in layout:
            s["end"] = s["start"] + s["frames"]
            if s["frames"] < self.min_shot * self.fps:
                raise PlanError(f"section {s['name']} too short ({s['frames']} frames); raise duration_s")
        return layout

    def segments(self, sec: dict) -> list[tuple[int, int]]:
        fps, beat = self.fps, self.style["beat"]
        dist = sec["style"]["shot_len_s"]
        snap_p = float(beat.get("hit_rate") or 0) if self.beats and beat.get("confidence") != "uncertain" else 0.0
        grid = self.downbeats if beat.get("use_downbeats") and self.downbeats else self.beats
        off = int(beat.get("offset_frames") or 0)
        min_f = round(self.min_shot * fps)
        hi = 3.0 * float(dist["median"])
        segs, t = [], sec["start"]
        while t < sec["end"]:
            length = round(sample_len(self.rng, dist, self.min_shot, hi) * fps)
            target = t + length
            if self.rng.random() < snap_p:
                b = nearest(grid, target / fps)
                if b is not None:
                    snapped = round(b * fps) + off
                    if abs(snapped - target) <= length / 2 and snapped - t >= min_f:
                        target = snapped
            if sec["end"] - target < min_f:
                target = sec["end"]
            segs.append((t, target))
            t = target
        return segs

    # -- choices -------------------------------------------------------
    def pick_transition(self, boundary: str, next_sec: dict | None) -> str:
        if boundary != "within" and next_sec and next_sec["style"].get("transition_in"):
            return next_sec["style"]["transition_in"]
        ok = {"within": ("any", "within_section"),
              "section": ("any", "section_boundary"),
              "chapter": ("any", "section_boundary", "chapter_boundary")}[boundary]
        items = [(t["type"], t["share"]) for t in self.style["transitions"] if t.get("where", "any") in ok]
        if not any(k == "hard_cut" for k, _ in items):
            items.append(("hard_cut", 0.0))
        return weighted(self.rng, items) if items else "hard_cut"

    def transition_frames(self, kind: str) -> int:
        for t in self.style["transitions"]:
            if t["type"] == kind and t.get("frames"):
                return int(t["frames"])
        return DEFAULT_FRAMES.get(kind, 0)

    def candidates(self, sec: dict, any_tags: bool):
        avoid = set(self.brief.get("avoid") or [])
        for cid, clip in self.clips.items():
            if cid in avoid:
                continue
            for i, shot in enumerate(clip.get("shots") or []):
                if shot.get("reject"):  # marked unusable in index/tags.json
                    continue
                if any_tags or not sec["tags"] or set(sec["tags"]) & set(shot.get("tags") or []):
                    yield cid, clip, i, shot

    def score(self, sec: dict, cid: str, shot: dict, type_counts: dict, last_cid: str | None) -> float:
        sel = self.style.get("selection") or {}
        max_shake = float(sel.get("max_shake", 1.0))
        s = float(shot.get("sharp", 0.5))
        s += 0.5 if shot.get("exposure_ok", True) else -1.0
        s -= 2.0 * max(0.0, float(shot.get("shake", 0)) - max_shake)
        targets = sel.get("shot_types") or {}
        if targets and shot.get("shot_type") in targets:
            n = sum(type_counts.values()) or 1
            deficit = targets[shot["shot_type"]] - type_counts.get(shot["shot_type"], 0) / n
            s += 0.6 * max(0.0, deficit)
        if cid in set(self.brief.get("prefer") or []):
            s += 0.3
        if cid == last_cid:
            s -= 0.4
        s -= 0.2 * len(self.used[cid])  # spread the edit across clips
        if sec["role"] == "hook" and shot.get("best") is not None:
            s += 0.2
        return s + self.rng.uniform(0, 0.05)

    def place(self, cid: str, clip: dict, shot: dict, rec_len: int, speed: float,
              hl: int, hr: int) -> tuple[int, int] | None:
        ratio = clip["fps"] / self.fps * speed
        core = fceil(rec_len * ratio)
        hl_s, hr_s = fceil(hl * ratio), fceil(hr * ratio)
        need = hl_s + core + hr_s
        start, end = int(shot["start"]), int(shot["end"])
        best = int(shot.get("best", (start + end) // 2))
        fits = [g for g in free_gaps(start, end, self.used[cid]) if g[1] - g[0] >= need]
        if not fits:
            return None
        g = min(fits, key=lambda g: 0 if g[0] <= best < g[1] else min(abs(best - g[0]), abs(best - g[1])))
        src_in = min(max(best - core // 2, g[0] + hl_s), g[1] - hr_s - core)
        return src_in, src_in + core

    # -- main ----------------------------------------------------------
    def plan(self) -> dict:
        fps = self.fps
        layout = self.sections()
        timeline: list[dict] = []  # {sec, rec_in, rec_out}
        for si, sec in enumerate(layout):
            for a, b in self.segments(sec):
                timeline.append({"sec": si, "rec_in": a, "rec_out": b})
        # transitions at each boundary (attached to the outgoing clip)
        for i, seg in enumerate(timeline[:-1]):
            nxt = timeline[i + 1]
            if nxt["sec"] == seg["sec"]:
                boundary = "within"
            else:
                both_ch = layout[seg["sec"]]["role"] == layout[nxt["sec"]]["role"] == "chapter"
                boundary = "chapter" if both_ch else "section"
            kind = self.pick_transition(boundary, layout[nxt["sec"]] if boundary != "within" else None)
            seg["t_out"] = kind
        motion = self.style.get("motion") or {}
        slow = motion.get("slow_mo") or {}
        push = motion.get("push_in") or {}
        pull = motion.get("pull_out") or {}
        video, type_counts, last_cid = [], {}, None
        queue = list(timeline)
        i = 0
        while i < len(queue):
            seg = queue[i]
            sec = layout[seg["sec"]]
            if i == 0 or queue[i - 1]["sec"] != seg["sec"]:
                type_counts = {}
            prev_t = queue[i - 1].get("t_out", "hard_cut") if i > 0 else "hard_cut"
            out_t = seg.get("t_out", "hard_cut") if i < len(queue) - 1 else "hard_cut"
            hl = math.ceil(self.transition_frames(prev_t) / 2) if prev_t != "hard_cut" else 0
            hr = math.ceil(self.transition_frames(out_t) / 2) if out_t != "hard_cut" else 0
            rec_len = seg["rec_out"] - seg["rec_in"]
            want_slow = self.rng.random() < float(slow.get("share") or 0)
            choice = None
            for any_tags in (False, True):
                cands = sorted(self.candidates(sec, any_tags),
                               key=lambda c: -self.score(sec, c[0], c[3], type_counts, last_cid))
                if want_slow:
                    cands.sort(key=lambda c: 0 if c[1]["fps"] >= 1.9 * fps else 1)
                for cid, clip, shot_i, shot in cands:
                    speed = float(slow.get("speed", 0.5)) if want_slow and clip["fps"] >= 1.9 * fps else 1.0
                    for drop in (False, True):
                        l, r = (0, 0) if drop else (hl, hr)
                        win = self.place(cid, clip, shot, rec_len, speed, l, r)
                        if win:
                            choice = (cid, clip, shot_i, shot, speed, win, drop)
                            break
                    if choice:
                        break
                if choice:
                    if any_tags and sec["tags"]:
                        self.warnings.append(f"{sec['name']} @{seg['rec_in']}: no tagged shot fits, used an untagged one")
                    break
            if choice is None:
                # nothing long enough: split the segment and retry with the shorter part
                if rec_len >= 2 * round(self.min_shot * fps):
                    mid = seg["rec_in"] + rec_len // 2
                    tail = {"sec": seg["sec"], "rec_in": mid, "rec_out": seg["rec_out"]}
                    if "t_out" in seg:
                        tail["t_out"] = seg["t_out"]
                    seg["rec_out"], seg["t_out"] = mid, "hard_cut"
                    queue.insert(i + 1, tail)
                    self.warnings.append(f"{sec['name']} @{seg['rec_in']}: footage too short, split a shot")
                    continue
                raise PlanError(f"no footage fits {rec_len} frames in section {sec['name']}")
            cid, clip, shot_i, shot, speed, (src_in, src_out), dropped = choice
            if dropped and (hl or hr):
                if hl and video and video[-1]["transition_out"]:
                    video[-1]["transition_out"] = None
                seg["t_out"] = "hard_cut"
                self.warnings.append(f"{sec['name']} @{seg['rec_in']}: no handles for the transition, made it a hard cut")
            ratio = clip["fps"] / fps * speed
            hl_s = 0 if dropped else fceil(hl * ratio)
            hr_s = 0 if dropped else fceil(hr * ratio)
            self.used[cid].append((src_in - hl_s, src_out + hr_s))
            out_kind = seg.get("t_out", "hard_cut") if i < len(queue) - 1 else "hard_cut"
            mot = None
            if speed == 1.0 and shot.get("motion", "static") == "static":
                r = self.rng.random()
                if r < float(push.get("share") or 0):
                    mot = {"type": "push_in", "from": 1.0,
                           "to": round(float((push.get("scale_end") or {}).get("median", 1.06)), 3)}
                elif r < float(push.get("share") or 0) + float(pull.get("share") or 0):
                    mot = {"type": "pull_out", "from": round(float((pull.get("scale_end") or {}).get("median", 1.06)), 3),
                           "to": 1.0}
            if mot:
                mot["route"] = "fusion"
            video.append({
                "id": f"v{len(video) + 1:03d}",
                "section": sec["name"],
                "clip": cid,
                "shot": shot_i,
                "source": clip["source"],
                "source_fps": clip["fps"],
                "src_in": src_in,
                "src_out": src_out,
                "rec_in": seg["rec_in"],
                "rec_out": seg["rec_out"],
                "speed": speed,
                "track": 1,
                "audio": "keep" if speed == 1.0 else "mute",
                "tags": list(shot.get("tags") or []),
                "shot_type": shot.get("shot_type"),
                "transition_out": None if out_kind == "hard_cut" else {
                    "kind": out_kind, "frames": self.transition_frames(out_kind),
                    "resolve": RESOLVE_TRANSITIONS.get(out_kind)},
                "motion": mot,
            })
            if shot.get("shot_type"):
                type_counts[shot["shot_type"]] = type_counts.get(shot["shot_type"], 0) + 1
            last_cid = cid
            i += 1
        plan = {
            "schema_version": 1,
            "style": self.style["name"],
            "project": self.brief.get("project"),
            "seed": self.seed,
            "timeline": {"fps": fps, "width": self.style["deliverable"]["width"],
                         "height": self.style["deliverable"]["height"],
                         "duration_frames": layout[-1]["end"],
                         "letterbox": self.style["deliverable"].get("letterbox")},
            "sections": [{"name": s["name"], "role": s["role"], "rec_in": s["start"], "rec_out": s["end"]}
                         for s in layout],
            "video": video,
            "titles": self.titles(layout),
            "audio": self.music(layout[-1]["end"]),
            "speech": self.speech(video),
            "grade": {k: self.style["grade"].get(k) for k in ("lut", "drx", "grain")},
            "markers": self.markers(video),
            "warnings": self.warnings,
        }
        plan["checks"] = check_plan(plan)
        return plan

    def titles(self, layout: list[dict]) -> list[dict]:
        text = self.style.get("text") or {}
        out = []
        for sec in layout:
            ch = sec["chapter"]
            if not ch:
                continue
            t = sec["start"]
            for key, field in (("chapter_card", "card"), ("title", "title")):
                spec, words = text.get(key), ch.get(field)
                if not spec or not words or spec.get("per") == "never":
                    continue
                rec_in = max(t, sec["start"] + round(float(spec.get("offset_s", 0.5)) * self.fps))
                rec_out = min(sec["end"], rec_in + round(float(spec.get("duration_s", 3.0)) * self.fps))
                if rec_out - rec_in < round(0.5 * self.fps):
                    self.warnings.append(f"{sec['name']}: no room for the {key}")
                    continue
                out.append({"id": f"t{len(out) + 1:03d}", "kind": key, "text": words,
                            "template": spec.get("template"), "rec_in": rec_in, "rec_out": rec_out,
                            "track": 3, "style": {k: spec.get(k) for k in
                                                  ("font_class", "fallback_fonts", "height_frac", "position", "color", "stroke", "plate", "in", "out")}})
                t = rec_out
        return out

    def music(self, total: int) -> list[dict]:
        out, t = [], 0
        for m in self.brief.get("music") or []:
            if t >= total:
                break
            dur = m.get("duration_s")
            frames = total - t if dur is None else min(total - t, round((float(dur) - float(m.get("start_s") or 0)) * self.fps))
            out.append({"id": f"m{len(out) + 1:03d}", "kind": "music", "source": m["source"],
                        "src_in_s": float(m.get("start_s") or 0), "rec_in": t, "rec_out": t + frames,
                        "track": 2, "fade_out_frames": round(2 * self.fps) if t + frames == total else 0})
            t += frames
        if t < total:
            self.warnings.append(f"music ends at frame {t}, timeline is {total}")
        return out

    def speech(self, video: list[dict]) -> list[list[int]]:
        spans = []
        for v in video:
            if v["speed"] != 1.0:
                continue
            clip = self.clips[v["clip"]]
            ratio = clip["fps"] / self.fps
            for a, b in (clip["shots"][v["shot"]].get("speech") or []):
                a, b = max(a, v["src_in"]), min(b, v["src_out"])
                if b > a:
                    spans.append([v["rec_in"] + math.floor((a - v["src_in"]) / ratio),
                                  v["rec_in"] + math.ceil((b - v["src_in"]) / ratio)])
        return spans

    def markers(self, video: list[dict]) -> list[dict]:
        manual = [r["element"] for r in self.style["resolve"]
                  if r["status"] == "manual" and "播放帧率" not in r["element"]]  # always listed first
        out = [{"rec": 0, "color": "Yellow", "name": "手动收尾",
                "note": "；".join(["设置播放帧率"] + manual)}]
        for v in video:
            if v["motion"]:
                out.append({"rec": v["rec_in"], "color": "Cyan", "name": v["motion"]["type"],
                            "note": f"{v['motion']['from']}→{v['motion']['to']}，Fusion Transform"})
        return out


def check_plan(plan: dict) -> dict:
    """Structural checks the tests and apply rely on."""
    v = plan["video"]
    problems = []
    for a, b in zip(v, v[1:]):
        if a["rec_out"] != b["rec_in"]:
            problems.append(f"{a['id']}->{b['id']}: gap/overlap {a['rec_out']}..{b['rec_in']}")
    if v and (v[0]["rec_in"] != 0 or v[-1]["rec_out"] != plan["timeline"]["duration_frames"]):
        problems.append("video does not span the timeline")
    by_src: dict[str, list[tuple[int, int, str]]] = {}
    for c in v:
        if not all(isinstance(c[k], int) for k in ("src_in", "src_out", "rec_in", "rec_out")):
            problems.append(f"{c['id']}: non-integer frame")
        if c["src_out"] <= c["src_in"] or c["rec_out"] <= c["rec_in"]:
            problems.append(f"{c['id']}: empty range")
        by_src.setdefault(c["source"], []).append((c["src_in"], c["src_out"], c["id"]))
    for src, rs in by_src.items():
        rs.sort()
        for (a0, a1, ia), (b0, b1, ib) in zip(rs, rs[1:]):
            if b0 < a1:
                problems.append(f"{ia}/{ib}: reuse the same frames of {src}")
    titles = sorted(plan["titles"], key=lambda t: t["rec_in"])
    for a, b in zip(titles, titles[1:]):
        if b["rec_in"] < a["rec_out"]:
            problems.append(f"{a['id']}/{b['id']}: titles overlap")
    return {"ok": not problems, "problems": problems,
            "transitions": sum(1 for c in v if c["transition_out"]),
            "clips": len(v)}


def explain(plan: dict) -> str:
    fps = plan["timeline"]["fps"]
    tc = lambda f: f"{int(f / fps // 60):02d}:{f / fps % 60:05.2f}"  # noqa: E731
    lines = [f"# 剪辑计划：{plan.get('project') or ''}（风格 {plan['style']}，seed {plan['seed']}）",
             f"时间线 {plan['timeline']['width']}x{plan['timeline']['height']} @ {fps}，"
             f"时长 {tc(plan['timeline']['duration_frames'])}，{len(plan['video'])} 个镜头", "",
             "| # | 时间 | 段落 | 素材 | 景别/标签 | 变速 | 运动 | 转出 |", "|---|---|---|---|---|---|---|---|"]
    for c in plan["video"]:
        t = c["transition_out"]
        lines.append(f"| {c['id']} | {tc(c['rec_in'])}–{tc(c['rec_out'])} | {c['section']} | {c['clip']} "
                     f"| {c.get('shot_type') or ''} {'/'.join(c['tags'][:3])} | {'' if c['speed'] == 1 else f'{c['speed']:.0%}'} "
                     f"| {c['motion']['type'] if c['motion'] else ''} | {f'{t['kind']} {t['frames']}f' if t else '硬切'} |")
    if plan["titles"]:
        lines += ["", "文字：" + "；".join(f"{tc(t['rec_in'])} {t['text']}" for t in plan["titles"])]
    for w in plan["warnings"]:
        lines.append(f"- 注意：{w}")
    if not plan["checks"]["ok"]:
        lines += ["", "检查未通过："] + [f"- {p}" for p in plan["checks"]["problems"]]
    return "\n".join(lines)


def build(style: dict, index: dict, brief: dict, beats: dict | None, seed: int | None = None) -> dict:
    seed = brief.get("seed", 0) if seed is None else seed
    return Planner(style, index, brief, beats, seed).plan()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--style")
    ap.add_argument("--project")
    ap.add_argument("--style-file", type=Path)
    ap.add_argument("--index", type=Path)
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--beats", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--explain", action="store_true", help="print a Markdown table for the user")
    args = ap.parse_args(argv)

    if args.project:
        proj = vpaths.load().project(args.project)
        args.index = args.index or proj / "index" / "footage.json"
        args.brief = args.brief or proj / "plan" / "brief.json"
        beats_default = proj / "index" / "beats.json"
        args.beats = args.beats or (beats_default if beats_default.exists() else None)
        args.out = args.out or proj / "plan" / "cut_plan.json"
    if args.style:
        args.style_file = args.style_file or vpaths.SKILL_DIR / "styles" / vpaths.check_name(args.style) / "style.json"
    if not (args.style_file and args.index and args.brief and args.out):
        ap.error("need --style/--style-file, --index, --brief and --out (or --project)")
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    try:
        plan = build(load(args.style_file), load(args.index), load(args.brief),
                     load(args.beats) if args.beats else None, args.seed)
    except PlanError as exc:
        print(f"plan_cut: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.explain:
        print(explain(plan))
    else:
        print(json.dumps(plan["checks"], ensure_ascii=False))
    return 0 if plan["checks"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
