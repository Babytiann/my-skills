#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy", "opencv-python-headless", "pillow"]
# ///
# -*- coding: utf-8 -*-
"""Grade each chapter toward its look card: one ASC CDL per chapter, solved against its own stills.

  uv run --python 3.13 --script grade.py solve --project NAME [--stills DIR] [--style-file S]
  uv run --python 3.13 --script grade.py solve --plan P --brief B --proxies DIR [--stills DIR]
                                               [--style-file S] --out O
  uv run --python 3.13 --script grade.py preview --grade G --chapter NAME STILL... [--graded STILL...]
                                                 [--out DIR]

solve    Measures each chapter's ungraded stills (by default one frame from the
         middle of each V1/V2 item's source range in its proxy, up to 12 per
         chapter; --stills DIR takes Resolve exports from DIR/<chapter>/ instead),
         then searches the CDL that brings the look within its card. The card is
         brief chapters[].look_ref (a sub-pack such as links/kula-kangri), else the
         plan's style; --style-file overrides it for every chapter.
         A chapter already inside the card keeps its own light: each key is only
         pushed once it is more than half its tolerance off. Night chapters
         (brief night: true, or source L50 < 25) are only ever darkened. A colour
         cast is fixed within bounds unless the chapter says cast: false (warm
         indoor light is a fact of the footage); what bounds can't reach is
         reported, not forced. Photos get their chapter's CDL but never steer it.
         Writes plan/grade.json; a summary goes to stderr.
preview  Applies one chapter's CDL to stills and writes PNGs. With --graded (the
         same stills exported from Resolve with the CDL applied, in the same
         order) it reports the mean |diff| per channel and the look difference:
         this is how the formula is calibrated against Resolve.

The formula is ASC CDL on 0-1 RGB: clamp(in * slope + offset) ^ power, then
saturation around Rec.709 luma. Resolve takes it per timeline item through
timeline_item_color safe_set_cdl; each chapter's "resolve" block is that call's
argument. Stills are decoded as measure_video.py decodes references (320 px
wide, area scaling, rgb24), so the predicted look and the card are measured
the same way.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402
import stats  # noqa: E402

# Starting values, not measurements: how far a chapter may be pushed.
BOUNDS = {"slope": (0.7, 1.3), "offset": (-0.05, 0.05), "power": (0.8, 1.2), "sat": (0.7, 1.3),
          "r_slope": (-0.06, 0.06), "b_slope": (-0.06, 0.06), "r_offset": (-0.02, 0.02), "b_offset": (-0.02, 0.02)}
NEUTRAL = {"slope": 1.0, "offset": 0.0, "power": 1.0, "sat": 1.0,
           "r_slope": 0.0, "b_slope": 0.0, "r_offset": 0.0, "b_offset": 0.0}
STEP = {"slope": 0.08, "offset": 0.02, "power": 0.08, "sat": 0.08,
        "r_slope": 0.02, "b_slope": 0.02, "r_offset": 0.008, "b_offset": 0.008}
CAST_PARAMS = ("r_slope", "b_slope", "r_offset", "b_offset")
TONE = ("L01", "L50", "L99", "Lstd", "shadow", "chroma")
CAST = ("sh_a", "sh_b", "mid_a", "mid_b", "hi_a", "hi_b")
NIGHT_FIT = ("L01", "chroma")
REPORT = TONE + CAST + ("vignette",)  # vignette is spatial: a CDL can't move it, so it is only reported
MAX_STILLS = 12
BLACK_L50 = 3      # a still this dark is a black frame, not a picture
NIGHT_L50 = 25     # a chapter whose source sits below this is treated as night
SPREAD_L50 = 20    # stills of one chapter further apart than this probably want two grades
EVALS = 500
PULL = 0.01        # weight of the pull back toward neutral, per parameter at full range
FORMULA = ("ASC CDL: out = clamp(in * slope + offset) ^ power on 0-1 RGB, then saturation around "
           "Rec.709 luma; one CDL per chapter on every V1/V2 item via timeline_item_color safe_set_cdl, on the node "
           "named in each chapter's resolve block (Resolve runs a node's CDL before that node's LUT)")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- chapters


def chapter_of(plan: dict) -> dict[str, str]:
    """Which chapter's grade each video item takes.

    An item in a chapter section is that chapter's. Hook, intro, interlude and
    ending items go to the chapter whose time window holds their shooting time
    (t_src), else the chapter nearest in time; items without a time go to the
    chapter nearest on the timeline. A plan without chapter sections is one group.
    """
    chapters = [s for s in plan["sections"] if s["role"] == "chapter"]
    if not chapters:
        return {v["id"]: "all" for v in plan["video"]}
    names = {s["name"] for s in chapters}
    timed = [s for s in chapters if s.get("window")]

    def gap(lo, hi, t):
        if lo is not None and t < lo:
            return lo - t
        if hi is not None and t > hi:
            return t - hi
        return 0.0

    out = {}
    for v in plan["video"]:
        if v.get("section") in names:
            out[v["id"]] = v["section"]
        elif "t_src" in v and timed:
            out[v["id"]] = min(timed, key=lambda s: gap(*s["window"], v["t_src"]))["name"]
        else:
            mid = (v["rec_in"] + v["rec_out"]) / 2
            out[v["id"]] = min(chapters, key=lambda s: gap(s["rec_in"], s["rec_out"] - 1, mid))["name"]
    return out


def bounds_for(night: bool, cast: bool) -> dict[str, tuple[float, float]]:
    """Search bounds. Night only darkens (slope <= 1, offset <= 0, power 1); no cast without permission."""
    b = dict(BOUNDS)
    if night:
        b.update(slope=(BOUNDS["slope"][0], 1.0), offset=(BOUNDS["offset"][0], 0.0), power=(1.0, 1.0))
    if night or not cast:
        b.update({k: (0.0, 0.0) for k in CAST_PARAMS})
    return b


def compose(p: dict, night: bool = False) -> tuple[dict, list[str]]:
    """Search parameters -> per-channel CDL, each channel clamped again; also names the channels clamped.

    R and B ride on the overall slope (relative) and offset (added); G follows the overall values.
    """
    lo_s, hi_s = BOUNDS["slope"]
    lo_o, hi_o = BOUNDS["offset"]
    if night:
        hi_s, hi_o = 1.0, 0.0
    slope = [p["slope"] * (1 + p["r_slope"]), p["slope"], p["slope"] * (1 + p["b_slope"])]
    offset = [p["offset"] + p["r_offset"], p["offset"], p["offset"] + p["b_offset"]]
    clamped = []
    for i, ch in enumerate("RGB"):
        s, o = min(hi_s, max(lo_s, slope[i])), min(hi_o, max(lo_o, offset[i]))
        if s != slope[i]:
            clamped.append(f"slope.{ch}")
        if o != offset[i]:
            clamped.append(f"offset.{ch}")
        slope[i], offset[i] = round(s, 4), round(o, 4)
    return ({"slope": slope, "offset": offset, "power": [round(p["power"], 4)] * 3, "sat": round(p["sat"], 4)},
            clamped)


def resolve_args(cdl: dict, node: int = 1) -> dict[str, str]:
    """timeline_item_color safe_set_cdl's argument: every value a string, channels space-separated."""
    trip = lambda xs: " ".join(f"{x:.4f}" for x in xs)  # noqa: E731
    return {"NodeIndex": str(node), "Slope": trip(cdl["slope"]), "Offset": trip(cdl["offset"]),
            "Power": trip(cdl["power"]), "Saturation": f"{cdl['sat']:.4f}"}


