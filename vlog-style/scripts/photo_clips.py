#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Short clips for the photos in a cut plan (stdlib, ffmpeg, sips).

Resolve places a native still at its default 5 s whatever frame range the
append asks for (resolve-routes.md, pitfall 27), so every photo the plan uses
becomes a clip at the timeline's frame rate and size, and plays from frame 0:

- the whole photo fitted inside the frame, black bars, nothing cropped;
- upright: HEIC/HEIF go through sips to JPEG first, which keeps the EXIF, and
  ffmpeg turns a JPEG upright from its Orientation tag when it decodes;
- Rec.709 matrix, limited range and bt709 tags, like the footage, so Resolve
  reads its colour the same way (probe #14: mean difference 0.81 of 255);
- as long as the longest item that uses the photo.

The style's photo scale is not baked in: apply sets it in Resolve with
set_transform (ZoomX/ZoomY), so the user can still change it there.

The clip goes to the item's `source` (proxies/<id>/photo.mp4) with a photo.json
beside it; a clip whose photo.json still matches is kept, so rerunning after
the plan changes only renders what changed. Originals are only read.

Usage:
  python3 -I -B photo_clips.py --project NAME [--force]
  python3 -I -B photo_clips.py --plan cut_plan.json [--force]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

SIPS_FIRST = {".heic", ".heif"}  # ffmpeg's HEIF support varies by build; sips is always on macOS


def rate(fps: float) -> str:
    """29.97 is 30000/1001 to ffmpeg; a plain float would drift by a frame every half minute."""
    n = round(fps)
    return f"{n * 1000}/1001" if abs(fps - n) > 0.01 else str(n)


def wanted(plan: dict) -> dict[str, dict]:
    """What each photo clip must be: one entry per output path."""
    tl = plan["timeline"]
    out: dict[str, dict] = {}
    for v in plan["video"]:
        if v.get("kind") != "photo":
            continue
        want = out.setdefault(v["source"], {"clip": v["clip"], "photo": v["photo"], "frames": 0, "fps": float(tl["fps"]),
                                            "width": int(tl["width"]), "height": int(tl["height"])})
        want["frames"] = max(want["frames"], int(v["src_out"]))
    return out


def render(src: Path, out: Path, want: dict) -> None:
    w, h = want["width"], want["height"]
    feed = src
    if src.suffix.lower() in SIPS_FIRST:
        feed = out.parent / "full.jpg"
        r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "best", str(src), "--out", str(feed)],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"sips could not read {src.name}: {r.stderr.strip()[:300]}")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:out_color_matrix=bt709:out_range=tv,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
          "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")  # ffmpeg 9 tags from the frames
    tmp = out.with_suffix(".part.mp4")
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-loop", "1", "-framerate", rate(want["fps"]),
                        "-i", str(feed), "-frames:v", str(want["frames"]), "-vf", vf, "-c:v", "libx264", "-crf", "14",
                        "-preset", "medium", "-tune", "stillimage", "-colorspace", "bt709", "-color_primaries", "bt709",
                        "-color_trc", "bt709", "-color_range", "tv", "-an", str(tmp)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg could not make a clip of {src.name}: {r.stderr.strip()[:300]}")
    tmp.replace(out)


def make(P: vpaths.Paths, plan: dict, force: bool = False) -> list[dict]:
    done = []
    for rel, want in sorted(wanted(plan).items()):
        out = P.abs(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        side = out.with_name("photo.json")
        record = {k: want[k] for k in ("photo", "frames", "fps", "width", "height")}
        old = json.loads(side.read_text(encoding="utf-8")) if side.exists() else None
        fresh = force or not out.exists() or old != record
        if fresh:
            render(P.abs(want["photo"]), out, want)
            side.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        done.append({"clip": want["clip"], "source": rel, "frames": want["frames"], "made": fresh})
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--force", action="store_true", help="render every clip again")
    args = ap.parse_args(argv)
    P = vpaths.load()
    if args.project:
        args.plan = args.plan or P.project(args.project) / "plan" / "cut_plan.json"
    if not args.plan:
        ap.error("need --project or --plan")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    done = make(P, plan, args.force)
    for d in done:
        print(f"{d['clip']}: {d['frames']} frames -> {d['source']}{'' if d['made'] else ' (kept)'}", file=sys.stderr)
    print(json.dumps(done, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
