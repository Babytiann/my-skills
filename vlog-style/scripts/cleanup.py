#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List the vlog-style cache, keep a reference's measurements, move entries to the Trash.

Reference downloads, proxies, sheets and strips live in <root>/cache/refs/<id>/
and are not kept once a style pack is accepted. Before they go, `keep` copies
what a later re-draft needs (measure_video.json, measure_audio.json,
labels.json, meta.json, and measure_music.json with its .embed.json, which hold
no text) to <root>/styles/<group>/refs/<id>/ with transcripts
and subtitle text stripped, so a creator album can grow without downloading
the reference again. The kept copy lives under root only, never in the repo.

This never deletes: it calls `trash` (macOS Trash, recoverable) and refuses
anything outside <root>/cache. Without --yes it only prints what would happen.

What trash leaves alone by default, and the way past it:
  in use      a process has a file open under the reference. Stop that process first.
  unfinished  no labels.json and no kept copy, so a distill may still be running.
              REF_ID is refused and --all skips it; --unfinished takes it anyway
              (an abandoned download, a re-watch clip that was never kept).
  never kept  measured but not kept. Refused, unless --no-keep.

`list` gives each reference a state: busy, unfinished, measured or kept.

Usage:
  python3 -I -B cleanup.py list
  python3 -I -B cleanup.py keep REF_ID... --style GROUP [--yes]
  python3 -I -B cleanup.py trash REF_ID... [--yes] [--no-keep] [--unfinished]
  python3 -I -B cleanup.py trash --all [--yes] [--no-keep] [--unfinished]
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import aggregate  # noqa: E402
import paths as vpaths  # noqa: E402