def at_bound(p: dict, bounds: dict) -> list[str]:
    """Parameters resting on one of the general bounds (night caps are rules, not limits hit)."""
    out = []
    for k, v in p.items():
        lo, hi = bounds[k]
        if lo == hi:
            continue
        if (v <= BOUNDS[k][0] + 1e-9 and lo == BOUNDS[k][0]) or (v >= BOUNDS[k][1] - 1e-9 and hi == BOUNDS[k][1]):
            out.append(k)
    return out


def pick_items(plan: dict, owner: dict[str, str], chapter: str) -> list[dict]:
    """The chapter's V1/V2 picture items that steer its grade, timeline order, at most MAX_STILLS evenly spread.
    Photos are left out: a phone has already processed them."""
    items = sorted((v for v in plan["video"] if owner.get(v["id"]) == chapter and v["track"] in (1, 2)
                    and v.get("kind") != "photo"), key=lambda v: (v["rec_in"], v["track"]))
    if len(items) <= MAX_STILLS:
        return items
    return [items[round(i * (len(items) - 1) / (MAX_STILLS - 1))] for i in range(MAX_STILLS)]


# ---------------------------------------------------------------- pixels

LUMA709 = (0.2126, 0.7152, 0.0722)
STILL_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def apply_cdl(rgb, cdl: dict):
    """0-255 RGB -> float32 0-255 with the CDL applied (no rounding, so the search sees small moves)."""
    import numpy as np

    x = np.asarray(rgb, np.float32) / 255.0
    y = np.clip(x * np.asarray(cdl["slope"], np.float32) + np.asarray(cdl["offset"], np.float32), 0.0, 1.0)
    y = y ** np.asarray(cdl["power"], np.float32)
    luma = (y @ np.asarray(LUMA709, np.float32))[..., None]
    return np.clip(luma + np.float32(cdl["sat"]) * (y - luma), 0.0, 1.0) * 255.0


