#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Subtitles for a cut plan: what is actually heard, at the frames it is heard.

Lines come from the plan's dialogue[] when it has one, else from the index for
every video[] item that keeps its sound. Whisper mishears names, so the result
goes to the user to proofread before it enters Resolve; plan/subs_fixes.json
keeps those corrections and is applied on every run, so changing the plan does
not lose them:

  {"replace": {"特罗姆色": "特罗姆瑟"},
   "translate": {"How did you bring all this here?": "你们是怎么把这些运过来的？"}}

replace fixes words inside every line. translate swaps a whole line, keyed by
the exact text as heard; lines whisper marked as not Chinese (a `lang` after
the text in footage.json) and missing from it stay as spoken and are listed.

An existing .srt is not overwritten without --force, because it may hold hand
edits.

Usage:
  python3 -I -B subs.py --project NAME [--max-chars 18] [--force]
  python3 -I -B subs.py --plan P --index I --out O [--fixes F] [--max-chars 18] [--force]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import paths as vpaths  # noqa: E402

MIN_CUE_S = 0.5
BREAK_AFTER = "。！？!?；;，,、：:"
TRAILING = "。，,、；;：:"  # subtitles drop these at the end of a line; ？！ carry meaning and stay


def split_line(text: str, max_chars: int) -> list[str]:
    """Break a sentence into cues: at punctuation first, then by length."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    pieces: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in BREAK_AFTER:
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)
    out: list[str] = []
    for piece in pieces:
        if out and len(out[-1]) + len(piece) <= max_chars and not out[-1].endswith(tuple("。！？!?")):
            out[-1] += piece
        else:
            while len(piece) > max_chars:
                out.append(piece[:max_chars])
                piece = piece[max_chars:]
            if piece:
                out.append(piece)
    cleaned = [c.rstrip(TRAILING).strip() for c in out]
    return [c for c in cleaned if c]


def rec_of(frame: float, src_in: int, rec_in: int, source_fps: float, fps: float) -> float:
    """Timeline frame of a source frame: 59.94 footage on a 29.97 timeline runs two source frames per timeline frame."""
    return rec_in + (frame - src_in) * fps / source_fps


def spoken(plan: dict, index: dict | None) -> list[tuple[float, float, str, str | None]]:
    """(rec start, rec end, text, language or None for Chinese), clipped to the item that plays them."""
    fps = float(plan["timeline"]["fps"])
    rows: list[tuple[float, float, str, str | None]] = []
    if plan.get("dialogue"):
        items = [(d, d["sentences"]) for d in plan["dialogue"]]
    else:
        clips = {c["id"]: c for c in (index or {}).get("clips") or []}
        items = [(v, clips.get(v["clip"], {}).get("sentences") or []) for v in plan["video"]
                 if v.get("audio") == "keep" and v.get("kind") != "photo"]
    for item, sentences in items:
        for a, b, text, *lang in sentences:
            a, b = max(a, item["src_in"]), min(b, item["src_out"])
            if b <= a:
                continue
            rows.append((rec_of(a, item["src_in"], item["rec_in"], item["source_fps"], fps),
                         rec_of(b, item["src_in"], item["rec_in"], item["source_fps"], fps), text,
                         lang[0] if lang else None))
    return sorted(rows, key=lambda r: r[:3])


def timed_lines(plan: dict, index: dict | None, translate: dict[str, str] | None = None
                ) -> list[tuple[float, float, str]]:
    """(rec start, rec end, text) in timeline frames; translate swaps whole lines."""
    return [(a, b, (translate or {}).get(text, text)) for a, b, text, _ in spoken(plan, index)]


def untranslated(plan: dict, index: dict | None, translate: dict[str, str]) -> list[tuple[str, str]]:
    """(language, text) of each foreign line heard in the cut that has no translation yet."""
    return sorted({(lang, text) for _, _, text, lang in spoken(plan, index) if lang and text not in translate})


def cues_from(rows: list[tuple[float, float, str]], fps: float, max_chars: int, fixes: dict[str, str]) -> list[dict]:
    """Split long lines, share each line's time by character count, keep cues apart and long enough to read."""
    cues: list[dict] = []
    for a, b, text in rows:
        for old, new in fixes.items():
            text = text.replace(old, new)
        parts = split_line(text, max_chars)
        chars = sum(len(p) for p in parts)
        t = a
        for part in parts:
            end = t + (b - a) * len(part) / chars
            cues.append({"a": t / fps, "b": end / fps, "text": part})
            t = end
    for c, nxt in zip(cues, cues[1:]):
        c["b"] = min(max(c["b"], c["a"] + MIN_CUE_S), max(nxt["a"], c["a"]))  # readable, but never over the next
    if cues:
        cues[-1]["b"] = max(cues[-1]["b"], cues[-1]["a"] + MIN_CUE_S)
    merged: list[dict] = []
    for c in cues:  # still too short to read: it shares the screen with what comes next
        if merged and merged[-1]["b"] - merged[-1]["a"] < MIN_CUE_S:
            merged[-1]["text"] += " " + c["text"]
            merged[-1]["b"] = c["b"]
        else:
            merged.append(c)
    return merged


def stamp(seconds: float) -> str:
    ms = max(0, math.floor(seconds * 1000 + 0.5))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(cues: list[dict]) -> str:
    return "".join(f"{i}\n{stamp(c['a'])} --> {stamp(c['b'])}\n{c['text']}\n\n" for i, c in enumerate(cues, 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--index", type=Path)
    ap.add_argument("--fixes", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--max-chars", type=int, default=18)
    ap.add_argument("--force", action="store_true", help="overwrite an existing .srt, which may hold hand edits")
    args = ap.parse_args(argv)
    if args.project:
        proj = vpaths.load().project(args.project)
        args.plan = args.plan or proj / "plan" / "cut_plan.json"
        args.index = args.index or proj / "index" / "footage.json"
        args.fixes = args.fixes or proj / "plan" / "subs_fixes.json"
        args.out = args.out or proj / "plan" / "subs.srt"
    if not (args.plan and args.out):
        ap.error("need --project, or --plan and --out")
    if args.out.exists() and not args.force:
        print(f"subs: {args.out.name} exists and may hold hand edits; put corrections in subs_fixes.json "
              "and pass --force to regenerate", file=sys.stderr)
        return 1
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    plan = load(args.plan)
    index = load(args.index) if args.index and args.index.exists() else None
    doc = load(args.fixes) if args.fixes and args.fixes.exists() else {}
    fixes, translate = doc.get("replace") or {}, doc.get("translate") or {}
    fps = float(plan["timeline"]["fps"])
    cues = cues_from(timed_lines(plan, index, translate), fps, args.max_chars, fixes)
    args.out.write_text(to_srt(cues), encoding="utf-8")
    missing = untranslated(plan, index, translate)
    if missing:
        print(f"{len(missing)} foreign line(s) have no translation and stay as spoken; add them to "
              "subs_fixes.json \"translate\", then regenerate with --force:", file=sys.stderr)
        for lang, text in missing:
            print(f"  [{lang}] {text}", file=sys.stderr)
    print(f"{len(cues)} cues; have the user proofread names and places before it goes into Resolve", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
