#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy", "opencv-python-headless", "pillow"]
# ///
# -*- coding: utf-8 -*-
"""Check a built timeline against its plan, and graded stills against the look card.

  python3 -I -B compare.py normalize CLIP_WHERE.json... --start-frame N [--out READBACK.json]
  python3 -I -B compare.py readback READBACK.json --plan cut_plan.json [--style NAME | --style-file S]
  python3 -I -B compare.py final FILM --plan cut_plan.json [--measured DIR] [--subs S] [--grade G]
                                [--style NAME | --style-file S] [--lufs -14] [--peak -1] [--out FILE]
  uv run --python 3.13 --script compare.py look STILL... (--style NAME | --style-file S) [--out FILE]

normalize turns saved `timeline clip_where` replies (the whole MCP reply, its
          result, or the bare clip list; several replies merge) into the
          readback format below. N is the timeline's start frame from
          `timeline get_current`; clip_where does not return it. Subtitle
          items go under "subtitle" without source frames.

readback  READBACK.json is the timeline read back through the MCP after the
          build, normalised to (frames, end exclusive, absolute timeline frames):
            {"start_frame": 86400, "video": {"1": [{"start", "end",
             "source_start", "name"}], "3": [...]}, "audio": {"1": [...]}}
          Every planned clip and title must be there, in order, at its planned
          frames; a transition edge may move by half the transition. Source
          in and out points of clips at 100% speed must match within one
          source frame (59.94 media reads back one short), plus that half
          transition in source frames. Planned dialogue and ambience are
          checked on their audio tracks the same way.
          Exit 1 on any mismatch. Shot lengths are compared with the style's
          rhythm as the viewer sees them, V2 laid over V1 (reported, not failed:
          the brief may ask for a different pace, and footage may not allow it).
final     checks the exported film, not the timeline: spec (size, rate,
          length, audio), black / frozen picture / silence the plan does not
          explain, loudness (LUFS +-1, true peak), and with DIR, a prep.py
          proxy folder of the film measured by measure_video.py,
          vision_ocr.swift and measure_audio.py --sentences: cuts (+-1 frame,
          half a transition), every subtitle on screen at its time and place,
          and speech against the subtitles (median <= 0.2 s). subs.srt and
          grade.json default to the plan's folder. Exit 1 on any failure;
          "complete" is false while a check could not run. Rhythm, hard-cut
          and speech share next to the style are reported only.
look      measures exported stills (letterbox masked, 320 px, Lab, the same
          code as measure_video.py) and compares the median with the style's
          grade.look: off by more than max(floor, reference MAD) fails, with a
          hint which way to push the grade.
Reading back alone is not a pass: also compare stills and contact sheets.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402
import stats  # noqa: E402

LOOK_FLOOR = {"L01": 3, "L50": 3, "L99": 3, "Lstd": 2, "shadow": 0.03, "chroma": 3, "sh_a": 2, "sh_b": 2,
              "mid_a": 2, "mid_b": 2, "hi_a": 2.5, "hi_b": 2.5, "vignette": 0.03}
HINT = {  # (got above target, got below target)
    "L50": ("整体偏亮", "整体偏暗"), "Lstd": ("对比偏高", "对比偏低"),
    "L01": ("黑位偏灰，压一点", "黑位压得太死，抬一点"), "L99": ("白位偏高", "高光发灰，白位提一点"),
    "shadow": ("暗部占比偏多", "暗部占比偏少"), "chroma": ("饱和度偏高", "饱和度偏低"),
    "mid_b": ("中间调偏暖（黄）", "中间调偏冷（蓝）"), "mid_a": ("中间调偏品红", "中间调偏绿"),
    "sh_b": ("暗部偏暖", "暗部偏冷"), "sh_a": ("暗部偏品红", "暗部偏绿"),
    "hi_b": ("高光偏暖", "高光偏冷"), "hi_a": ("高光偏品红", "高光偏绿"),
    "vignette": ("暗角偏弱", "暗角偏重"),
}


def load_style(args) -> dict | None:
    if getattr(args, "style_file", None):
        return json.loads(Path(args.style_file).expanduser().read_text(encoding="utf-8"))
    if getattr(args, "style", None):
        try:
            path = vpaths.style_file(args.style)
        except ValueError as exc:
            raise SystemExit(f"compare: {exc}") from None
        return json.loads(path.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------- readback

def audio_wanted(plan: dict) -> dict[int, list[dict]]:
    """Audio the plan places on its own, by track. Music has its own check: it comes from a rendered bed."""
    out: dict[int, list[dict]] = {}
    for item in [*(plan.get("dialogue") or []), *(plan.get("ambient") or [])]:
        out.setdefault(item["track"], []).append(item)
    return {t: sorted(items, key=lambda x: x["rec_in"]) for t, items in out.items()}


def source_issues(label: str, w: dict, g: dict, tol_in: int, tol_out: int) -> list[str]:
    """Source in/out read back against the plan; a side the readback does not carry is not checked."""
    out = []
    if g.get("source_start") is not None and abs(g["source_start"] - w["src_in"]) > tol_in:
        out.append(f"{label}: source in {w['src_in']}, read back {g['source_start']}")
    if g.get("source_end") is not None and w.get("src_out") is not None \
            and abs(g["source_end"] - w["src_out"]) > tol_out:
        out.append(f"{label}: source out {w['src_out']}, read back {g['source_end']}")
    return out


def check_readback(rb: dict, plan: dict, style: dict | None = None) -> dict:
    s0 = int(rb.get("start_frame") or 0)
    fps = float(plan["timeline"]["fps"])
    tracks = rb.get("video") or {}
    issues: list[str] = []
    exact = 0
    plan_tracks = sorted({v["track"] for v in plan["video"]})
    wanted = [(t, [v for v in plan["video"] if v["track"] == t]) for t in plan_tracks]
    if plan.get("titles"):
        wanted.append((plan["titles"][0].get("track", 3), plan["titles"]))
    for track, want in wanted:
        got = sorted(tracks.get(str(track)) or [], key=lambda x: x["start"])
        if len(got) != len(want):
            issues.append(f"V{track}: read back {len(got)} item(s), plan has {len(want)}")
        is_title = want is plan.get("titles")
        for k, (w, g) in enumerate(zip(want, got)):
            tin = tout = 0
            if not is_title:
                prev = want[k - 1] if k else None
                if prev and prev.get("transition_out"):
                    tin = math.ceil(prev["transition_out"]["frames"] / 2)
                if w.get("transition_out"):
                    tout = math.ceil(w["transition_out"]["frames"] / 2)
            a, b = g["start"] - s0, g["end"] - s0
            if abs(a - w["rec_in"]) > tin or abs(b - w["rec_out"]) > tout:
                issues.append(f"V{track} #{k + 1} {w['id']}: planned {w['rec_in']}–{w['rec_out']}, read back {a}–{b}")
            elif a == w["rec_in"] and b == w["rec_out"]:
                exact += 1
            if not is_title and float(w.get("speed", 1)) == 1.0:
                ratio = float(w.get("source_fps") or fps) / fps
                issues += source_issues(f"V{track} #{k + 1} {w['id']}", w, g,
                                        1 + math.ceil(tin * ratio), 1 + math.ceil(tout * ratio))
    audio = rb.get("audio") or {}
    for track, want in audio_wanted(plan).items():
        got = sorted(audio.get(str(track)) or [], key=lambda x: x["start"])
        if len(got) != len(want):
            issues.append(f"A{track}: read back {len(got)} item(s), plan has {len(want)}")
        for k, (w, g) in enumerate(zip(want, got)):
            a, b = g["start"] - s0, g["end"] - s0
            if a != w["rec_in"] or b != w["rec_out"]:
                issues.append(f"A{track} #{k + 1} {w['id']}: planned {w['rec_in']}–{w['rec_out']}, read back {a}–{b}")
            else:
                exact += 1
            issues += source_issues(f"A{track} #{k + 1} {w['id']}", w, g, 1, 1)
    v1 = sorted(tracks.get(str(plan_tracks[0] if plan_tracks else 1)) or [], key=lambda x: x["start"])
    end = max((g["end"] for items in tracks.values() for g in items), default=s0) - s0
    if end != plan["timeline"]["duration_frames"]:
        issues.append(f"timeline ends at {end}, plan has {plan['timeline']['duration_frames']} frames")
    out = {"ok": not issues, "start_frame": s0, "items_exact": exact, "issues": issues}
    over = [(g["start"], g["end"]) for g in tracks.get("2") or []] if 2 in plan_tracks and "dialogue" in plan else []
    lens = [n / fps for n in stats.flat_shots([(g["start"], g["end"]) for g in v1], over)]
    if lens:
        planned = [v for v in plan["video"] if v["track"] == (plan_tracks or [1])[0]]
        planned_over = [(v["rec_in"], v["rec_out"]) for v in plan["video"] if v["track"] == 2] if over else []
        rhythm = {"got": stats.summary(lens), "plan": stats.summary(
            [n / fps for n in stats.flat_shots([(v["rec_in"], v["rec_out"]) for v in planned], planned_over)])}
        target = ((style or {}).get("rhythm") or {}).get("shot_len_s")
        if target:
            m, d = target["median"], target.get("mad") or 0
            rhythm["style"] = target
            rhythm["within_style"] = abs(rhythm["got"]["median"] - m) <= max(0.25 * m, d)
        out["rhythm"] = rhythm
    return out


# ---------------------------------------------------------------- normalize

KINDS = ("video", "audio", "subtitle")


def clips_of(reply) -> tuple[list[dict], int | None]:
    """The clip list of a saved clip_where reply (the whole MCP reply, its result, or the bare list), and total_clips."""
    if isinstance(reply, list):
        return reply, None
    body = reply.get("result", reply) if isinstance(reply, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get("clips"), list):
        raise ValueError("not a timeline clip_where reply (no clips list)")
    return body["clips"], body.get("total_clips")


def normalize(replies: list, start_frame: int) -> tuple[dict, list[str]]:
    """clip_where replies -> the readback format, plus warnings about a reply that cannot be the whole timeline."""
    seen: dict[str, dict] = {}
    totals: list[int] = []
    warnings: list[str] = []
    for reply in replies:
        clips, total = clips_of(reply)
        if total is not None:
            totals.append(total)
        for c in clips:
            seen[c.get("timeline_item_id") or f"{c['track_type']}{c['track_index']}@{c['start']}"] = c
    out: dict = {"start_frame": start_frame, **{k: {} for k in KINDS}}
    for c in seen.values():
        kind = c.get("track_type")
        if kind not in KINDS:
            warnings.append(f"skipped {c.get('name')!r}: track type {kind!r}")
            continue
        item = {"start": c["start"], "end": c["end"], "name": c.get("name")}
        if kind != "subtitle":
            item.update({k: c[k] for k in ("source_start", "source_end", "source_fps") if c.get(k) is not None})
        out[kind].setdefault(str(c["track_index"]), []).append(item)
    for kind in KINDS:
        for items in out[kind].values():
            items.sort(key=lambda x: x["start"])
        out[kind] = dict(sorted(out[kind].items(), key=lambda kv: int(kv[0])))
    if totals and len(seen) < max(totals):
        warnings.append(f"read {len(seen)} item(s), the timeline has {max(totals)}: a filtered clip_where misses some")
    early = sum(c["start"] < start_frame for c in seen.values())
    if early:
        warnings.append(f"{early} item(s) start before --start-frame {start_frame}: is that the timeline's start frame?")
    return out, warnings


# ---------------------------------------------------------------- final

PUNCT = re.compile(r"[\s，。、！？,.!?：:；;“”\"'‘’…·《》()（）\-]")


def norm_text(s: str | None) -> str:
    return PUNCT.sub("", s or "")


def at(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


def outside(a: float, b: float, spans) -> float:
    """Seconds of [a, b) that no span covers."""
    covered, t = 0.0, a
    for x, y in sorted(spans):
        x, y = max(x, t), min(y, b)
        if y > x:
            covered += y - x
            t = y
    return max(0.0, b - a - covered)


def planned_cuts(plan: dict) -> list[dict]:
    """The cuts a viewer should see, in timeline frames: V1 edges not hidden under V2, and V2's own edges.
    Two V1 items that carry on one clip's source are one shot; a transition's middle may sit half of it away."""
    total = plan["timeline"]["duration_frames"]
    v1 = sorted((v for v in plan["video"] if v["track"] == 1), key=lambda v: v["rec_in"])
    over = [(v["rec_in"], v["rec_out"]) for v in plan["video"] if v["track"] == 2]
    out: dict[int, dict] = {}
    for prev, nxt in zip(v1, v1[1:]):
        f = prev["rec_out"]
        if prev["clip"] == nxt["clip"] and abs(nxt["src_in"] - prev["src_out"]) <= 1:
            continue
        if any(a < f < b for a, b in over):
            continue
        t = prev.get("transition_out")
        out[f] = {"frame": f, "kind": t["kind"] if t else "hard_cut",
                  "tol": 1 + (math.ceil(t["frames"] / 2) if t else 0)}
    for a, b in over:
        for f in (a, b):
            if 0 < f < total:
                out.setdefault(f, {"frame": f, "kind": "cutaway", "tol": 1})
    return sorted(out.values(), key=lambda c: c["frame"])


def check_cuts(cuts: dict, plan: dict, near: int = 6, missed_share: float = 0.1) -> dict:
    """measure_video's cuts on the film against the plan. A cut found but off by more than its tolerance fails;
    so do missing cuts beyond missed_share (the detector misses some cuts between similar shots). Extra cuts
    (flashes, whip pans, a jump inside a clip) are listed."""
    fps = float(plan["timeline"]["fps"])
    got = sorted({round(float(c["t"]) * fps) for c in cuts.get("cuts") or []})
    want = planned_cuts(plan)
    used: set[int] = set()
    moved, missed = [], []
    for p in want:
        best = min(got, key=lambda f: abs(f - p["frame"]), default=None)
        d = None if best is None else best - p["frame"]
        if d is not None and abs(d) <= p["tol"]:
            used.add(best)
        elif d is not None and abs(d) <= p["tol"] + near:
            used.add(best)
            moved.append(f"{p['kind']} at frame {p['frame']} ({at(p['frame'] / fps)}) is at {best} ({d:+d})")
        else:
            missed.append(p["frame"])
    extra = [f for f in got if f not in used and all(abs(f - p["frame"]) > p["tol"] + near for p in want)]
    issues = list(moved)
    if want and len(missed) > missed_share * len(want):
        issues.append(f"{len(missed)} of {len(want)} planned cuts not found (over {missed_share:.0%}): frames "
                      + ", ".join(map(str, missed[:12])))
    return {"ok": not issues, "planned": len(want), "found": len(want) - len(missed) - len(moved),
            "moved": len(moved), "missed": missed, "extra": extra, "issues": issues}


def detect(film: Path, duration: float) -> dict:
    """Black and frozen picture, and silence, in one ffmpeg pass (picture scaled to 320 px)."""
    hw = ["-hwaccel", "videotoolbox"] if sys.platform == "darwin" else []
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", *hw, "-i", str(film),
                        "-map", "0:v:0", "-map", "0:a:0?",
                        "-vf", "scale=320:-2,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2",
                        "-af", "silencedetect=n=-50dB:d=1.5", "-f", "null", "-"], capture_output=True, text=True)
    if r.returncode:
        raise ValueError(f"ffmpeg could not read {film.name}: {r.stderr.strip()[-300:]}")
    spans: dict[str, list] = {"black": [], "freeze": [], "silence": []}
    start: dict[str, float] = {}
    for line in r.stderr.splitlines():
        if m := re.search(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)", line):
            spans["black"].append((float(m.group(1)), float(m.group(2))))
        elif m := re.search(r"(freeze|silence)_start: (-?[\d.]+)", line):
            start[m.group(1)] = max(0.0, float(m.group(2)))
        elif m := re.search(r"(freeze|silence)_end: (-?[\d.]+)", line):
            spans[m.group(1)].append((start.pop(m.group(1), 0.0), float(m.group(2))))
    for kind, a in start.items():  # still going when the file ended
        spans[kind].append((a, duration))
    return {k: [(round(a, 3), round(b, 3)) for a, b in v] for k, v in spans.items()}


def check_gaps(spans: dict, plan: dict, grade: dict | None, duration: float) -> dict:
    """Black, frozen picture and silence the plan does not explain. Explained: black at a dip-to-black and in
    night chapters, frozen picture on photos, silence where no audio is planned, and the first 1 s / last 2 s."""
    import mix

    fps = float(plan["timeline"]["fps"])
    video = plan.get("video") or []
    ends = [(0.0, 1.0), (duration - 2.0, duration)]
    dips = []
    for v in video:
        t = v.get("transition_out")
        if t and "black" in t["kind"]:
            half = t["frames"] / fps / 2 + 0.5
            dips.append((v["rec_out"] / fps - half, v["rec_out"] / fps + half))
    nights = {c["name"] for c in (grade or {}).get("chapters") or [] if c.get("night")}
    night_items = {i for i, ch in ((grade or {}).get("items") or {}).items() if ch in nights}
    dark = [(v["rec_in"] / fps, v["rec_out"] / fps) for v in video if v["id"] in night_items]
    photos = [(v["rec_in"] / fps - 0.5, v["rec_out"] / fps + 0.5) for v in video if v.get("kind") == "photo"]
    sound = [(i["rec_in"] / fps, i["rec_out"] / fps) for i in mix.items_of(plan)]
    issues = []
    for a, b in spans["black"]:
        if outside(a, b, dips + dark + ends) >= 0.5:
            issues.append(f"black {at(a)}–{at(b)}" + ("" if grade else " (no grade.json: night chapters unknown)"))
    for a, b in spans["freeze"]:
        if outside(a, b, photos + ends) >= 0.5:
            issues.append(f"frozen picture {at(a)}–{at(b)}")
    for a, b in spans["silence"]:
        b2 = min(b, duration - 2.0)  # the music fades out over the last 2 s
        if b2 > a and (b2 - a) - outside(a, b2, sound) >= 1.5:
            issues.append(f"silence {at(a)}–{at(b)} where the plan has sound")
    return {"ok": not issues, **spans, "issues": issues}


def check_spec(info: dict, plan: dict) -> dict:
    tl = plan["timeline"]
    fps = float(tl["fps"])
    want_s = tl["duration_frames"] / fps
    issues = []
    if (info.get("width"), info.get("height")) != (tl["width"], tl["height"]):
        issues.append(f"{info.get('width')}x{info.get('height')}, the timeline is {tl['width']}x{tl['height']}")
    if not info.get("fps") or abs(info["fps"] - fps) > 0.01:
        issues.append(f"{info.get('fps')} fps, the timeline is {fps}")
    if abs(float(info.get("duration_s") or 0) - want_s) > 1.5 / fps:
        issues.append(f"{info.get('duration_s')} s long, the timeline is {want_s:.3f} s")
    if not info.get("audio"):
        issues.append("no audio stream")
    return {"ok": not issues, **{k: info.get(k) for k in ("codec", "width", "height", "fps", "duration_s", "audio")},
            "issues": issues}


def check_loudness(film: Path, lufs: float, peak: float) -> dict:
    import mix

    try:
        m = mix.measure(film)
    except mix.MixError as exc:
        return {"ok": False, "issues": [str(exc)]}
    issues = []
    if m["lufs"] is None:
        issues.append("no measurable sound")
    else:
        if abs(m["lufs"] - lufs) > 1.0:
            issues.append(f"{m['lufs']} LUFS, want {lufs} ± 1: run mix.py master on an audio-only render")
        if m["true_peak_dbtp"] is not None and m["true_peak_dbtp"] > peak:
            issues.append(f"true peak {m['true_peak_dbtp']:+.1f} dBTP, ceiling {peak}: encoding can add a few tenths, "
                          "so rerun mix.py master with a lower --peak")
    return {"ok": not issues, **{k: m[k] for k in ("lufs", "true_peak_dbtp", "lra_lu")}, "issues": issues}


def parse_srt(text: str) -> list[dict]:
    stamp = r"(\d+):(\d+):(\d+)[,.](\d+)"
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            if m := re.match(rf"\s*{stamp}\s*-->\s*{stamp}", line):
                g = [int(x) for x in m.groups()]
                cues.append({"a": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                             "b": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, "text": " ".join(lines[i + 1:]).strip()})
                break
    return cues


def check_subs(text: dict, cues: list[dict], want: dict | None) -> dict:
    """Every cue read on screen (OCR tracks of the film), at its time within one OCR step, and placed like the style."""
    tracks = text.get("tracks") or []
    tol = float(text.get("step") or 0.25) + 0.1
    issues, hits = [], []
    for c in cues:
        ct = norm_text(c["text"])
        near = [t for t in tracks if t["t_out"] > c["a"] - 1 and t["t_in"] < c["b"] + 1]
        best = max(near, key=lambda t: stats.text_sim(norm_text(t["text"]), ct), default=None)
        label = f"{at(c['a'])} 「{c['text']}」"
        if best is None or stats.text_sim(norm_text(best["text"]), ct) < 0.8:
            issues.append(f"{label}: not read on screen" + (f" (closest 「{best['text']}」)" if best else ""))
            continue
        hits.append(best)
        if abs(best["t_in"] - c["a"]) > tol or abs(best["t_out"] - c["b"]) > tol:
            issues.append(f"{label}: on screen {best['t_in']:.2f}–{best['t_out']:.2f} s, "
                          f"planned {c['a']:.2f}–{c['b']:.2f} s")
    out: dict = {"cues": len(cues), "read": len(hits)}
    if hits and want:
        y, h = stats.median([t["center"][1] for t in hits]), stats.median([t["height"] for t in hits])
        out.update(center_y=round(y, 4), height=round(h, 4))
        wy, wh = float(want["position"][1]), float(want["height_frac"])
        if abs(y - wy) > 0.015:
            issues.append(f"subtitle centre y {y:.3f}, the style has {wy}: change the preset's position")
        if abs(h / wh - 1) > 0.15:
            issues.append(f"subtitle box height {h:.4f}, the style has {wh}: change the preset's size")
    return {"ok": not issues, **out, "issues": issues}


def whisper_rows(audio: dict | None) -> list[dict]:
    """Whisper's sentences on the film; never the burned-in captions, which would check the subtitles against themselves."""
    tr = (audio or {}).get("transcript") or {}
    if tr.get("whisper"):
        return tr["whisper"]
    return (tr.get("segments") or []) if tr.get("source") == "whisper" else []