def proxy_frame(proxy: Path, t: float):
    """One frame at t seconds, decoded as measure_video decodes (AW wide, area scaling, rgb24); None on failure."""
    import numpy as np

    import measure_video as mv

    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(proxy)], capture_output=True, text=True)
    try:
        pw, ph = (int(x) for x in r.stdout.strip().split(",")[:2])
    except ValueError:
        return None
    w, h = mv.AW, max(2, int(round(mv.AW * ph / pw / 2)) * 2)
    r = subprocess.run(mv.ffmpeg_cmd(proxy, w, h, ss=t, n=1), capture_output=True)
    if r.returncode or len(r.stdout) < w * h * 3:
        return None
    return np.frombuffer(r.stdout[:w * h * 3], np.uint8).reshape(h, w, 3)


def frame_looks(frames: list, cdl: dict | None = None, step: int = 1) -> list[dict]:
    """measure_video.look_of on each (rgb, mask), graded by cdl first; every step-th pixel when step > 1."""
    import measure_video as mv

    out = []
    for rgb, mask in frames:
        if step > 1:
            rgb, mask = rgb[::step, ::step], mask[::step, ::step]
        st = mv.look_of(apply_cdl(rgb, cdl) if cdl else rgb, mask)
        if st:
            out.append(st)
    return out


def median_look(rows: list[dict]) -> dict:
    """Per key, the median over stills of the values that exist (look_of writes NaN as None)."""
    keys = {k for r in rows for k, v in r.items() if not isinstance(v, list)}
    out = {}
    for k in keys:
        vals = [r[k] for r in rows if r.get(k) is not None and math.isfinite(r[k])]
        out[k] = stats.median(vals) if vals else None
    return out


def gather(plan: dict, owner: dict[str, str], chapter: str, proxies: Path | None,
           stills: Path | None) -> tuple[list, list[dict], list[str]]:
    """The chapter's ungraded stills as (rgb, mask) at the analysis width, what each one is, and warnings."""
    import numpy as np
    from PIL import Image

    import compare
    import measure_video as mv

    frames, used, warns = [], [], []
    if stills:
        d = stills / chapter
        files = sorted(f for f in d.iterdir() if f.suffix.lower() in STILL_EXT) if d.is_dir() else []
        if not files:
            warns.append(f"no stills in {chapter}/ under --stills")
        for f in files:
            frames.append(compare.still_mask(np.asarray(Image.open(f).convert("RGB"))))
            used.append({"file": f.name})
    else:
        for v in pick_items(plan, owner, chapter):
            proxy = proxies / v["clip"] / "proxy.mp4"
            t = (v["src_in"] + v["src_out"]) / 2 / float(v["source_fps"])
            rgb = proxy_frame(proxy, t) if proxy.exists() else None
            if rgb is None:
                warns.append(f"{v['id']}: no frame from proxies/{v['clip']}/proxy.mp4")
                continue
            frames.append(compare.still_mask(rgb))
            used.append({"item": v["id"], "clip": v["clip"], "t": round(t, 3)})
    keep_f, keep_u = [], []
    for fr, u in zip(frames, used):
        st = mv.look_of(*fr)
        if st is None or (st["L50"] is not None and st["L50"] <= BLACK_L50):
            warns.append(f"{u.get('item') or u.get('file')}: black or unmeasurable still, skipped")
            continue
        keep_f.append(fr)
        keep_u.append(u)
    return keep_f, keep_u, warns


# ---------------------------------------------------------------- solve


def rnd(x, k: int = 3):
    return None if x is None or not math.isfinite(x) else round(float(x), k)


