#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dialogue levels and the film's loudness, as AudioVolume values for Resolve.

  python3 -I -B mix.py dialogue --project NAME [--target -16] [--max-gain 12] [--lr-db 6]
  python3 -I -B mix.py master --project NAME --render FILE [--lufs -14] [--peak -1]
  (--plan cut_plan.json instead of --project; mix.json is written next to it)

dialogue  measures each dialogue[] item over its own source range (ffmpeg
          ebur128, integrated and gated, so pauses barely count) and sets
          gain_db to bring it to --target LUFS, clamped to +-max-gain. An item
          too short or too quiet to measure keeps 0 dB and is listed. A stereo
          source whose channels differ by more than --lr-db is flagged: only
          one side may have a mic, and the film would talk from one speaker.
master    measures an audio-only render of the timeline as built with the
          current mix.json, and shifts every audio item by the same master_db
          so the film lands on --lufs, unless that would put the true peak
          above --peak: then the peak wins and the shortfall is reported (a
          limiter on the main bus is a manual finish). master_db accumulates,
          so a second render after writing the new values refines it.

plan/mix.json, relative paths only:
  {"target_lufs", "max_gain_db", "master": {"master_db", "render", "measured",
   "expected", "lufs", "peak"}, "items": [{"id", "kind", "track", "rec_in",
   "rec_out", "base_db", "measured_lufs", "volume_db"}], "warnings"}
