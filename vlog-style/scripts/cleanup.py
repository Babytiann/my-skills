#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List the vlog-style cache and move entries to the Trash.

Reference downloads, proxies, sheets and strips live in <root>/cache/refs/<id>/
and are not kept once a style pack is accepted. This never deletes: it calls
`trash` (macOS Trash, recoverable) and refuses anything outside <root>/cache.
Without --yes it only prints what would move.

Usage:
  python3 -I -B cleanup.py list
  python3 -I -B cleanup.py trash REF_ID... [--yes]
  python3 -I -B cleanup.py trash --all [--yes]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

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
            out.append({"path": P.rel(k), "mb": round(du(k) / 2**20, 1),
                        "files": sum(1 for _ in k.rglob("*")) if k.is_dir() else 1})
    return out


def inside_cache(P: vpaths.Paths, target: Path) -> bool:
    cache = P.dir("cache").resolve()
    t = target.resolve()
    return t != cache and t.is_relative_to(cache)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("trash")
    t.add_argument("ids", nargs="*")
    t.add_argument("--all", action="store_true")
    t.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    P = vpaths.load()

    if args.cmd == "list":
        print(json.dumps(listing(P), ensure_ascii=False, indent=1))
        return 0

    if args.all == bool(args.ids):
        ap.error("give REF_ID... or --all")
    targets = [P.ref(i) for i in args.ids] if args.ids else [P.abs(e["path"]) for e in listing(P)]
    targets = [x for x in targets if x.exists()]
    for x in targets:
        if not inside_cache(P, x):
            print(f"refuse: {x} is not inside the cache", file=sys.stderr)
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