def solve(frames: list, target: dict, src: dict, night: bool, cast: bool) -> dict:
    """Coordinate descent over the CDL parameters toward the card; deterministic, at most EVALS looks.

    Each fitted key costs only what lies beyond half its tolerance, so a chapter
    already inside the card stays where its light put it; a small pull keeps
    every parameter near neutral. The search runs on every other pixel; the
    prediction is measured at the full analysis width.
    """
    import compare

    tol = {k: max(compare.LOOK_FLOOR[k], target[k].get("mad") or 0)
           for k in REPORT if target.get(k) and k in compare.LOOK_FLOOR}
    keys = NIGHT_FIT if night else TONE + (CAST if cast else ())
    fit = [k for k in keys if k in tol and src.get(k) is not None]
    bounds = bounds_for(night, cast)
    active = [k for k, (lo, hi) in bounds.items() if hi > lo]

    def cost(p: dict) -> float:
        look = median_look(frame_looks(frames, compose(p, night)[0], step=2))
        c = 0.0
        for k in fit:
            got = look.get(k)
            over = 1.0 if got is None else max(0.0, abs(got - target[k]["median"]) - tol[k] / 2) / tol[k]
            c += over * over
        for k in active:
            c += PULL * ((p[k] - NEUTRAL[k]) * 2 / (BOUNDS[k][1] - BOUNDS[k][0])) ** 2
        return c

    p, step, evals = dict(NEUTRAL), dict(STEP), 1
    best = cost(p)
    while evals < EVALS and any(step[k] >= STEP[k] / 64 for k in active):
        moved = False
        for k in active:
            lo, hi = bounds[k]
            for sign in (1, -1):
                v = min(hi, max(lo, p[k] + sign * step[k]))
                if v == p[k]:
                    continue
                q = {**p, k: v}
                c = cost(q)
                evals += 1
                if c < best - 1e-9:
                    p, best, moved = q, c, True
                    break
            if evals >= EVALS:
                break
        if not moved:
            step = {k: s / 2 for k, s in step.items()}

    cdl, clamped = compose(p, night)
    pred = median_look(frame_looks(frames, cdl))
    look = {}
    for k in REPORT:
        if k not in tol:
            continue
        t, got = target[k]["median"], pred.get(k)
        look[k] = {"source": rnd(src.get(k)), "target": t, "predicted": rnd(got), "tol": round(tol[k], 3),
                   "ok": got is not None and abs(got - t) <= tol[k], "fit": k in fit}
    return {"params": {k: round(v, 4) for k, v in p.items()}, "cdl": cdl, "clamped": clamped,
            "at_bound": at_bound(p, bounds), "look": look, "evals": evals}


def grade_chapter(name: str, frames: list, target: dict, night_brief: bool | None, cast: bool) -> dict:
    """One chapter's entry in grade.json, without the look_ref and stills bookkeeping."""
    warns: list[str] = []
    rows = frame_looks(frames)
    if not rows:
        cdl = compose(NEUTRAL)[0]
        return {"night": bool(night_brief), "night_from": "brief" if night_brief is not None else None,
                "cast": cast, "cdl": cdl, "look": {}, "at_bound": [], "clamped": [],
                "warnings": ["no measurable stills: CDL left neutral"]}
    src = median_look(rows)
    night, night_from = bool(night_brief), ("brief" if night_brief is not None else None)
    if night_brief is None and src.get("L50") is not None and src["L50"] < NIGHT_L50:
        night, night_from = True, "auto"
        warns.append(f"source L50 {src['L50']:.1f} < {NIGHT_L50}: graded as night (darken only, no cast); "
                     "set night: false in the brief if it isn't")
    l50 = [r["L50"] for r in rows if r.get("L50") is not None]
    if l50 and max(l50) - min(l50) > SPREAD_L50:
        warns.append(f"stills span L50 {min(l50):.0f}-{max(l50):.0f}: one CDL may not fit both; "
                     "consider splitting the chapter")
    res = solve(frames, target, src, night, cast)
    if res["at_bound"] or res["clamped"]:
        warns.append(f"at bound: {', '.join(res['at_bound'] + res['clamped'])}")
    missed = [k for k, v in res["look"].items() if v["fit"] and not v["ok"]]
    if missed:
        cast_missed = [k for k in missed if k in CAST]
        warns.append(f"still off the card after grading: {', '.join(missed)}"
                     + ("; the cast is only fixed within bounds, the rest is the light it was shot in"
                        if cast_missed else ""))
    return {"night": night, "night_from": night_from, "cast": cast and not night, **res, "warnings": warns}


