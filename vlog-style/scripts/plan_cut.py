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
  plan/story.json      optional paper edit: which spoken lines carry each chapter

Without a story every picture keeps its own sound, as it always has. With one,
the lines the story names become A-roll on V1 with their sound on A1, cut on
sentence boundaries; B-roll fills the gaps and cutaways go on V2.

Footage facts outrank the style. Under order: chronological (the default with a
story) a shot stays in its own time: a chapter only takes footage from its own
time window, and running short is an error, not a reason to pull in another
day. The named exceptions are the hook, the interlude, the closing photo run,
scenery marked movable, and story items with out_of_order. All of them show up
in --explain.

Usage:
  python3 -I -B plan_cut.py --style NAME --project NAME [--seed N] [--explain]
  python3 -I -B plan_cut.py --style-file S --index I --brief B [--beats J] [--story P] --out O
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402
import schema  # noqa: E402
import stats  # noqa: E402

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
# Starting values, not measurements; a style's `aroll` overrides them.
AROLL_DEFAULTS = {"pad_s": 0.15, "pad_out_s": 0.25, "cutaway_after_s": 6.0, "cutaway_share": 0.4,
                  "cutaway_lead_s": 1.5, "cutaway_tail_s": 1.0, "j_s": 0.0, "l_s": 0.0}
ORDER_TOLERANCE_S = 300.0  # shots of one scene may swap within this; further back is not allowed
LIGHTS = ["day", "golden", "blue", "night"]  # neighbours in this list can sit next to each other
FIRST_GAP_WEIGHT = 1.5  # a chapter opens wider: Links sets the scene with wide and aerial shots
CAPACITY_USABLE = 0.8


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


def epoch(iso: str) -> float:
    t = dt.datetime.fromisoformat(iso)
    return t.replace(tzinfo=t.tzinfo or dt.timezone.utc).timestamp()


def nearest(values: list[float], t: float) -> float | None:
    if not values:
        return None
    i = bisect.bisect_left(values, t)
    cands = [values[j] for j in (i - 1, i) if 0 <= j < len(values)]
    return min(cands, key=lambda v: abs(v - t))


def allocate(budget: int, weights: list[float], caps: list[float], min_f: int) -> tuple[list[int], int]:
    """Frames per gap: by weight, capped by the footage each gap can reach.

    A gap too small to hold one shot is dropped and its share goes to the others.
    Returns the frames per gap (they add up exactly) and the frames nothing could take.
    """
    active = [i for i, w in enumerate(weights) if w > 0 and caps[i] >= min_f]
    while True:
        share, left, pool = [0.0] * len(weights), float(budget), list(active)
        while pool and left > 1e-9:
            total = sum(weights[i] for i in pool)
            over = [i for i in pool if left * weights[i] / total > caps[i]]
            if not over:
                for i in pool:
                    share[i] = left * weights[i] / total
                left = 0.0
                break
            for i in over:
                share[i] = caps[i]
                left -= caps[i]
                pool.remove(i)
        small = [i for i in active if 0 < share[i] < min_f]
        if not small:
            break
        active.remove(min(small, key=lambda i: share[i]))
    out: list[int] = []
    running = 0.0
    for x in share:
        running += x
        out.append(round(running) - sum(out))
    return out, budget - sum(out)


# ---------------------------------------------------------------- planner