def check_sync(rows: list[dict], cues: list[dict], max_s: float = 0.2, min_lines: int = 3) -> dict:
    """Median start of each heard sentence against the subtitle cue it begins. Both come from whisper, so its bias cancels."""
    offs = []
    for r in rows:
        rt = norm_text(r["text"])
        best, sim = None, 0.0
        for c in cues:
            if abs(c["a"] - r["t_in"]) <= 2.0:
                ct = norm_text(c["text"])
                s = stats.text_sim(rt[:len(ct)], ct)
                if s > sim:
                    best, sim = c, s
        if best is not None and sim >= 0.6:
            offs.append(r["t_in"] - best["a"])
    if len(offs) < min_lines:
        return {"ok": None, "skipped": f"only {len(offs)} heard line(s) matched a subtitle", "issues": []}
    med = stats.median(offs)
    issues = [] if abs(med) <= max_s else [f"speech starts {med:+.2f} s from its subtitle (median of {len(offs)} "
                                           f"lines, allowed ±{max_s}): sound and picture are out of sync"]
    return {"ok": not issues, "median_offset_s": round(med, 3), "lines": len(offs), "issues": issues}


def style_report(cuts: dict | None, audio: dict | None, style: dict | None) -> dict:
    """What the viewer gets next to the style. Reported only: the brief and the footage may ask for something else."""
    style = style or {}
    out: dict = {}
    if cuts and cuts.get("cuts") is not None:
        fps = float(cuts.get("fps") or 1)
        bounds = [0, *(c["frame"] for c in cuts["cuts"]), int(cuts.get("n_frames") or 0)]
        out["shot_len_s"] = {"got": stats.summary([(b - a) / fps for a, b in zip(bounds, bounds[1:]) if b > a]),
                             "style": (style.get("rhythm") or {}).get("shot_len_s")}
        n = cuts.get("counts") or {}
        total = (n.get("hard") or 0) + (n.get("gradual") or 0)
        if total:
            out["hard_cut_share"] = {"got": round(n["hard"] / total, 3), "style": next(
                (t.get("share") for t in style.get("transitions") or [] if t.get("type") == "hard_cut"), None)}
    if audio and audio.get("speech_share") is not None:
        out["speech_share"] = {"got": audio["speech_share"], "style": (style.get("aroll") or {}).get("speech_share")}
    return out