# ---------------------------------------------------------------- main


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def thin_card(name: str, target: dict, cast: bool) -> str | None:
    """A warning when the look card lacks keys a grade would fit, else None.

    A creator pack keeps only the keys its films agree on (style-schema.md, 专辑), so its card
    may have no cast keys; those keys are left alone. Say so instead of skipping them silently.
    """
    missing = [k for k in TONE + (CAST if cast else ()) if (target.get(k) or {}).get("median") is None]
    if not missing:
        return None
    return (f"{name} 的 look 没有 {', '.join(missing)}，这些项不调；"
            "要调请在 brief 里给这一章写 look_ref，用光线和地方最像的子包")


def card(look_ref: str | None, plan_style: str, override: dict | None) -> tuple[str, dict, str | None]:
    """The look card a chapter grades toward: (its name, grade.look, a warning or None)."""
    if override is not None:
        return override.get("name") or "style-file", override["grade"]["look"], None
    if look_ref:
        return look_ref, load_json(vpaths.style_file(look_ref))["grade"]["look"], None
    warn = (f"no look_ref in the brief: graded toward {plan_style}; a sub-pack (the place the footage "
            "resembles) gives a closer card and more colour keys")
    return plan_style, load_json(vpaths.style_file(plan_style))["grade"]["look"], warn


def build(plan: dict, brief: dict, proxies: Path | None, stills: Path | None, override: dict | None,
          node: int = 1) -> dict:
    owner = chapter_of(plan)
    briefed = {c.get("name"): c for c in brief.get("chapters") or []}
    names = list(dict.fromkeys(owner[v["id"]] for v in sorted(plan["video"], key=lambda v: v["rec_in"])))
    chapters = []
    for name in names:
        bc = briefed.get(name, {})
        ref, target, warn = card(bc.get("look_ref"), plan["style"], override)
        frames, used, warns = gather(plan, owner, name, proxies, stills)
        thin = thin_card(ref, target, bc.get("cast", True) is not False)
        for w in (thin, warn):
            if w:
                warns.insert(0, w)
        g = grade_chapter(name, frames, target, bc.get("night"), bc.get("cast", True) is not False)
        g["warnings"] = warns + g["warnings"]
        chapters.append({"name": name, "look_ref": ref, "stills": used, **g, "resolve": resolve_args(g["cdl"], node)})
    return {"schema_version": 1, "style": plan["style"], "project": plan.get("project"), "formula": FORMULA,
            "bounds": {k: list(v) for k, v in BOUNDS.items()}, "stills_from": "stills" if stills else "proxies",
            "chapters": chapters, "items": owner}


def summary(grade: dict) -> str:
    lines = []
    for c in grade["chapters"]:
        cdl = c["cdl"]
        night = f"  夜景({c['night_from']})" if c["night"] else ""
        lines.append(f"{c['name']}  ← {c['look_ref']}  静帧 {len(c['stills'])}{night}")
        lines.append(f"  slope {' '.join(f'{x:.3f}' for x in cdl['slope'])}  offset "
                     f"{' '.join(f'{x:+.3f}' for x in cdl['offset'])}  power {cdl['power'][0]:.3f}  "
                     f"sat {cdl['sat']:.3f}")
        for k, v in c["look"].items():
            if v["fit"] or not v["ok"]:
                mark = "✓" if v["ok"] else "✗"
                fit = "" if v["fit"] else "  (不拟合)"
                lines.append(f"  {mark} {k:8s} 原 {v['source'] if v['source'] is not None else '-':>8}  "
                             f"目标 {v['target']:8.2f}  预测 {v['predicted'] if v['predicted'] is not None else '-':>8}"
                             f"  容差 {v['tol']:.2f}{fit}")
        lines += [f"  ! {w}" for w in c["warnings"]]
    return "\n".join(lines)


