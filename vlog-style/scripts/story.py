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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402


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
        for a, b, text in c.get("sentences") or []:
            lines.append(f"[{mmss(a / fps)}–{mmss(b / fps)}] {text}")
        if t is not None:
            last_end = t + frames / fps
    return "\n".join(lines).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--project", required=True)
    d.add_argument("--gap-min", type=float, default=30.0, help="mark a break this long between clips")
    args = ap.parse_args(argv)
    proj = vpaths.load().project(args.project)
    index = json.loads((proj / "index" / "footage.json").read_text(encoding="utf-8"))
    print(dump(index, args.gap_min), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
