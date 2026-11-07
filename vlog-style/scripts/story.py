#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["pillow"]
# ///
# -*- coding: utf-8 -*-
"""Read the footage as a story: what was said, when, and in what order.

  python3 -I -B story.py dump --project NAME [--gap-min 30]
      every clip's transcript in shooting order, split by shooting day and long
      breaks, with the real time and device on each line. The agent drafts the
      chapters and plan/story.json from this; chapters follow what happened.
  uv run --python 3.13 --script story.py review --project NAME
      contact sheets of plan/cut_plan.json in timeline order, for checking the
      edit by eye before the user sees it (references/apply.md section 6)

Transcript text stays in the project under root and never enters the repo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402
import plan_cut  # noqa: E402
import subs  # noqa: E402

ROLE = {"aroll": "A-roll", "broll": "B-roll", "cutaway": "盖画面", "photo": "照片"}
CELL_W, CELL_H, COLS, LABEL_H = 320, 180, 4, 66
CJK_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def epoch(iso: str) -> float:
    return dt.datetime.fromisoformat(iso).timestamp()


def clock(iso: str | None) -> str:
    return dt.datetime.fromisoformat(iso).strftime("%m-%d %H:%M:%S") if iso else "时间未知"


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


def dump(index: dict, gap_min: float = 30.0) -> str:
    clips = sorted(index.get("clips") or [], key=lambda c: c.get("order", 0))
    undated = [c["id"] for c in clips if not c.get("shot_at")]
    lines = []
    if undated:
        lines += [f"注意：{len(undated)} 个片段没有拍摄时间，按文件名排在最后，顺序不可靠：{'、'.join(undated)}", ""]
    day, last_end = None, None
    for c in clips:
        if c.get("day") != day:
            day = c.get("day")
            lines += ["", f"## 第 {day} 天" if day else "## 时间未知"]
            last_end = None
        t = epoch(c["shot_at"]) if c.get("shot_at") else None
        if t is not None and last_end is not None and t - last_end >= gap_min * 60:
            lines += ["", f"--- 间隔 {(t - last_end) / 60:.0f} 分钟 ---"]
        fps, frames = c["fps"], c.get("frames") or 0
        kinds = sorted({s.get("shot_type") for s in c.get("shots") or [] if s.get("shot_type")})
        tags = sorted({x for s in c.get("shots") or [] for x in s.get("tags") or []})
        lines.append(f"### {c['id']}  {clock(c.get('shot_at'))}  {c.get('device') or ''}  长 {frames / fps:.1f} s"
                     f"  {'/'.join(kinds)}  {'/'.join(tags)}".rstrip())
        for a, b, text, *lang in c.get("sentences") or []:
            mark = f"[{lang[0]}] " if lang else ""
            lines.append(f"[{mmss(a / fps)}–{mmss(b / fps)}] {mark}{text}")
        if t is not None:
            last_end = t + frames / fps
    return "\n".join(lines).strip() + "\n"


def cells(plan: dict, index: dict | None) -> list[dict]:
    """One cell per picture item, in timeline order (V2 right after the V1 item it covers).

    Each carries what review needs to judge by eye: the real shooting time, the
    named exceptions to the clock, and the words heard at the item's middle.
    """
    fps = float(plan["timeline"]["fps"])
    heard = subs.timed_lines(plan, index)
    out = []
    for v in sorted(plan["video"], key=lambda v: (v["rec_in"], v["track"])):
        mid = (v["rec_in"] + v["rec_out"]) / 2
        said = next((t for a, b, t in heard if a <= mid < b), "")
        photo = v.get("kind") == "photo"
        out.append({"id": v["id"], "section": v.get("section") or "", "track": v["track"],
                    "role": ROLE.get(v.get("role") or v.get("kind"), v.get("kind") or ""), "clip": v["clip"],
                    "photo": photo,
                    "src_s": 0.0 if photo else (v["src_in"] + v["src_out"]) / 2 / float(v.get("source_fps") or fps),
                    "rec": mmss(v["rec_in"] / fps), "when": plan_cut.when(v), "marks": plan_cut.out_of_time(v),
                    "said": said})
    return out


def grab(proxy: Path, t: float) -> bytes | None:
    """One CELL_W x CELL_H RGB frame at t seconds, letterboxed; None when ffmpeg can't."""
    vf = (f"scale={CELL_W}:{CELL_H}:force_original_aspect_ratio=decrease,"
          f"pad={CELL_W}:{CELL_H}:(ow-iw)/2:(oh-ih)/2")
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(proxy),
                        "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return r.stdout if r.returncode == 0 and len(r.stdout) == CELL_W * CELL_H * 3 else None


