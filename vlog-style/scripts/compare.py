#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy", "opencv-python-headless", "pillow"]
# ///
# -*- coding: utf-8 -*-
"""Check a built timeline against its plan, and graded stills against the look card.

  python3 -I -B compare.py normalize CLIP_WHERE.json... --start-frame N [--out READBACK.json]
  python3 -I -B compare.py readback READBACK.json --plan cut_plan.json [--style NAME | --style-file S]
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
    for p in (rb, lk):
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