def preview(grade: dict, chapter: str, stills: list[Path], graded: list[Path] | None, out_dir: Path) -> dict:
    """Apply one chapter's CDL to stills; with Resolve's graded exports, report how close the formula came."""
    import cv2
    import numpy as np
    from PIL import Image

    import compare
    import measure_video as mv

    ch = next((c for c in grade["chapters"] if c["name"] == chapter), None)
    if ch is None:
        raise SystemExit(f"no chapter {chapter!r}; have: {', '.join(c['name'] for c in grade['chapters'])}")
    if graded and len(graded) != len(stills):
        raise SystemExit("--graded needs one export per still, in the same order")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, pred_rows, graded_rows = [], [], []
    for i, f in enumerate(stills):
        img = np.round(apply_cdl(np.asarray(Image.open(f).convert("RGB")), ch["cdl"])).astype(np.uint8)
        png = out_dir / f"{f.stem}.graded.png"
        Image.fromarray(img).save(png)
        st = mv.look_of(*compare.still_mask(img))
        if st:
            pred_rows.append(st)
        row = {"still": f.name, "png": png.name}
        if graded:
            g = np.asarray(Image.open(graded[i]).convert("RGB"))
            if g.shape != img.shape:
                g = cv2.resize(g, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
            d = np.abs(img.astype(np.int16) - g.astype(np.int16)).mean(axis=(0, 1))
            row["mean_abs_diff"] = dict(zip("RGB", (round(float(x), 2) for x in d)))
            gs = mv.look_of(*compare.still_mask(g))
            if gs:
                graded_rows.append(gs)
        rows.append(row)
    res = {"chapter": chapter, "cdl": ch["cdl"], "stills": rows,
           "predicted": {k: rnd(v) for k, v in median_look(pred_rows).items() if k in REPORT}}
    if graded:
        got = {k: rnd(v) for k, v in median_look(graded_rows).items() if k in REPORT}
        res["graded"] = got
        res["look_diff"] = {k: round(res["predicted"][k] - got[k], 3) for k in REPORT
                            if res["predicted"].get(k) is not None and got.get(k) is not None}
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("solve")
    s.add_argument("--project")
    s.add_argument("--plan", type=Path)
    s.add_argument("--brief", type=Path)
    s.add_argument("--proxies", type=Path, help="folder of <clip>/proxy.mp4")
    s.add_argument("--stills", type=Path, help="folder of <chapter>/ subfolders of ungraded Resolve stills")
    s.add_argument("--style-file", type=Path, help="grade every chapter toward this pack's look (tests)")
    s.add_argument("--node", type=int, default=1,
                   help="graph node for the CDL (default 1; 2 when a style pack's LUT sits on node 1)")
    s.add_argument("--out", type=Path)
    pv = sub.add_parser("preview")
    pv.add_argument("stills", type=Path, nargs="+")
    pv.add_argument("--grade", type=Path, required=True)
    pv.add_argument("--chapter", required=True)
    pv.add_argument("--graded", type=Path, nargs="+", help="the same stills exported from Resolve, graded")
    pv.add_argument("--out", type=Path, help="folder for the PNGs (default: grade_preview/ beside --grade)")
    args = ap.parse_args(argv)

    if args.cmd == "preview":
        out = args.out or args.grade.parent / "grade_preview"
        res = preview(load_json(args.grade), args.chapter, args.stills, args.graded, out)
        for r in res["stills"]:
            if "mean_abs_diff" in r:
                log(f"  {r['still']}: 平均 |diff| " + "  ".join(f"{c} {v:.2f}" for c, v in r["mean_abs_diff"].items()))
        for k, d in res.get("look_diff", {}).items():
            log(f"  {k:8s} 预测 − Resolve {d:+.2f}")
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    if args.project:
        proj = vpaths.load().project(args.project)
        args.plan = args.plan or proj / "plan" / "cut_plan.json"
        args.brief = args.brief or proj / "plan" / "brief.json"
        args.proxies = args.proxies or proj / "proxies"
        args.out = args.out or proj / "plan" / "grade.json"
    if not (args.plan and args.brief and args.out and (args.proxies or args.stills)):
        ap.error("solve needs --plan, --brief, --out and --proxies or --stills (or --project)")
    if args.node < 1:
        ap.error("--node counts from 1")
    override = load_json(args.style_file) if args.style_file else None
    try:
        grade = build(load_json(args.plan), load_json(args.brief), args.proxies, args.stills, override, args.node)
    except ValueError as exc:  # a look_ref naming no pack
        log(f"grade: {exc}")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(grade, ensure_ascii=False, indent=1), encoding="utf-8")
    log(summary(grade))
    return 0


if __name__ == "__main__":
    sys.exit(main())