def du(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def listing(P: vpaths.Paths) -> list[dict]:
    cache = P.dir("cache")
    if not cache.exists():
        return []
    out = []
    for p in sorted(cache.iterdir()):
        kids = sorted(p.iterdir()) if p.is_dir() and p.name == "refs" else [p]
        for k in kids:
            if k.name.startswith("."):  # .DS_Store and friends are not cache entries
                continue
            row = {"path": P.rel(k), "mb": round(du(k) / 2**20, 1),
                   "files": sum(1 for _ in k.rglob("*")) if k.is_dir() else 1}
            if p.name == "refs":
                row["kept"] = [P.rel(x) for x in kept(P, k.name)]
                row["state"] = ref_state(P, k)
            out.append(row)
    return out


def inside_cache(P: vpaths.Paths, target: Path) -> bool:
    cache = P.dir("cache").resolve()
    t = target.resolve()
    return t != cache and t.is_relative_to(cache)


def kept(P: vpaths.Paths, ref_id: str) -> list[Path]:
    """Kept copies of one reference under any creator album or misc."""
    styles = P.dir("styles")
    if not styles.exists():
        return []
    return sorted(p.parent for p in styles.glob(f"*/refs/{glob.escape(ref_id)}/measure_video.json"))


def busy(path: Path) -> list[str]:
    """Pids with a file open under path. Without lsof the check is skipped."""
    tool = shutil.which("lsof")
    if not tool:
        return []
    return subprocess.run([tool, "-t", "+D", str(path)], capture_output=True, text=True).stdout.split()


def unfinished(P: vpaths.Paths, x: Path) -> bool:
    """A reference with no labels and no kept copy; a re-watch of a kept one does not count."""
    return (x.is_dir() and x.parent.name == "refs"
            and not (x / "labels.json").exists() and not kept(P, x.name))


def ref_state(P: vpaths.Paths, ref: Path) -> str:
    if busy(ref):
        return "busy"
    if kept(P, ref.name):
        return "kept"
    return "unfinished" if unfinished(P, ref) else "measured"


# ---------------------------------------------------------------- keep

def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def dump(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def misread_tracks(labels: dict) -> set:
    """Tracks the labels mark as captions that OCR sorted as titles (note `subtitle_misread`)."""
    return {t["track"] for t in labels.get("titles") or []
            if t.get("note") == "subtitle_misread" and t.get("track") is not None}


def strip_video(mv: dict, mtime: float, misread: set = frozenset()) -> dict:
    """Drop what was said or filmed; title text stays, labels point at it.
    Title tracks in `misread` are captions and lose their text too (learnings 2026-11-06)."""
    mv = dict(mv)
    # files measured before measure_video wrote the date: the file time is the honest one
    mv.setdefault("measured", time.strftime("%Y-%m-%d", time.localtime(mtime)))
    text = mv.get("text")
    if isinstance(text, dict):
        tracks = [t if t.get("kind") == "title" and t.get("i", n) not in misread else dict(t, text="")
                  for n, t in enumerate(text.get("tracks", []))]
        mv["text"] = dict(text, tracks=tracks, transcript=[])
    strips = mv.get("strips")
    if misread and isinstance(strips, dict) and isinstance(strips.get("titles"), list):
        mv["strips"] = dict(strips, titles=[dict(s, text="") if s.get("track") in misread else s
                                            for s in strips["titles"]])
    return mv


def strip_labels(labels: dict) -> dict:
    """Blank the text of titles noted `subtitle_misread`; the label and its note stay."""
    labels = dict(labels)
    if isinstance(labels.get("titles"), list):
        labels["titles"] = [dict(t, text="") if t.get("note") == "subtitle_misread" else t
                            for t in labels["titles"]]
    return labels


def strip_audio(ma: dict) -> dict:
    """Keep speech timing, drop the words."""
    ma = dict(ma)
    tr = ma.get("transcript")
    if isinstance(tr, dict):
        def bare(rows):
            return [{k: v for k, v in r.items() if k != "text"} for r in rows or []]
        ma["transcript"] = dict(tr, segments=bare(tr.get("segments")),
                                whisper=bare(tr.get("whisper")) if tr.get("whisper") else None)
    return ma


def keep(P: vpaths.Paths, ids: list[str], group: str, yes: bool) -> int:
    try:
        dest_root = P.style_refs(group)
    except ValueError as exc:
        print(f"refuse: {exc}; --style takes the top folder (a creator or misc), not GROUP/NAME", file=sys.stderr)
        return 2
    plan = []
    for i in ids:
        src = P.ref(i)
        if not src.is_dir() or not inside_cache(P, src):
            print(f"refuse: {P.rel(src)} is not a reference in the cache", file=sys.stderr)
            return 2
        missing = [n for n in ("measure_video.json", "labels.json") if not (src / n).exists()]
        if missing:
            print(f"refuse: {i} has no {', '.join(missing)}; measure and label it first", file=sys.stderr)
            return 2
        plan.append((src, dest_root / i))
    for src, dest in plan:
        verb = "replace" if dest.exists() else "keep"
        print(f"{verb if yes else 'would ' + verb} {P.rel(src)} -> {P.rel(dest)}")
    if not yes:
        print("dry run: add --yes to write the kept copies", file=sys.stderr)
        return 0
    for src, dest in plan:
        dest.mkdir(parents=True, exist_ok=True)
        mv = src / "measure_video.json"
        labels = load(src / "labels.json")
        dump(dest / mv.name, strip_video(load(mv), mv.stat().st_mtime, misread_tracks(labels)))
        if (src / "measure_audio.json").exists():
            dump(dest / "measure_audio.json", strip_audio(load(src / "measure_audio.json")))
        dump(dest / "labels.json", strip_labels(labels))
        for name in (aggregate.MUSIC_FILE, aggregate.MUSIC_EMBED):  # numbers and CLAP vectors only, nothing to strip
            if (src / name).exists():
                dump(dest / name, load(src / name))
        meta = aggregate.video_meta(src)
        if meta:
            dump(dest / "meta.json", meta)
    return 0


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    k = sub.add_parser("keep")
    k.add_argument("ids", nargs="+")
    k.add_argument("--style", required=True, help="top folder: a creator album or misc")
    k.add_argument("--yes", action="store_true")
    t = sub.add_parser("trash")
    t.add_argument("ids", nargs="*")
    t.add_argument("--all", action="store_true")
    t.add_argument("--yes", action="store_true")
    t.add_argument("--no-keep", action="store_true", help="also trash measured references that were never kept")
    t.add_argument("--unfinished", action="store_true",
                   help="also trash references with no labels and no kept copy")
    args = ap.parse_args(argv)
    P = vpaths.load()

    if args.cmd == "list":
        print(json.dumps(listing(P), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "keep":
        return keep(P, args.ids, args.style, args.yes)

    if args.all == bool(args.ids):
        ap.error("give REF_ID... or --all")
    targets = [P.ref(i) for i in args.ids] if args.ids else [P.abs(e["path"]) for e in listing(P)]
    targets = [x for x in targets if x.exists()]
    for x in targets:
        if not inside_cache(P, x):
            print(f"refuse: {x} is not inside the cache", file=sys.stderr)
            return 2
    in_use = {x: pids for x in targets if (pids := busy(x))}
    not_done = [x for x in targets if x not in in_use and unfinished(P, x) and not args.unfinished]
    verb = "skip" if args.all else "refuse"
    for x, pids in in_use.items():
        print(f"{verb}: {P.rel(x)} is in use by pid {', '.join(pids)}; stop that process first", file=sys.stderr)
    for x in not_done:
        print(f"{verb}: {P.rel(x)} has no labels.json and no kept copy, so it may still be running; "
              "add --unfinished if it is abandoned", file=sys.stderr)
    if args.all:
        targets = [x for x in targets if x not in in_use and x not in not_done]
    elif in_use or not_done:
        return 2
    if not args.no_keep:
        unkept = [x for x in targets if (x / "measure_video.json").exists() and not kept(P, x.name)]
        for x in unkept:
            print(f"refuse: {P.rel(x)} has measurements that were never kept; run "
                  f"`cleanup.py keep {x.name} --style <group> --yes` first, or add --no-keep", file=sys.stderr)
        if unkept:
            return 2
    if not targets:
        print("nothing to trash")
        return 0
    for x in targets:
        print(f"{'trash' if args.yes else 'would trash'} {P.rel(x)} ({du(x) / 2**20:.1f} MB)")
    if not args.yes:
        print("dry run: add --yes to move these to the Trash", file=sys.stderr)
        return 0
    tool = shutil.which("trash")
    if not tool:
        print("`trash` not found; move the folders to the Trash by hand", file=sys.stderr)
        return 2
    r = subprocess.run([tool, *map(str, targets)])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