def final_check(film: Path, plan: dict, measured: Path | None = None, cues: list[dict] | None = None,
                grade: dict | None = None, style: dict | None = None, lufs: float = -14.0, peak: float = -1.0) -> dict:
    import prep

    def load(name):
        p = measured / name if measured else None
        return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else None

    def skip(why):
        return {"ok": None, "skipped": why, "issues": []}

    info = prep.probe(film)
    dur = float(info["duration_s"])
    mv, au = load("measure_video.json") or {}, load("measure_audio.json")
    cuts, text = mv.get("cuts"), mv.get("text") or {}
    checks = {"spec": check_spec(info, plan),
              "gaps": check_gaps(detect(film, dur), plan, grade, dur),
              "loudness": check_loudness(film, lufs, peak),
              "cuts": check_cuts(cuts, plan) if isinstance(cuts, dict) and "cuts" in cuts
              else skip("no cuts: run measure_video.py on the film's prep folder and pass it as --measured")}
    if not cues:
        checks["subtitles"] = {"ok": True, "note": "no subtitles planned", "issues": []}
        checks["sync"] = {"ok": True, "note": "no subtitles to time the speech against", "issues": []}
    else:
        want = ((style or {}).get("text") or {}).get("subtitle")
        checks["subtitles"] = check_subs(text, cues, want) if "tracks" in text \
            else skip("no OCR text: run vision_ocr.swift on the film into --measured, then measure_video.py")
        rows = whisper_rows(au)
        checks["sync"] = check_sync(rows, cues) if rows \
            else skip("no whisper sentences: run measure_audio.py --sentences on --measured")
    return {"film": film.name, "ok": all(c["ok"] is not False for c in checks.values()),
            "complete": all(c["ok"] is not None for c in checks.values()),
            "checks": checks, "style": style_report(cuts, au, style)}