volume_db goes into `timeline_item set_property {key: "AudioVolume"}` on the
audio item at that track and record frame (timeline start + rec_in). Items:
dialogue (A1, levelled), ambience (A4, the style's broll_ambient_db), music
beds (A2), and without a story the clips that keep their own sound. The raw
music on A3 is muted and left out.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

VOLUME_RANGE = (-100.0, 30.0)  # what AudioVolume accepts (resolve-routes.md)
UNMEASURED = -69.0  # ebur128 reports -70 LUFS when nothing passes the gate


class MixError(Exception):
    pass


def measure(path: Path, start_s: float = 0.0, dur_s: float | None = None) -> dict:
    """Integrated loudness, true peak, LRA and per-channel RMS of the first audio stream (or a range of it)."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"]
    if start_s:
        cmd += ["-ss", f"{start_s:.6f}"]
    if dur_s is not None:
        cmd += ["-t", f"{dur_s:.6f}"]
    cmd += ["-i", str(path), "-map", "0:a:0", "-af",
            "astats=measure_overall=none:measure_perchannel=RMS_level,ebur128=peak=true:framelog=verbose",
            "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise MixError(f"ffmpeg could not read the audio of {path.name}: {r.stderr.strip()[-300:]}")
    rms, chan = [], None
    for line in r.stderr.splitlines():
        if m := re.search(r"Channel: (\d+)", line):
            chan = int(m.group(1))
        elif chan and (m := re.search(r"RMS level dB: (-?inf|-?[\d.]+)", line)):
            rms.append(float(m.group(1)))
            chan = None
    tail = r.stderr[r.stderr.rfind("Summary:"):]

    def grab(pat):
        m = re.search(pat, tail)
        return float(m.group(1)) if m else None
    i = grab(r"I:\s+(-?[\d.]+) LUFS")
    return {"lufs": i if i is not None and i > UNMEASURED else None, "lra_lu": grab(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbtp": grab(r"Peak:\s+(-?inf|-?[\d.]+) dBFS"), "channel_rms_db": rms}


def items_of(plan: dict) -> list[dict]:
    """Every audio item the timeline plays, with the level it starts from (before master)."""
    out = [{"id": d["id"], "kind": "dialogue", "track": d["track"], "rec_in": d["rec_in"], "rec_out": d["rec_out"],
            "base_db": 0.0} for d in plan.get("dialogue") or []]
    out += [{"id": a["id"], "kind": "ambient", "track": a["track"], "rec_in": a["rec_in"], "rec_out": a["rec_out"],
             "base_db": float(a.get("db") or 0)} for a in plan.get("ambient") or []]
    out += [{"id": m["id"], "kind": "music", "track": m.get("track", 2), "rec_in": m["rec_in"],
             "rec_out": m["rec_out"], "base_db": 0.0} for m in plan.get("audio") or [] if m.get("kind") == "music"]
    if "dialogue" not in plan:  # without a story the clips bring their own sound
        out += [{"id": v["id"], "kind": "clip", "track": v["track"], "rec_in": v["rec_in"], "rec_out": v["rec_out"],
                 "base_db": 0.0} for v in plan.get("video") or [] if v.get("audio") == "keep"]
    return sorted(out, key=lambda x: (x["track"], x["rec_in"]))


def clamp_volume(db: float) -> float:
    return round(min(VOLUME_RANGE[1], max(VOLUME_RANGE[0], db)), 1)


def level_dialogue(plan: dict, P: vpaths.Paths, target: float, max_gain: float, lr_db: float) -> dict:
    items = items_of(plan)
    by_id = {d["id"]: d for d in plan.get("dialogue") or []}
    warnings: list[str] = []
    for it in items:
        it["measured_lufs"] = None
        if it["kind"] != "dialogue":
            continue
        d = by_id[it["id"]]
        fps = float(d["source_fps"])
        m = measure(P.abs(d["source"]), d["src_in"] / fps, (d["src_out"] - d["src_in"]) / fps)
        it["measured_lufs"] = m["lufs"]
        name = Path(d["source"]).name
        if m["lufs"] is None:
            warnings.append(f"{it['id']} {name}: too short or too quiet to measure, left at 0 dB")
        else:
            want = target - m["lufs"]
            it["base_db"] = round(max(-max_gain, min(max_gain, want)), 1)
            if abs(want) > max_gain:
                warnings.append(f"{it['id']} {name}: needs {want:+.1f} dB to reach {target} LUFS, "
                                f"clamped to {it['base_db']:+.1f}; listen to it")
        rms = m["channel_rms_db"]
        if len(rms) >= 2 and abs(rms[0] - rms[1]) > lr_db:
            gap = abs(rms[0] - rms[1])
            gap_s = "one side is silent" if gap == float("inf") else f"left and right differ by {gap:.1f} dB"
            warnings.append(f"{it['id']} {name}: {gap_s}; only one side may have a mic, so map the clip's audio "
                            "to the louder channel in Resolve (manual finish)")
    for it in items:
        it["volume_db"] = clamp_volume(it["base_db"])
    return {"target_lufs": target, "max_gain_db": max_gain, "master": {"master_db": 0.0}, "items": items,
            "warnings": warnings}


def apply_master(mix: dict, plan: dict, render: Path, P: vpaths.Paths, lufs: float, peak: float) -> dict:
    fresh = [(i["id"], i["track"], i["rec_in"], i["rec_out"]) for i in items_of(plan)]
    if fresh != [(i["id"], i["track"], i["rec_in"], i["rec_out"]) for i in mix["items"]]:
        raise MixError("the plan's audio items changed since mix.json was written; run mix.py dialogue again")
    m = measure(render)
    if m["lufs"] is None or m["true_peak_dbtp"] is None:
        raise MixError(f"{render.name}: no measurable sound; is it the audio-only render of the timeline?")
    warnings = [w for w in mix.get("warnings") or [] if not w.startswith("master:")]
    dur = probe_duration(render)
    want = plan["timeline"]["duration_frames"] / float(plan["timeline"]["fps"])
    if dur is not None and abs(dur - want) > 0.5:
        warnings.append(f"master: the render is {dur:.1f} s and the film {want:.1f} s; render the whole timeline")
    delta = lufs - m["lufs"]
    if m["true_peak_dbtp"] + delta > peak:
        delta = peak - m["true_peak_dbtp"]
        warnings.append(f"master: the true peak ({m['true_peak_dbtp']:+.1f} dBTP) holds the film at "
                        f"{m['lufs'] + delta:.1f} LUFS, {lufs - m['lufs'] - delta:.1f} LU short of {lufs}; "
                        "a limiter on the main bus in Fairlight gets it there (manual finish)")
    total = round(float((mix.get("master") or {}).get("master_db") or 0) + delta, 1)
    try:
        rel = P.rel(render)
    except ValueError:
        rel = render.name
    mix["master"] = {"master_db": total, "render": rel, "lufs": lufs, "peak": peak,
                     "measured": {k: m[k] for k in ("lufs", "true_peak_dbtp", "lra_lu")},
                     "expected": {"lufs": round(m["lufs"] + delta, 1), "true_peak_dbtp": round(m["true_peak_dbtp"] + delta, 1)}}
    for it in mix["items"]:
        it["volume_db"] = clamp_volume(it["base_db"] + total)
        if it["volume_db"] != round(it["base_db"] + total, 1):
            warnings.append(f"master: {it['id']} would need {it['base_db'] + total:+.1f} dB, outside AudioVolume's range")
    mix["warnings"] = warnings
    return mix


def probe_duration(path: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("dialogue")
    dl.add_argument("--target", type=float, default=-16.0, help="dialogue level, LUFS (default -16)")
    dl.add_argument("--max-gain", type=float, default=12.0)
    dl.add_argument("--lr-db", type=float, default=6.0, help="flag stereo sources whose channels differ more")
    ms = sub.add_parser("master")
    ms.add_argument("--render", type=Path, required=True, help="audio-only render of the timeline")
    ms.add_argument("--lufs", type=float, default=-14.0)
    ms.add_argument("--peak", type=float, default=-1.0, help="true-peak ceiling, dBTP")
    for p in (dl, ms):
        p.add_argument("--project")
        p.add_argument("--plan", type=Path)
    args = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("mix: ffmpeg not on PATH", file=sys.stderr)
        return 2
    P = vpaths.load()
    plan_path = args.plan or (P.project(args.project) / "plan" / "cut_plan.json" if args.project else None)
    if not plan_path:
        ap.error("need --project or --plan")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out = plan_path.parent / "mix.json"
    try:
        if args.cmd == "dialogue":
            mix = level_dialogue(plan, P, args.target, args.max_gain, args.lr_db)
        else:
            if out.exists():
                mix = json.loads(out.read_text(encoding="utf-8"))
            elif plan.get("dialogue"):
                raise MixError("no mix.json yet: run mix.py dialogue first, build with it, then render")
            else:
                mix = level_dialogue(plan, P, -16.0, 12.0, 6.0)
            mix = apply_master(mix, plan, args.render.expanduser(), P, args.lufs, args.peak)
    except MixError as exc:
        print(f"mix: {exc}", file=sys.stderr)
        return 1
    out.write_text(json.dumps(mix, ensure_ascii=False, indent=1), encoding="utf-8")
    for w in mix["warnings"]:
        print(f"  ! {w}", file=sys.stderr)
    levelled = [i for i in mix["items"] if i["kind"] == "dialogue"]
    print(f"mix: {len(mix['items'])} audio item(s), {len(levelled)} dialogue, master {mix['master']['master_db']:+.1f} dB "
          f"-> {out.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