class Planner:
    def __init__(self, style: dict, index: dict, brief: dict, beats: dict | None, seed: int,
                 story: dict | None = None):
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
        self.story = story
        structure = style["structure"]
        self.chrono = structure.get("order", "chronological" if story is not None else "free") == "chronological"
        self.tolerance = float(structure.get("order_tolerance_s", ORDER_TOLERANCE_S))
        self.aroll = {**AROLL_DEFAULTS, **(style.get("aroll") or {})}
        self.t0: dict[str, float] = {}
        self.untimed: set[str] = set()
        self.cursor: float | None = None
        self.anchors: dict = {}
        self.windows: dict[str, tuple[float, float]] = {}
        self.spoken: list[tuple[dict, dict, dict]] = []  # (video item, anchor, section) of each A-roll
        if self.chrono:
            self.clip_times()
        if story is not None:
            self.resolve_story()
        if self.chrono:
            self.chapter_windows()

    # -- time ----------------------------------------------------------
    def clip_times(self) -> None:
        clips = self.clips.values()
        if not any(c.get("shot_at") or "order" in c for c in clips):
            raise PlanError("chronological order needs shot_at/order in the index: rerun aggregate.py footage")
        cursor = 0.0
        for c in sorted(clips, key=lambda c: c.get("order", math.inf)):
            if c.get("shot_at"):
                self.t0[c["id"]] = epoch(c["shot_at"])
            else:
                self.t0[c["id"]] = cursor
                self.untimed.add(c["id"])
            cursor = self.t0[c["id"]] + (c.get("frames") or 0) / c["fps"]
        if self.untimed:
            self.warnings.append(f"{len(self.untimed)} clip(s) have no shooting time and follow file-name order: "
                                 + ", ".join(sorted(self.untimed)))

    def time_at(self, cid: str, frame: float) -> float:
        return self.t0[cid] + frame / self.clips[cid]["fps"]

    def chapter_windows(self) -> None:
        """Each chapter's time window: from the brief, else its story lines, else its tagged shots."""
        chapters = self.brief.get("chapters") or [{"name": "main", "tags": []}]
        cores = []
        for c in chapters:
            given = c.get("window")
            lo = epoch(given[0]) if given and given[0] else None
            hi = epoch(given[1]) if given and given[1] else None
            found = self.timed_range(c)
            cores.append({"name": c.get("name"), "lo": lo, "hi": hi, "given": (lo is not None, hi is not None),
                          "found": found})
        edges = []
        for a, b in zip(cores, cores[1:]):
            left = a["hi"] if a["hi"] is not None else (a["found"][1] if a["found"] else None)
            right = b["lo"] if b["lo"] is not None else (b["found"][0] if b["found"] else None)
            if left is None or right is None:
                who = a if left is None else b
                raise PlanError(f"chapter {who['name']}: no way to tell when it happened; give it story lines, "
                                "tagged shots or a window in the brief")
            if left > right:
                raise PlanError(f"chapters {a['name']} and {b['name']} overlap in time "
                                f"({self.stamp(right)} is before {self.stamp(left)}); "
                                "chapters must follow shooting order, or set their windows in the brief")
            edges.append((left, right))
        for i, c in enumerate(cores):
            lo = c["lo"] if c["given"][0] else ((edges[i - 1][0] + edges[i - 1][1]) / 2 if i else -math.inf)
            hi = c["hi"] if c["given"][1] else ((edges[i][0] + edges[i][1]) / 2 if i < len(edges) else math.inf)
            self.windows[c["name"]] = (lo, hi)
        if self.story is not None:
            self.check_story_times(chapters)

    def timed_range(self, chapter: dict) -> tuple[float, float] | None:
        items = self.chapter_anchors(chapter.get("name"))
        timed = [a for a in items if "t0" in a and not a.get("out_of_order") and not a.get("movable")]
        if timed:
            return min(a["t0"] for a in timed), max(a["t1"] for a in timed)
        tags = set(chapter.get("tags") or [])
        spans = [(self.time_at(c["id"], s["start"]), self.time_at(c["id"], s["end"]))
                 for c in self.clips.values() for s in c.get("shots") or []
                 if tags & set(s.get("tags") or []) and not s.get("movable") and not s.get("reject")]
        return (min(a for a, _ in spans), max(b for _, b in spans)) if spans else None

    def stamp(self, t: float) -> str:
        if not math.isfinite(t):
            return "open"
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%m-%d %H:%M:%S")

    def check_story_times(self, chapters: list[dict]) -> None:
        """A paper edit is a draft too: it may not run against the clock without saying why."""
        for c in chapters:
            name = c.get("name")
            lo, hi = self.windows[name]
            last = None
            for a in self.chapter_anchors(name):
                for c in a.get("cover") or []:
                    t0 = self.time_at(c["clip"], c["src_in"])
                    t1 = self.time_at(c["clip"], c["src_out"])
                    if (t1 <= lo or t0 >= hi) and not c.get("movable") and not c.get("out_of_order"):
                        raise PlanError(f"{c['where']}: {c['clip']} was shot at {self.stamp(t0)}, outside this chapter's "
                                        f"{self.stamp(lo)} .. {self.stamp(hi)}; mark it movable if it is plain scenery, "
                                        "or set out_of_order with a reason")
                if "t0" not in a or a.get("out_of_order"):
                    continue
                if a.get("movable"):  # scenery may be placed freely, and takes no part in the order
                    continue
                if a["t1"] <= lo or a["t0"] >= hi:
                    raise PlanError(f"{a['where']}: {a['clip']} was shot at {self.stamp(a['t0'])}, outside this "
                                    f"chapter's {self.stamp(lo)} .. {self.stamp(hi)}; "
                                    "set out_of_order with a reason if that is intended")
                if last is not None and a["t0"] < last["t0"]:
                    raise PlanError(f"{a['where']}: {a['clip']} was shot at {self.stamp(a['t0'])}, before the item "
                                    f"above it ({last['clip']}, {self.stamp(last['t0'])}); "
                                    "set out_of_order with a reason if that is intended")
                last = a

    # -- story ---------------------------------------------------------
    def resolve_story(self) -> None:
        story = self.story
        names = [c.get("name") for c in self.brief.get("chapters") or []]
        roles = {s["role"] for s in self.style["structure"]["sections"]}
        chapters = story.get("chapters") or {}
        for name in chapters:
            if name not in names:
                raise PlanError(f"story chapter {name!r} is not in the brief ({', '.join(map(str, names))})")
        if chapters and "chapter" not in roles:
            raise PlanError("story has chapters but the style has no chapter section")
        if story.get("hook") and "hook" not in roles:
            raise PlanError("story has a hook but the style has no hook section")
        self.anchors = {"hook": [self.resolve_item(item, f"story.hook[{i}]", preview=True)
                                 for i, item in enumerate(story.get("hook") or [])],
                        "chapters": {name: [self.resolve_item(item, f"story.chapters[{name!r}][{i}]")
                                            for i, item in enumerate(items)]
                                     for name, items in chapters.items()}}

    def resolve_item(self, item: dict, where: str, preview: bool = False) -> dict:
        if "showcase" in item:
            if not item.get("tags") or not item.get("dur_s"):
                raise PlanError(f"{where}: a showcase needs tags and dur_s")
            return {"kind": "showcase", "where": where, "name": item["showcase"], "tags": list(item["tags"]),
                    "frames": round(float(item["dur_s"]) * self.fps)}
        cid = item.get("clip")
        clip = self.clips.get(cid)
        if clip is None:
            raise PlanError(f"{where}: clip {cid!r} is not in the index")
        if cid in set(self.brief.get("avoid") or []):
            self.warnings.append(f"{where}: {cid} is on the avoid list")
        src_in, src_out, lines = self.span_of(item, clip, where, whole_sentences=not item.get("broll"))
        shot_i = next((i for i, s in enumerate(clip["shots"]) if s["start"] <= src_in < s["end"]), 0)
        shot = clip["shots"][shot_i]
        if shot.get("reject"):
            self.warnings.append(f"{where}: {cid} shot {shot_i} is marked reject")
        ratio = clip["fps"] / self.fps
        frames = max(1, math.floor((src_out - src_in) / ratio + 1e-9))
        a = {"kind": "broll" if item.get("broll") else "aroll", "where": where, "clip": cid, "shot": shot_i,
             "src_in": src_in, "src_out": src_in + fceil(frames * ratio), "frames": frames, "lines": lines,
             "note": item.get("note"), "preview": preview, "out_of_order": item.get("out_of_order"),
             "movable": bool(shot.get("movable")) if item.get("broll") else False, "light": shot.get("light"),
             "cover": [self.resolve_cover(c, f"{where}.cover[{i}]") for i, c in enumerate(item.get("cover") or [])]}
        if self.chrono:
            a["t0"], a["t1"] = self.time_at(cid, a["src_in"]), self.time_at(cid, a["src_out"])
        return a

    def resolve_cover(self, item: dict, where: str) -> dict:
        cid = item.get("clip")
        clip = self.clips.get(cid)
        if clip is None:
            raise PlanError(f"{where}: clip {cid!r} is not in the index")
        src_in, src_out, _ = self.span_of(item, clip, where, whole_sentences=False)
        shot_i = next((i for i, s in enumerate(clip["shots"]) if s["start"] <= src_in < s["end"]), 0)
        ratio = clip["fps"] / self.fps
        frames = max(1, math.floor((src_out - src_in) / ratio + 1e-9))
        return {"where": where, "clip": cid, "shot": shot_i, "src_in": src_in, "src_out": src_in + fceil(frames * ratio),
                "frames": frames, "at_s": item.get("at_s"), "out_of_order": item.get("out_of_order"),
                "movable": bool(clip["shots"][shot_i].get("movable"))}

    def span_of(self, item: dict, clip: dict, where: str, whole_sentences: bool) -> tuple[int, int, list]:
        """Source frames of one story item. A spoken item grows or shrinks to whole sentences, then takes its pads."""
        try:
            in_s, out_s = float(item["in_s"]), float(item["out_s"])
        except (KeyError, TypeError, ValueError):
            raise PlanError(f"{where}: needs in_s and out_s in seconds") from None
        fps, total = clip["fps"], clip["frames"]
        if not 0 <= in_s < out_s or out_s * fps > total + 1:
            raise PlanError(f"{where}: {clip['id']} {in_s}-{out_s} s is outside the clip (0-{total / fps:.2f} s)")
        a, b = round(in_s * fps), min(total, round(out_s * fps))
        if not whole_sentences:
            return a, b, []
        lines = sorted(clip.get("sentences") or [])
        if not lines:
            raise PlanError(f"{where}: {clip['id']} has no sentences; measure it without --no-asr "
                            "and rerun aggregate.py footage")
        hit = [i for i, s in enumerate(lines) if s[1] > a and s[0] < b]
        if not hit:
            raise PlanError(f"{where}: {clip['id']} has no sentence between {in_s} and {out_s} s")
        first, last = hit[0], hit[-1]
        s0, s1 = lines[first][0], lines[last][1]
        lo = s0 - round(self.aroll["pad_s"] * fps)
        if first and lines[first - 1][1] > lo:  # the sentence before is inside the pad: cut half way
            lo = (lines[first - 1][1] + s0) // 2
        hi = s1 + round(self.aroll["pad_out_s"] * fps)
        if last + 1 < len(lines) and lines[last + 1][0] < hi:
            hi = (s1 + lines[last + 1][0]) // 2
        lo, hi = max(0, min(lo, s0)), min(total, max(hi, s1))
        return lo, hi, lines[first:last + 1]

    def chapter_anchors(self, name: str | None) -> list[dict]:
        return (self.anchors.get("chapters") or {}).get(name) or []

    def anchors_of(self, sec: dict) -> list[dict]:
        if sec["role"] == "hook":
            return self.anchors.get("hook") or []
        return self.chapter_anchors(sec["name"]) if sec["role"] == "chapter" else []

    def spoken_frames(self, items: list[dict]) -> int:
        return sum(a["frames"] for a in items if a["kind"] == "aroll")

    def story_duration(self) -> float:
        """Without duration_s the film runs as long as its words need at the style's share of speech."""
        share = float(self.aroll.get("speech_share") or 0)
        if share <= 0:
            raise PlanError("the brief has no duration_s and the style has no aroll.speech_share")
        spoken = self.spoken_frames(self.anchors.get("hook") or [])
        spoken += sum(self.spoken_frames(items) for items in (self.anchors.get("chapters") or {}).values())
        if not spoken:
            raise PlanError("the brief has no duration_s and the story has no spoken lines")
        return spoken / self.fps / share

    def chapter_weights(self, chapters: list[dict]) -> list[float]:
        """An unweighted chapter's weight follows its words, scaled so a written weight of 1 still means something."""
        if self.story is None:
            return [float(c.get("weight") or 1) for c in chapters]
        said = {c.get("name"): self.spoken_frames(self.chapter_anchors(c.get("name"))) for c in chapters}
        loose = [said[c.get("name")] for c in chapters if not c.get("weight") and said[c.get("name")]]
        mean = sum(loose) / len(loose) if loose else 1
        return [float(c["weight"]) if c.get("weight") else (said[c.get("name")] / mean if said[c.get("name")] else 1.0)
                for c in chapters]

    def reserve(self, layout: list[dict], total: int) -> None:
        """Sections other than chapters may need more than the style's share; chapters give the rest back."""
        norm = sum(s["share"] for s in layout)
        extra = 0.0
        for s in layout:
            s["min_frames"] = sum(a["frames"] for a in self.anchors_of(s))
            want = (s["min_frames"] + 2) / total * norm
            if s["role"] != "chapter" and s["min_frames"] and want > s["share"]:
                extra += want - s["share"]
                s["share"] = want
        room = sum(s["share"] for s in layout if s["role"] == "chapter")
        if extra and room <= extra:
            raise PlanError(f"the hook and ending need {extra / norm * total / self.fps:.1f} s more than the "
                            f"{total / self.fps:.1f} s film can spare; raise duration_s")
        for s in layout:
            if extra and s["role"] == "chapter":
                s["share"] *= (room - extra) / room

    # -- layout --------------------------------------------------------
    def sections(self) -> list[dict]:
        if self.story is not None and not self.brief.get("duration_s"):
            total = round(self.story_duration() * self.fps)
        else:
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
                weights = self.chapter_weights(chapters)
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
        if self.anchors:
            self.reserve(layout, total)
        norm = sum(s["share"] for s in layout)
        t = 0
        for i, s in enumerate(layout):
            n = total - t if i == len(layout) - 1 else round(total * s["share"] / norm)
            if self.downbeats and 0 < i:
                snapped = nearest(self.downbeats, t / self.fps)
                if snapped is not None and abs(snapped * self.fps - t) <= self.fps:
                    shift = round(snapped * self.fps) - t
                    if i > 0 and layout[i - 1]["frames"] + shift >= self.min_shot * self.fps \
                            and layout[i - 1]["frames"] + shift >= layout[i - 1].get("min_frames", -math.inf) \
                            and n - shift >= s.get("min_frames", -math.inf):
                        layout[i - 1]["frames"] += shift
                        t += shift
                        n -= shift
            s["start"], s["frames"] = t, n
            t += n
        self.check_budgets(layout)
        for s in layout:
            s["end"] = s["start"] + s["frames"]
            if s["frames"] < self.min_shot * self.fps:
                raise PlanError(f"section {s['name']} too short ({s['frames']} frames); raise duration_s")
            if self.chrono:
                s["window"] = self.window_of(s)
        return layout

    def window_of(self, sec: dict) -> tuple[float, float] | None:
        if sec["role"] == "chapter":
            return self.windows.get(sec["name"], (-math.inf, math.inf))
        spans = list(self.windows.values())
        if sec["role"] == "intro" and spans:  # sets up what follows: anything up to the end of the first chapter
            return (-math.inf, spans[0][1])
        if sec["role"] == "ending" and spans:
            return (spans[-1][0], math.inf)
        return None  # hook and interlude are outside the telling

    def check_budgets(self, layout: list[dict]) -> None:
        if not self.anchors:
            return
        fps, total = self.fps, sum(s["frames"] for s in layout)
        for s in layout:
            items = self.anchors_of(s)
            need = sum(a["frames"] for a in items)
            if need > s["frames"]:
                said = self.spoken_frames(items)
                raise PlanError(f"section {s['name']}: its story items run {need / fps:.1f} s "
                                f"({said / fps:.1f} s spoken) but the section has {s['frames'] / fps:.1f} s; "
                                "raise duration_s or weight, or cut lines from the story")
        spoken = sum(self.spoken_frames(self.anchors_of(s)) for s in layout)
        if spoken > 0.85 * total:
            self.warnings.append(f"A-roll takes {spoken / total:.0%} of the film, which leaves little room for B-roll")

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

    def limit(self, sec: dict, cid: str, clip: dict, shot: dict) -> dict | None:
        """The part of a shot this section may use, or None when the shot belongs to another time."""
        window = sec.get("window")
        if not self.chrono or window is None:
            return shot
        tol = 0.0 if cid in self.untimed else self.tolerance
        lo, hi = window
        gap = sec.get("gap")
        if gap:
            lo, hi = max(lo, gap["lo"] - tol), min(hi, gap["hi"] + tol)
        if self.cursor is not None:
            lo = max(lo, self.cursor - tol)
        fps, t0 = clip["fps"], self.t0[cid]
        start = int(shot["start"]) if lo == -math.inf else max(int(shot["start"]), math.ceil((lo - t0) * fps))
        end = int(shot["end"]) if hi == math.inf else min(int(shot["end"]), math.floor((hi - t0) * fps))
        if end <= start:
            return None
        return shot if (start, end) == (shot["start"], shot["end"]) else {**shot, "start": start, "end": end}

    def light_ok(self, sec: dict, shot: dict) -> bool:
        here = shot.get("light")
        if here not in LIGHTS:
            return False
        for other in (sec.get("gap") or {}).get("lights") or []:
            if other in LIGHTS and abs(LIGHTS.index(other) - LIGHTS.index(here)) > 1:
                return False
        return True

    def admits(self, sec: dict, cid: str, clip: dict, shot: dict, borrow: bool) -> bool:
        """In its own time, or (when borrowing) scenery that may leave it: same place, light that fits."""
        if not self.chrono:
            return not borrow
        in_time = self.limit(sec, cid, clip, shot) is not None
        if not borrow:
            return in_time
        if in_time or not shot.get("movable") or not shot.get("light"):
            return False
        if not set(sec["tags"]) & set(shot.get("tags") or []):
            return False
        return bool(sec.get("showcase")) or self.light_ok(sec, shot)

    def passes(self, sec: dict) -> list[tuple[bool, bool, bool]]:
        """(any tag, borrow, keep off speech), tried in order. What belongs to this time always comes first."""
        if sec.get("showcase"):  # the words promised this subject: never fall back to untagged shots
            return [(False, False, True), (False, True, True), (False, False, False)]
        if self.story is not None:
            return [(False, False, True), (True, False, True), (False, False, False), (True, False, False),
                    (False, True, True)]
        if self.chrono:
            return [(False, False, False), (True, False, False), (False, True, False)]
        return [(False, False, False), (True, False, False)]

    def candidates(self, sec: dict, any_tags: bool, borrow: bool = False):
        avoid = set(self.brief.get("avoid") or [])
        for cid, clip in self.clips.items():
            if cid in avoid:
                continue
            for i, shot in enumerate(clip.get("shots") or []):
                if shot.get("reject"):  # marked unusable in index/tags.json
                    continue
                if not self.admits(sec, cid, clip, shot, borrow):
                    continue
                if any_tags or not sec["tags"] or set(sec["tags"]) & set(shot.get("tags") or []):
                    yield cid, clip, i, shot

    def capacity(self, sec: dict) -> float:
        """Timeline frames of footage this gap could reach, counting only pieces long enough to use."""
        min_f = round(self.min_shot * self.fps)
        total, seen = 0.0, set()
        for borrow in (False, True):
            for cid, clip, i, shot in self.candidates(sec, True, borrow):
                if (cid, i) in seen:
                    continue
                seen.add((cid, i))
                room = shot if borrow else self.limit(sec, cid, clip, shot)
                ratio = clip["fps"] / self.fps
                for a, b in free_gaps(int(room["start"]), int(room["end"]), self.used[cid]):
                    if (b - a) / ratio >= min_f:
                        total += (b - a) / ratio
        return total

    def bias(self, sec: dict, cid: str, shot: dict) -> float:
        """Score adjustments that only exist with a story or under chronological order."""
        if self.story is None and not self.chrono:
            return 0.0
        b = 0.0
        if self.story is not None:  # the picture is muted here: somebody talking in it would be seen and not heard
            span = max(1, shot["end"] - shot["start"])
            talk = sum(max(0, min(y, shot["end"]) - max(x, shot["start"])) for x, y in shot.get("speech") or [])
            b -= 0.8 * talk / span
            if shot.get("shot_type") == "selfie":
                b -= 0.5
        if self.chrono and sec.get("window") is not None:
            t0, t1 = self.time_at(cid, shot["start"]), self.time_at(cid, shot["end"])
            scale = max(self.tolerance, 60.0)
            if self.cursor is not None and t1 < self.cursor:
                b -= (self.cursor - t1) / scale
            target = sec.get("near") if sec.get("near") is not None else self.target_time(sec)
            if target is not None:
                b -= min(1.0, max(t0 - target, target - t1, 0.0) / scale)
        return b

    def target_time(self, sec: dict) -> float | None:
        """When the footage for this moment should come from, walking through the gap's real time as the gap fills.

        Without this an early pick from late in the gap moves the clock past everything before it.
        """
        span = (sec.get("gap") or {}).get("span")
        if span is None or sec.get("progress") is None:
            return None
        return span[0] + sec["progress"] * (span[1] - span[0])

    def aim(self, room: dict, cid: str, clip: dict, target: float, rec_len: int, speed: float) -> dict:
        """Start the window at the target time rather than around the shot's sharpest frame."""
        core = fceil(rec_len * clip["fps"] / self.fps * speed)
        frame = round((target - self.t0[cid]) * clip["fps"]) + core // 2
        return {**room, "best": min(max(frame, int(room["start"])), int(room["end"]) - 1)}

    def time_span(self, sec: dict) -> tuple[float, float] | None:
        """First and last moment of the footage this stretch may use."""
        lo, hi = math.inf, -math.inf
        for cid, clip, _, shot in self.candidates(sec, True):
            room = self.limit(sec, cid, clip, shot)
            lo, hi = min(lo, self.time_at(cid, room["start"])), max(hi, self.time_at(cid, room["end"]))
        return (lo, hi) if lo < hi else None

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
        s += self.bias(sec, cid, shot)
        return s + self.rng.uniform(0, 0.05)

    def place(self, cid: str, clip: dict, shot: dict, rec_len: int, speed: float,
              hl: int, hr: int, keep_off: tuple | list = ()) -> tuple[int, int] | None:
        ratio = clip["fps"] / self.fps * speed
        core = fceil(rec_len * ratio)
        hl_s, hr_s = fceil(hl * ratio), fceil(hr * ratio)
        need = hl_s + core + hr_s
        start, end = int(shot["start"]), int(shot["end"])
        best = int(shot.get("best", (start + end) // 2))
        fits = [g for g in free_gaps(start, end, [*self.used[cid], *keep_off]) if g[1] - g[0] >= need]
        if not fits:
            return None
        g = min(fits, key=lambda g: 0 if g[0] <= best < g[1] else min(abs(best - g[0]), abs(best - g[1])))
        src_in = min(max(best - core // 2, g[0] + hl_s), g[1] - hr_s - core)
        return src_in, src_in + core

    # -- main ----------------------------------------------------------
    def pins_time(self, anchor: dict) -> bool:
        return "t0" in anchor and not anchor.get("out_of_order") and not anchor.get("movable") \
            and not anchor.get("preview")

    def gap_limits(self, sec: dict, anchors: list[dict]) -> list[dict]:
        """What each gap between anchors may show: the time between the lines around it, and their light."""
        w_lo, w_hi = sec.get("window") or (-math.inf, math.inf)
        out = []
        for k in range(len(anchors) + 1):
            before = [a for a in anchors[:k] if self.pins_time(a)]
            after = [a for a in anchors[k:] if self.pins_time(a)]
            out.append({"lo": max(w_lo, before[-1]["t1"]) if before else w_lo,
                        "hi": min(w_hi, after[0]["t0"]) if after else w_hi,
                        "lights": [a.get("light") for a in anchors[max(0, k - 1):k + 1]]})
        return out

    def edge_gap(self, sec: dict) -> dict | None:
        """Keep the intro before the first spoken line and the ending after the last, footage allowing."""
        lo, hi = sec["window"]
        said = [a for items in (self.anchors.get("chapters") or {}).values() for a in items if self.pins_time(a)]
        if sec["role"] not in ("intro", "ending") or not said:
            return None
        if sec["role"] == "intro":
            hi = min(a["t0"] for a in said)
        else:
            lo = max(a["t1"] for a in said)
        gap = {"lo": lo, "hi": hi, "lights": []}
        if CAPACITY_USABLE * self.capacity({**sec, "gap": gap}) >= sec["frames"]:
            return gap
        self.warnings.append(f"section {sec['role']}: too little footage outside the spoken lines, "
                             "it shares its time with the chapter next to it")
        return None

    def extend_tail(self, anchors: list[dict], frames: int) -> bool:
        """Give a remainder too small for any shot to the last spoken line, if the source has silence for it."""
        for a in reversed(anchors):
            if a["kind"] != "aroll":
                continue
            clip = self.clips[a["clip"]]
            ratio = clip["fps"] / self.fps
            end = a["src_out"] + fceil(frames * ratio)
            starts = [s[0] for s in clip.get("sentences") or [] if s[0] >= a["lines"][-1][1]]
            if end > clip["frames"] or (starts and end > min(starts)):
                return False
            self.used[a["clip"]].append((a["src_out"], end))
            a["src_out"], a["frames"] = end, a["frames"] + frames
            if "t1" in a:
                a["t1"] = self.time_at(a["clip"], end)
            return True
        return False

    def pieces(self, si: int, sec: dict) -> list[dict]:
        """One section's timeline: its fixed items, and segments() in the gaps between them."""
        anchors = self.anchors_of(sec)
        if not anchors:
            window = sec.get("window")
            if not window:
                return [{"sec": si, "rec_in": a, "rec_out": b} for a, b in self.segments(sec)]
            gap = self.edge_gap(sec) or {"lo": window[0], "hi": window[1], "lights": []}
            gap["span"] = self.time_span({**sec, "gap": gap})
            return [{"sec": si, "rec_in": a, "rec_out": b, "gap": gap, "progress": (a - sec["start"]) / sec["frames"]}
                    for a, b in self.segments(sec)]
        fps, min_f = self.fps, round(self.min_shot * self.fps)
        budget = sec["frames"] - sum(a["frames"] for a in anchors)
        gaps = self.gap_limits(sec, anchors)
        if self.chrono and sec.get("window"):
            for g in gaps:
                g["span"] = self.time_span({**sec, "gap": g})
        if sec["role"] == "hook":  # opens on the words; whatever is left follows them
            weights = [0.0] * len(anchors) + [1.0]
        else:
            weights = [FIRST_GAP_WEIGHT] + [1.0] * len(anchors)
        # sampled shot lengths never use a gap's footage to the last frame
        caps = [CAPACITY_USABLE * self.capacity({**sec, "gap": g}) for g in gaps]
        shares, left = allocate(budget, weights, caps, min_f)
        if left and not (left < min_f and self.extend_tail(anchors, left)):
            raise PlanError(f"section {sec['name']}: footage from this time is {left / fps:.1f} s short of the "
                            f"{budget / fps:.1f} s of B-roll it needs. Lower duration_s or this chapter's weight, "
                            "add footage, mark scenery movable, or set out_of_order on an item")
        out, t = [], sec["start"]
        for k, share in enumerate(shares):
            if share:
                out += [{"sec": si, "rec_in": a, "rec_out": b, "gap": gaps[k], "progress": (a - t) / share}
                        for a, b in self.segments({**sec, "start": t, "end": t + share})]
                t += share
            if k == len(anchors):
                break
            a = anchors[k]
            if a["kind"] == "showcase":
                out += [{"sec": si, "rec_in": x, "rec_out": y, "gap": gaps[k], "showcase": a}
                        for x, y in self.segments({**sec, "start": t, "end": t + a["frames"]})]
            else:
                out.append({"sec": si, "rec_in": t, "rec_out": t + a["frames"], "fixed": a})
            t += a["frames"]
        if t != sec["end"]:
            raise PlanError(f"section {sec['name']}: laid out to frame {t}, the section ends at {sec['end']}")
        return out

    def view_of(self, sec: dict, seg: dict) -> dict:
        if "gap" not in seg:
            return sec
        view = {**sec, "gap": seg["gap"], "progress": seg.get("progress")}
        if "showcase" in seg:
            view.update(tags=seg["showcase"]["tags"], showcase=seg["showcase"]["name"])
        return view

    def clip_item(self, n: int, sec: dict, cid: str, clip: dict, shot_i: int, shot: dict, src_in: int, src_out: int,
                  rec_in: int, rec_out: int, speed: float, track: int, out_kind: str, mot: dict | None) -> dict:
        return {
            "id": f"v{n:03d}",
            "section": sec["name"],
            "clip": cid,
            "shot": shot_i,
            "source": clip["source"],
            "source_fps": clip["fps"],
            "src_in": src_in,
            "src_out": src_out,
            "rec_in": rec_in,
            "rec_out": rec_out,
            "speed": speed,
            "track": track,
            "audio": "keep" if speed == 1.0 else "mute",
            "tags": list(shot.get("tags") or []),
            "shot_type": shot.get("shot_type"),
            "transition_out": None if out_kind == "hard_cut" else {
                "kind": out_kind, "frames": self.transition_frames(out_kind),
                "resolve": RESOLVE_TRANSITIONS.get(out_kind)},
            "motion": mot,
        }

    def place_fixed(self, seg: dict, sec: dict, video: list[dict], type_counts: dict) -> str:
        a = seg["fixed"]
        cid, clip = a["clip"], self.clips[a["clip"]]
        shot = clip["shots"][a["shot"]]
        item = self.clip_item(len(video) + 1, sec, cid, clip, a["shot"], shot, a["src_in"], a["src_out"],
                              seg["rec_in"], seg["rec_out"], 1.0, 1, "hard_cut", None)
        item["role"] = a["kind"]
        if a.get("preview"):
            item["preview"] = True
        if a.get("out_of_order"):
            item["out_of_order"] = a["out_of_order"]
        if a.get("note"):
            item["note"] = a["note"]
        if a.get("movable"):
            item["movable"] = True
        video.append(item)
        if a["kind"] == "aroll":
            self.spoken.append((item, a, sec))
        if shot.get("shot_type"):
            type_counts[shot["shot_type"]] = type_counts.get(shot["shot_type"], 0) + 1
        if self.pins_time(a):
            self.cursor = a["t0"] if self.cursor is None else max(self.cursor, a["t0"])
        return cid

    def fill(self, timeline: list[dict], layout: list[dict]) -> list[dict]:
        fps = self.fps
        motion = self.style.get("motion") or {}
        slow = motion.get("slow_mo") or {}
        push = motion.get("push_in") or {}
        pull = motion.get("pull_out") or {}
        video, type_counts, last_cid = [], {}, None
        queue = list(timeline)
        i = 0
        while i < len(queue):
            seg = queue[i]
            sec = self.view_of(layout[seg["sec"]], seg)
            if i == 0 or queue[i - 1]["sec"] != seg["sec"]:
                type_counts = {}
                self.cursor = None
            if "fixed" in seg:
                last_cid = self.place_fixed(seg, sec, video, type_counts)
                i += 1
                continue
            prev_t = queue[i - 1].get("t_out", "hard_cut") if i > 0 else "hard_cut"
            out_t = seg.get("t_out", "hard_cut") if i < len(queue) - 1 else "hard_cut"
            hl = math.ceil(self.transition_frames(prev_t) / 2) if prev_t != "hard_cut" else 0
            hr = math.ceil(self.transition_frames(out_t) / 2) if out_t != "hard_cut" else 0
            rec_len = seg["rec_out"] - seg["rec_in"]
            want_slow = self.rng.random() < float(slow.get("share") or 0)
            choice = None
            for any_tags, borrow, quiet in self.passes(sec):
                cands = sorted(self.candidates(sec, any_tags, borrow),
                               key=lambda c: -self.score(sec, c[0], c[3], type_counts, last_cid))
                if want_slow:
                    cands.sort(key=lambda c: 0 if c[1]["fps"] >= 1.9 * fps else 1)
                for cid, clip, shot_i, shot in cands:
                    speed = float(slow.get("speed", 0.5)) if want_slow and clip["fps"] >= 1.9 * fps else 1.0
                    room = shot if borrow else self.limit(sec, cid, clip, shot)
                    target = self.target_time(sec) if self.chrono and not borrow and sec.get("window") else None
                    if target is not None:
                        room = self.aim(room, cid, clip, target, rec_len, speed)
                    keep_off = [tuple(x) for x in shot.get("speech") or []] if quiet else ()
                    for drop in (False, True):
                        l, r = (0, 0) if drop else (hl, hr)
                        win = self.place(cid, clip, room, rec_len, speed, l, r, keep_off)
                        if win:
                            choice = (cid, clip, shot_i, shot, speed, win, drop, borrow)
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
                    for key in ("gap", "showcase", "progress"):
                        if key in seg:
                            tail[key] = seg[key]
                    if "t_out" in seg:
                        tail["t_out"] = seg["t_out"]
                    seg["rec_out"], seg["t_out"] = mid, "hard_cut"
                    queue.insert(i + 1, tail)
                    self.warnings.append(f"{sec['name']} @{seg['rec_in']}: footage too short, split a shot")
                    continue
                if sec.get("showcase"):
                    raise PlanError(f"showcase {sec['showcase']} in {sec['name']}: not enough footage tagged "
                                    f"{sec['tags']} for {self.gap_left(queue, i) / fps:.1f} s more; "
                                    "shorten dur_s or add footage. It will not be padded with other scenery")
                if self.chrono and sec.get("window") is not None:
                    raise PlanError(f"section {sec['name']} @{seg['rec_in'] / fps:.1f} s: footage from this time runs "
                                    f"out, {self.gap_left(queue, i) / fps:.1f} s short. Lower duration_s or this "
                                    "chapter's weight, add footage, mark scenery movable, or set out_of_order")
                raise PlanError(f"no footage fits {rec_len} frames in section {sec['name']}")
            cid, clip, shot_i, shot, speed, (src_in, src_out), dropped, borrowed = choice
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
            item = self.clip_item(len(video) + 1, sec, cid, clip, shot_i, shot, src_in, src_out,
                                  seg["rec_in"], seg["rec_out"], speed, 1, out_kind, mot)
            if borrowed:
                item.update(borrowed=True, movable=True, light=shot.get("light"))
            if sec.get("showcase"):
                item["showcase"] = sec["showcase"]
            video.append(item)
            if self.chrono and not borrowed and not sec.get("showcase") and sec.get("window") is not None:
                t = self.time_at(cid, src_in)  # only ever forward: small swaps must not add up to going back
                self.cursor = t if self.cursor is None else max(self.cursor, t)
            if shot.get("shot_type"):
                type_counts[shot["shot_type"]] = type_counts.get(shot["shot_type"], 0) + 1
            last_cid = cid
            i += 1
        return video

    @staticmethod
    def gap_left(queue: list[dict], i: int) -> int:
        """Frames still to fill in this stretch of the section, up to the next fixed item."""
        total = 0
        for seg in queue[i:]:
            if seg["sec"] != queue[i]["sec"] or "fixed" in seg:
                break
            total += seg["rec_out"] - seg["rec_in"]
        return total

    def reserve_fixed(self) -> None:
        """Story items claim their frames first, or B-roll placed earlier would take them."""
        items = list(self.anchors.get("hook") or [])
        for chapter in (self.anchors.get("chapters") or {}).values():
            items += chapter
        for a in items:
            if a["kind"] != "showcase":
                self.used[a["clip"]].append((a["src_in"], a["src_out"]))
            for c in a.get("cover") or []:
                self.used[c["clip"]].append((c["src_in"], c["src_out"]))

    def pick_cutaway(self, sec: dict, near: dict, wide: dict, rec_len: int, skip: str | None, last: str | None):
        """A picture for over somebody's words: close to them in time first, then anywhere in the chapter."""
        for view in (near, wide):
            for any_tags, borrow, quiet in self.passes(view):
                cands = sorted(self.candidates(view, any_tags, borrow),
                               key=lambda c: -self.score(view, c[0], c[3], {}, last) + (0.5 if c[0] == skip else 0.0))
                for cid, clip, shot_i, shot in cands:
                    if shot.get("shot_type") == "selfie":  # a talking face over somebody else's words
                        continue
                    room = shot if borrow else self.limit(view, cid, clip, shot)
                    keep_off = [tuple(x) for x in shot.get("speech") or []] if quiet else ()
                    win = self.place(cid, clip, room, rec_len, 1.0, 0, 0, keep_off)
                    if win:
                        return cid, clip, shot_i, shot, win, borrow
        return None

    def cutaways(self, first_n: int) -> list[dict]:
        """V2 over long A-roll. Picture and exposed speaker both run at the style's pace, so the cut reads like it."""
        out: list[dict] = []
        fps, a = self.fps, self.aroll
        min_f = round(self.min_shot * fps)
        lead, tail = round(a["cutaway_lead_s"] * fps), round(a["cutaway_tail_s"] * fps)
        share = float(a["cutaway_share"])
        dist = self.style["rhythm"]["shot_len_s"]
        hi = 3.0 * float(dist["median"])
        expose = (1 - share) / share if share > 0 else 0.0
        draw = lambda: round(sample_len(self.rng, dist, self.min_shot, hi) * fps)  # noqa: E731
        self.cursor = None
        for v, anchor, sec in self.spoken:
            covers = list(anchor.get("cover") or [])
            length = v["rec_out"] - v["rec_in"]
            auto = share > 0 and length > float(a["cutaway_after_s"]) * fps
            if not covers and not auto:
                continue
            end = v["rec_out"] - tail
            t = v["rec_in"] + max(lead, round(draw() * expose)) if auto else v["rec_in"] + lead
            base = {k: x for k, x in sec.items() if k not in ("gap", "showcase")}
            near = {"lo": anchor.get("t0", -math.inf), "hi": anchor.get("t1", math.inf), "lights": [anchor.get("light")]}
            near_view = {**base, "gap": near, "near": anchor.get("t0")}
            window = sec.get("window") or (-math.inf, math.inf)
            wide_view = {**base, "gap": {"lo": window[0], "hi": window[1], "lights": [anchor.get("light")]},
                         "near": anchor.get("t0")}
            missed = False
            while t + min_f <= end and (covers or auto):
                if covers:
                    c = covers.pop(0)
                    if c.get("at_s") is not None:
                        t = max(t, v["rec_in"] + round(float(c["at_s"]) * fps))
                    n = min(c["frames"], end - t)
                    if n < min_f:
                        self.warnings.append(f"{c['where']}: no room for this cover on the line it belongs to")
                        continue
                    clip = self.clips[c["clip"]]
                    shot = clip["shots"][c["shot"]]
                    win = (c["src_in"], c["src_in"] + fceil(n * clip["fps"] / fps))
                    picked = (c["clip"], clip, c["shot"], shot, win, False)
                    forced = c
                else:
                    n = min(draw(), end - t)
                    if n < min_f:
                        break
                    picked = self.pick_cutaway(sec, near_view, wide_view, n, v["clip"], None)
                    forced = None
                    if picked is None:
                        missed = True
                        break
                cid, clip, shot_i, shot, (src_in, src_out), borrowed = picked
                if forced is None:
                    self.used[cid].append((src_in, src_out))
                item = self.clip_item(first_n + len(out), sec, cid, clip, shot_i, shot, src_in, src_out,
                                      t, t + n, 1.0, 2, "hard_cut", None)
                item["role"] = "cutaway"
                if borrowed:
                    item.update(borrowed=True, movable=True, light=shot.get("light"))
                if forced is not None:
                    item["chosen"] = True
                    if forced.get("out_of_order"):
                        item["out_of_order"] = forced["out_of_order"]
                    if forced.get("movable"):
                        item["movable"] = True
                out.append(item)
                t += n + max(min_f, round(draw() * expose))
            if missed:
                self.warnings.append(f"{v['id']}: no picture from this time to cut away to, the speaker stays on screen")
            for c in covers:
                self.warnings.append(f"{c['where']}: no room for this cover on the line it belongs to")
        return out

    def dialogue(self) -> list[dict]:
        """A1. J and L reach into the silence around a line, never into the sentence before or after it."""
        out: list[dict] = []
        fps = self.fps
        j, l = round(float(self.aroll["j_s"]) * fps), round(float(self.aroll["l_s"]) * fps)
        last_out = 0
        for n, (v, a, _) in enumerate(self.spoken):
            clip = self.clips[v["clip"]]
            ratio = clip["fps"] / fps
            lines = sorted(clip.get("sentences") or [])
            first = lines.index(a["lines"][0])
            last = lines.index(a["lines"][-1])
            before = lines[first - 1][1] if first else 0
            after = lines[last + 1][0] if last + 1 < len(lines) else clip["frames"]
            lead = max(0, min(j, v["rec_in"] - last_out, math.floor((v["src_in"] - before) / ratio)))
            nxt = self.spoken[n + 1][0]["rec_in"] if n + 1 < len(self.spoken) else self.total_frames
            trail = max(0, min(l, nxt - v["rec_out"], math.floor((after - v["src_out"]) / ratio)))
            d = {"id": f"d{n + 1:03d}", "clip": v["clip"], "source": v["source"], "source_fps": clip["fps"],
                 "src_in": v["src_in"] - fceil(lead * ratio), "src_out": v["src_out"] + fceil(trail * ratio),
                 "rec_in": v["rec_in"] - lead, "rec_out": v["rec_out"] + trail, "track": 1,
                 "sentences": a["lines"], "clear": [before, after]}
            v["dialogue"] = d["id"]
            last_out = d["rec_out"]
            out.append(d)
        return out

    def dialogue_speech(self, dialogue: list[dict]) -> list[list[int]]:
        spans = []
        for d in dialogue:
            ratio = d["source_fps"] / self.fps
            for a, b, _ in d["sentences"]:
                a, b = max(a, d["src_in"]), min(b, d["src_out"])
                if b > a:
                    spans.append([d["rec_in"] + math.floor((a - d["src_in"]) / ratio),
                                  d["rec_in"] + math.ceil((b - d["src_in"]) / ratio)])
        return spans

    def ambient(self, video: list[dict]) -> list[dict]:
        db = (self.style.get("audio") or {}).get("broll_ambient_db")
        if db is None:
            return []
        return [{"id": f"a{n + 1:03d}", "clip": v["clip"], "source": v["source"], "source_fps": v["source_fps"],
                 "src_in": v["src_in"], "src_out": v["src_out"], "rec_in": v["rec_in"], "rec_out": v["rec_out"],
                 "track": 4, "db": db}
                for n, v in enumerate(x for x in video if x.get("role") == "broll" and x["speed"] == 1.0)]

    def plan(self) -> dict:
        if self.anchors:
            self.reserve_fixed()
        layout = self.sections()
        self.total_frames = layout[-1]["end"]
        timeline: list[dict] = []  # {sec, rec_in, rec_out}
        for si, sec in enumerate(layout):
            timeline += self.pieces(si, sec)
        # transitions at each boundary (attached to the outgoing clip)
        for i, seg in enumerate(timeline[:-1]):
            nxt = timeline[i + 1]
            if nxt["sec"] == seg["sec"]:
                boundary = "within"
            else:
                both_ch = layout[seg["sec"]]["role"] == layout[nxt["sec"]]["role"] == "chapter"
                boundary = "chapter" if both_ch else "section"
            if "fixed" in seg or "fixed" in nxt:  # a spoken line starts and ends on a hard cut
                seg["t_out"] = "hard_cut"
                wanted = layout[nxt["sec"]]["style"].get("transition_in")
                if boundary != "within" and wanted and wanted != "hard_cut":
                    self.warnings.append(f"{layout[nxt['sec']]['name']}: {wanted} into this section dropped, "
                                         "a spoken line stays on a hard cut")
                continue
            kind = self.pick_transition(boundary, layout[nxt["sec"]] if boundary != "within" else None)
            seg["t_out"] = kind
        video = self.fill(timeline, layout)
        return self.assemble(layout, video)

    def assemble(self, layout: list[dict], video: list[dict]) -> dict:
        fps = self.fps
        dialogue: list[dict] = []
        if self.story is not None:
            for v in video:
                v.setdefault("role", "broll")
                v["audio"] = "mute"
            video = video + self.cutaways(len(video) + 1)
            dialogue = self.dialogue()
            for v in video:
                v["audio"] = "mute"
        if self.chrono:
            for v in video:
                v["t_src"] = round(self.time_at(v["clip"], v["src_in"]), 3)
                v["day"] = self.clips[v["clip"]].get("day")
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
            "speech": self.speech(video) if self.story is None else self.dialogue_speech(dialogue),
            "grade": {k: self.style["grade"].get(k) for k in ("lut", "drx", "grain")},
            "markers": self.markers(video),
            "warnings": self.warnings,
        }
        if self.chrono:
            for s, out in zip(layout, plan["sections"]):
                if s.get("window"):
                    out["window"] = [x if math.isfinite(x) else None for x in s["window"]]
            plan["order"] = {"mode": "chronological", "tolerance_s": self.tolerance, "untimed": sorted(self.untimed)}
        if self.story is not None:
            plan["dialogue"] = dialogue
            plan["ambient"] = self.ambient(video)
            plan["story_stats"] = self.story_stats(plan)
        plan["checks"] = check_plan(plan)
        return plan

    def story_stats(self, plan: dict) -> dict:
        fps, total = self.fps, plan["timeline"]["duration_frames"]
        v1 = [v for v in plan["video"] if v["track"] == 1]
        v2 = [v for v in plan["video"] if v["track"] == 2]
        spoken = sum(v["rec_out"] - v["rec_in"] for v in v1 if v.get("role") == "aroll")
        flat = stats.flat_shots([(v["rec_in"], v["rec_out"]) for v in v1], [(v["rec_in"], v["rec_out"]) for v in v2])
        borrowed: dict[str, float] = {}
        for v in plan["video"]:
            if v.get("borrowed"):
                borrowed[v["section"]] = round(borrowed.get(v["section"], 0.0) + (v["rec_out"] - v["rec_in"]) / fps, 2)
        return {"aroll_share": round(spoken / max(1, total), 3), "aroll_share_style": self.aroll.get("speech_share"),
                "flat_shot_s": round(stats.median([n / fps for n in flat]), 2) if flat else None,
                "flat_shot_s_style": self.style["rhythm"]["shot_len_s"]["median"], "borrowed_s": borrowed}

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
            if self.story is not None and out:  # a film going silent before its end cannot be published as is
                raise PlanError(f"the music runs out at {t / self.fps:.1f} s and the film is {total / self.fps:.1f} s; "
                                "add a track to brief.music or shorten the film")
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


def check_story(plan: dict, problems: list[str]) -> None:
    """V2 and A1. A sentence edge may sit under one timeline frame off: frame rates don't always divide evenly."""
    fps = plan["timeline"]["fps"]
    arolls = [v for v in plan["video"] if v["track"] == 1 and v.get("role") == "aroll"]
    v2 = sorted((v for v in plan["video"] if v["track"] == 2), key=lambda v: v["rec_in"])
    for c in v2:
        if not any(a["rec_in"] <= c["rec_in"] and c["rec_out"] <= a["rec_out"] for a in arolls):
            problems.append(f"{c['id']}: cutaway {c['rec_in']}..{c['rec_out']} is not over one A-roll")
    for a, b in zip(v2, v2[1:]):
        if b["rec_in"] < a["rec_out"]:
            problems.append(f"{a['id']}/{b['id']}: cutaways overlap")
    dialogue = sorted(plan["dialogue"], key=lambda d: d["rec_in"])
    for a, b in zip(dialogue, dialogue[1:]):
        if b["rec_in"] < a["rec_out"]:
            problems.append(f"{a['id']}/{b['id']}: dialogue overlaps on A1")
    for d in dialogue:
        tol = d["source_fps"] / fps
        for x, y, text in d["sentences"]:
            if d["src_in"] >= x + tol or d["src_out"] <= y - tol:
                problems.append(f"{d['id']}: source {d['src_in']}..{d['src_out']} cuts into a sentence ({x}..{y}: {text[:12]})")
        if d["src_in"] <= d["clear"][0] - tol or d["src_out"] >= d["clear"][1] + tol:
            problems.append(f"{d['id']}: source {d['src_in']}..{d['src_out']} reaches into the sentence next door")


def check_order(plan: dict, problems: list[str]) -> None:
    """Footage in its own time, read back from the plan alone: this also catches the picker being wrong."""
    secs = {s["name"]: s for s in plan["sections"]}
    tol = plan["order"]["tolerance_s"]
    untimed = set(plan["order"]["untimed"])
    cursor: dict[str, float] = {}
    for v in sorted(plan["video"], key=lambda v: (v["track"], v["rec_in"])):
        window = secs[v["section"]].get("window")
        if window is None or "t_src" not in v or v.get("out_of_order"):
            continue
        if v.get("borrowed"):
            if not v.get("movable") or not v.get("light"):
                problems.append(f"{v['id']}: borrowed from another time but not marked movable with a light")
            continue
        if v.get("movable") or v.get("showcase"):
            continue
        t0, t1 = v["t_src"], v["t_src"] + (v["src_out"] - v["src_in"]) / v["source_fps"]
        lo, hi = window
        if (hi is not None and t0 > hi + 0.5) or (lo is not None and t1 < lo - 0.5):
            problems.append(f"{v['id']}: {v['clip']} was shot outside the time of {v['section']}")
        if v["track"] == 1:
            allowed = 0.0 if v["clip"] in untimed else tol
            last = cursor.get(v["section"])
            if last is not None and t0 < last - allowed - 0.5:
                problems.append(f"{v['id']}: {v['clip']} runs backward in {v['section']} by {last - t0:.0f} s")
            cursor[v["section"]] = max(last, t0) if last is not None else t0


def check_plan(plan: dict) -> dict:
    """Structural checks the tests and apply rely on."""
    v = plan["video"]
    v1 = [c for c in v if c["track"] == 1]
    problems = []
    for a, b in zip(v1, v1[1:]):
        if a["rec_out"] != b["rec_in"]:
            problems.append(f"{a['id']}->{b['id']}: gap/overlap {a['rec_out']}..{b['rec_in']}")
    if v1 and (v1[0]["rec_in"] != 0 or v1[-1]["rec_out"] != plan["timeline"]["duration_frames"]):
        problems.append("video does not span the timeline")
    hooks = {s["name"] for s in plan["sections"] if s["role"] == "hook"}
    by_src: dict[str, list[tuple[int, int, str]]] = {}
    for c in v:
        if not all(isinstance(c[k], int) for k in ("src_in", "src_out", "rec_in", "rec_out")):
            problems.append(f"{c['id']}: non-integer frame")
        if c["src_out"] <= c["src_in"] or c["rec_out"] <= c["rec_in"]:
            problems.append(f"{c['id']}: empty range")
        if c["section"] not in hooks:  # a cold open previews lines that are heard again later
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
    if "dialogue" in plan:
        check_story(plan, problems)
    if "order" in plan:
        check_order(plan, problems)
    return {"ok": not problems, "problems": problems,
            "transitions": sum(1 for c in v if c["transition_out"]),
            "clips": len(v)}


def when(v: dict) -> str:
    """Where a picture sits in real time, for the tables the user reads."""
    if "t_src" not in v:
        return ""
    day = f"D{v['day']} " if v.get("day") else ""
    return day + dt.datetime.fromtimestamp(v["t_src"], dt.timezone.utc).strftime("%H:%M")


def out_of_time(v: dict) -> str:
    """Every named exception to the clock, spelled out in the table."""
    marks = []
    if v.get("preview"):
        marks.append("预告")
    if v.get("borrowed"):
        marks.append(f"借用 D{v['day']}" if v.get("day") else "借用")
    if v.get("showcase"):
        marks.append(f"风景段落：{v['showcase']}")
    if v.get("out_of_order"):
        marks.append(f"顺序例外：{v['out_of_order']}")
    return "；".join(marks)


def explain_story(plan: dict) -> str:
    fps = plan["timeline"]["fps"]
    tc = lambda f: f"{int(f / fps // 60):02d}:{f / fps % 60:05.2f}"  # noqa: E731
    v1 = [v for v in plan["video"] if v["track"] == 1]
    v2 = [v for v in plan["video"] if v["track"] == 2]
    said = {d["id"]: d for d in plan["dialogue"]}
    st = plan["story_stats"]
    target = f"（风格 {st['aroll_share_style']:.0%}）" if st.get("aroll_share_style") else ""
    lines = [f"# 剪辑计划：{plan.get('project') or ''}（风格 {plan['style']}，seed {plan['seed']}）",
             f"时间线 {plan['timeline']['width']}x{plan['timeline']['height']} @ {fps}，"
             f"时长 {tc(plan['timeline']['duration_frames'])}；V1 {len(v1)} 条，V2 {len(v2)} 条，对白 {len(said)} 条",
             f"A-roll 占比 {st['aroll_share']:.0%}{target}；压平后的画面中位数 {st['flat_shot_s']} s"
             f"（风格 {st['flat_shot_s_style']} s）。风格是范围，差得多要说明原因，不为此改内容。", "",
             "| # | 时间 | 段落 | 角色 | 素材 | 拍摄 | 景别/标签 | 说明 | 转出 |", "|---|---|---|---|---|---|---|---|---|"]
    for c in v1:
        t = c["transition_out"]
        first = said[c["dialogue"]]["sentences"][0][2] if c.get("dialogue") else ""
        note = "；".join(x for x in (first, c.get("note"), out_of_time(c)) if x)
        lines.append(f"| {c['id']} | {tc(c['rec_in'])}–{tc(c['rec_out'])} | {c['section']} | {c['role']} | {c['clip']} "
                     f"| {when(c)} | {c.get('shot_type') or ''} {'/'.join(c['tags'][:3])} | {note} "
                     f"| {f'{t['kind']} {t['frames']}f' if t else '硬切'} |")
    if v2:
        lines += ["", "**V2 盖在说话上的画面**", "", "| # | 时间 | 段落 | 素材 | 拍摄 | 景别/标签 | 说明 |", "|---|---|---|---|---|---|---|"]
        for c in v2:
            note = "；".join(x for x in ("指定" if c.get("chosen") else "", out_of_time(c)) if x)
            lines.append(f"| {c['id']} | {tc(c['rec_in'])}–{tc(c['rec_out'])} | {c['section']} | {c['clip']} "
                         f"| {when(c)} | {c.get('shot_type') or ''} {'/'.join(c['tags'][:3])} | {note} |")
    if said:
        lines += ["", "**A1 对白**", "", "| # | 时间 | 素材 | 句数 | 首句 |", "|---|---|---|---|---|"]
        for d in plan["dialogue"]:
            lines.append(f"| {d['id']} | {tc(d['rec_in'])}–{tc(d['rec_out'])} | {d['clip']} | {len(d['sentences'])} "
                         f"| {d['sentences'][0][2]} |")
    if st["borrowed_s"]:
        lines += ["", "借用的镜头（不在本章时间内的风景）：" + "；".join(f"{k} {v} s" for k, v in st["borrowed_s"].items())]
    if plan["titles"]:
        lines += ["", "文字：" + "；".join(f"{tc(t['rec_in'])} {t['text']}" for t in plan["titles"])]
    for w in plan["warnings"]:
        lines.append(f"- 注意：{w}")
    if not plan["checks"]["ok"]:
        lines += ["", "检查未通过："] + [f"- {p}" for p in plan["checks"]["problems"]]
    return "\n".join(lines)


def explain(plan: dict) -> str:
    if "dialogue" in plan:
        return explain_story(plan)
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
    for c in plan["video"]:
        if out_of_time(c):
            lines.append(f"- {c['id']} {tc(c['rec_in'])} {c['clip']} {when(c)}：{out_of_time(c)}")
    for w in plan["warnings"]:
        lines.append(f"- 注意：{w}")
    if not plan["checks"]["ok"]:
        lines += ["", "检查未通过："] + [f"- {p}" for p in plan["checks"]["problems"]]
    return "\n".join(lines)


def build(style: dict, index: dict, brief: dict, beats: dict | None, seed: int | None = None,
          story: dict | None = None) -> dict:
    seed = brief.get("seed", 0) if seed is None else seed
    return Planner(style, index, brief, beats, seed, story).plan()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--style")
    ap.add_argument("--project")
    ap.add_argument("--style-file", type=Path)
    ap.add_argument("--index", type=Path)
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--beats", type=Path)
    ap.add_argument("--story", type=Path)
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
        story_default = proj / "plan" / "story.json"
        args.story = args.story or (story_default if story_default.exists() else None)
        args.out = args.out or proj / "plan" / "cut_plan.json"
    if args.style and not args.style_file:
        try:
            args.style_file = vpaths.style_file(args.style)
        except ValueError as exc:
            print(f"plan_cut: {exc}", file=sys.stderr)
            return 1
    if not (args.style_file and args.index and args.brief and args.out):
        ap.error("need --style/--style-file, --index, --brief and --out (or --project)")
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    try:
        plan = build(load(args.style_file), load(args.index), load(args.brief),
                     load(args.beats) if args.beats else None, args.seed,
                     load(args.story) if args.story else None)
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