# ---------------------------------------------------------------- look

def still_mask(rgb):
    """A still at the analysis width and its picture mask (letterbox bars out), as measure_video measures."""
    import cv2
    import numpy as np

    import measure_video as mv

    h, w = rgb.shape[:2]
    if w > mv.AW:
        rgb = cv2.resize(rgb, (mv.AW, max(2, round(mv.AW * h / w))), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    top, bot = mv.letterbox(g.mean(axis=1)[None, :])
    mask = np.zeros(g.shape, bool)
    mask[top:g.shape[0] - bot] = True
    return rgb, mask


def measure_stills(files: list[Path]) -> list[dict]:
    import numpy as np
    from PIL import Image

    import measure_video as mv

    rows = []
    for f in files:
        rgb, mask = still_mask(np.asarray(Image.open(f).convert("RGB")))
        st = mv.look_of(rgb, mask)
        if st:
            rows.append({"file": f.name, **st})
    return rows


def compare_look(rows: list[dict], target: dict) -> dict:
    res = {}
    for k, floor in LOOK_FLOOR.items():
        t = target.get(k)
        vals = [r[k] for r in rows if r.get(k) is not None]
        if not t or not vals:
            continue
        got = stats.median(vals)
        tol = max(floor, t.get("mad") or 0)
        d = got - t["median"]
        item = {"target": t["median"], "got": round(got, 3), "delta": round(d, 3), "tol": round(tol, 3),
                "ok": abs(d) <= tol}
        if not item["ok"]:
            item["hint"] = HINT[k][0 if d > 0 else 1]
        res[k] = item
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    nm = sub.add_parser("normalize")
    nm.add_argument("replies", type=Path, nargs="+", help="saved timeline clip_where replies")
    nm.add_argument("--start-frame", type=int, required=True, help="the timeline's start frame (timeline get_current)")
    nm.add_argument("--out", type=Path)
    rb = sub.add_parser("readback")
    rb.add_argument("readback", type=Path)
    rb.add_argument("--plan", type=Path, required=True)
    lk = sub.add_parser("look")
    lk.add_argument("stills", type=Path, nargs="+")
    lk.add_argument("--out", type=Path)
    fn = sub.add_parser("final")
    fn.add_argument("film", type=Path)
    fn.add_argument("--plan", type=Path, required=True)
    fn.add_argument("--measured", type=Path, help="prep.py folder of the film, measured")
    fn.add_argument("--subs", type=Path, help="default: subs.srt next to the plan")
    fn.add_argument("--grade", type=Path, help="default: grade.json next to the plan")
    fn.add_argument("--lufs", type=float, default=-14.0)
    fn.add_argument("--peak", type=float, default=-1.0, help="true-peak ceiling, dBTP")
    fn.add_argument("--out", type=Path)
    for p in (rb, lk, fn):
        p.add_argument("--style")
        p.add_argument("--style-file")
    args = ap.parse_args(argv)
    if args.cmd == "normalize":
        try:
            res, warns = normalize([json.loads(p.read_text(encoding="utf-8")) for p in args.replies], args.start_frame)
        except ValueError as exc:
            raise SystemExit(f"compare: {exc}") from None
        for w in warns:
            print(f"  ! {w}", file=sys.stderr)
        text = json.dumps(res, ensure_ascii=False, indent=1)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    style = load_style(args)

    if args.cmd == "readback":
        res = check_readback(json.loads(args.readback.read_text(encoding="utf-8")),
                             json.loads(args.plan.read_text(encoding="utf-8")), style)
        for i in res["issues"]:
            print(f"  ✗ {i}", file=sys.stderr)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1

    if args.cmd == "final":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        subs_path = args.subs or args.plan.parent / "subs.srt"
        grade_path = args.grade or args.plan.parent / "grade.json"
        cues = parse_srt(subs_path.read_text(encoding="utf-8")) if subs_path.exists() else None
        grade = json.loads(grade_path.read_text(encoding="utf-8")) if grade_path.exists() else None
        try:
            res = final_check(args.film.expanduser(), plan, args.measured, cues, grade, style, args.lufs, args.peak)
        except ValueError as exc:
            raise SystemExit(f"compare: {exc}") from None
        for name, c in res["checks"].items():
            mark = "?" if c["ok"] is None else "✓" if c["ok"] else "✗"
            print(f"  {mark} {name:9s} {c.get('skipped') or c.get('note') or ''}", file=sys.stderr)
            for i in c["issues"]:
                print(f"      {i}", file=sys.stderr)
        if not res["complete"]:
            print("  not complete: a skipped check is not a pass", file=sys.stderr)
        text = json.dumps(res, ensure_ascii=False, indent=1)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0 if res["ok"] else 1

    if not style:
        ap.error("look needs --style or --style-file")
    rows = measure_stills([p.expanduser() for p in args.stills])
    if not rows:
        print("no measurable stills", file=sys.stderr)
        return 2
    res = {"stills": len(rows), "look": compare_look(rows, style["grade"]["look"]), "per_still": rows}
    res["ok"] = all(v["ok"] for v in res["look"].values())
    for k, v in res["look"].items():
        mark = "✓" if v["ok"] else "✗"
        print(f"  {mark} {k:8s} 目标 {v['target']:8.2f}  实测 {v['got']:8.2f}  差 {v['delta']:+7.2f}  "
              f"容差 {v['tol']:.2f}  {v.get('hint', '')}", file=sys.stderr)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