def review(plan: dict, index: dict | None, proj: Path, out_dir: Path) -> list[Path]:
    """Write one contact sheet per section into out_dir and return their paths."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        try:
            return ImageFont.truetype(CJK_FONT, size)
        except OSError:
            return ImageFont.load_default(size=size)

    def fit(text: str, f, width: int) -> str:
        while text and f.getlength(text) > width:
            text = text[:-2] + "…" if len(text) > 1 else ""
        return text

    out_dir.mkdir(parents=True, exist_ok=True)
    small, head = font(13), font(18)
    order = [s["name"] for s in plan.get("sections") or []]
    groups: dict[str, list[dict]] = {}
    for c in cells(plan, index):
        groups.setdefault(c["section"], []).append(c)
    written = []
    for n, name in enumerate(sorted(groups, key=lambda k: order.index(k) if k in order else len(order)), 1):
        group = groups[name]
        rows = -(-len(group) // COLS)
        img = Image.new("RGB", (COLS * CELL_W, 34 + rows * (CELL_H + LABEL_H)), (18, 18, 18))
        dr = ImageDraw.Draw(img)
        dr.text((8, 7), f"{name}：{len(group)} 条，按时间线顺序；黄字是例外（预告、借用、风景段落、顺序例外）",
                font=head, fill=(240, 240, 240))
        for k, c in enumerate(group):
            x, y = (k % COLS) * CELL_W, 34 + (k // COLS) * (CELL_H + LABEL_H)
            proxy = proj / "proxies" / c["clip"] / ("preview.jpg" if c["photo"] else "proxy.mp4")
            raw = grab(proxy, c["src_s"]) if proxy.exists() else None
            if raw:
                img.paste(Image.frombytes("RGB", (CELL_W, CELL_H), raw), (x, y))
            else:
                dr.rectangle((x, y, x + CELL_W - 1, y + CELL_H - 1), fill=(60, 30, 30))
                dr.text((x + 8, y + 8), "没有代理帧", font=small, fill=(240, 160, 160))
            if c["track"] == 2:
                dr.rectangle((x, y, x + CELL_W - 1, y + CELL_H - 1), outline=(90, 160, 255), width=3)
            lines = [(f"{c['id']}  V{c['track']} {c['role']}  {c['rec']}", (235, 235, 235)),
                     (f"{c['clip']}  {c['when'] or '时间未知'}", (190, 190, 190)),
                     (c["marks"], (255, 210, 0)),
                     (f"「{c['said']}」" if c["said"] else "", (160, 220, 160))]
            for i, (text, colour) in enumerate(lines):
                if text:
                    dr.text((x + 4, y + CELL_H + 2 + i * 16), fit(text, small, CELL_W - 8), font=small, fill=colour)
        path = out_dir / f"{n:02d}_{name.replace('/', '_')}.jpg"
        img.save(path, quality=82)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--project", required=True)
    d.add_argument("--gap-min", type=float, default=30.0, help="mark a break this long between clips")
    r = sub.add_parser("review")
    r.add_argument("--project", required=True)
    args = ap.parse_args(argv)
    paths = vpaths.load()
    proj = paths.project(args.project)
    index = json.loads((proj / "index" / "footage.json").read_text(encoding="utf-8"))
    if args.cmd == "dump":
        print(dump(index, args.gap_min), end="")
        return 0
    plan = json.loads((proj / "plan" / "cut_plan.json").read_text(encoding="utf-8"))
    for path in review(plan, index, proj, proj / "qa" / "review"):
        print(paths.rel(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
