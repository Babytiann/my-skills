#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path table for the vlog-style skill.

paths.toml (next to SKILL.md) holds the only absolute path, `root`. Every other
location is written relative to it, so moving the library to a NAS means
editing one line. VLOG_STYLE_ROOT overrides `root` for a single run (tests, a
trial mount) without touching the file.

Rules enforced here:
- files under root are referred to by relative POSIX paths ("projects/x/a.mp4");
- an absolute path is built only at the moment a tool needs it and is never
  written back into JSON or Markdown (`check` finds violations).

Usage:
  python3 -I -B paths.py show
  python3 -I -B paths.py init [--style NAME] [--project NAME]
  python3 -I -B paths.py resolve REL
  python3 -I -B paths.py check FILE_OR_DIR...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG = SKILL_DIR / "paths.toml"
ENV_ROOT = "VLOG_STYLE_ROOT"

STYLE_SUBDIRS = ("stills", "luts", "drx", "fusion")
PROJECT_SUBDIRS = ("footage", "proxies", "index", "plan", "qa")

NAME_RE = re.compile(r"^[^\W_][\w.-]*$")
# Absolute paths that should never appear in generated files. Limited to real
# filesystem roots so URLs (https://host/video/...) are not flagged.
ABS_IN_TEXT = re.compile(
    r"(?<![\w:/.])(?:~/|/(?:Users|Volumes|private|tmp|var|home|opt|Library|Applications|System|mnt|media)/)"
    r"|\b[A-Za-z]:\\\\?"
)


@dataclass(frozen=True)
class Paths:
    root: Path
    dirs: dict[str, str] = field(default_factory=dict)

    def abs(self, rel: str | PurePosixPath) -> Path:
        """Relative POSIX path under root -> absolute Path. Refuses escapes."""
        rel = str(rel)
        if not rel or rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel):
            raise ValueError(f"expected a path relative to root, got {rel!r}")
        parts = PurePosixPath(rel).parts
        if ".." in parts:
            raise ValueError(f"'..' is not allowed in {rel!r}")
        return self.root.joinpath(*parts)

    def rel(self, path: str | Path) -> str:
        """Absolute path under root -> relative POSIX string."""
        p = Path(os.path.abspath(Path(path).expanduser()))
        root = Path(os.path.abspath(self.root))
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            raise ValueError(f"{p} is outside root {root}") from None

    def dir(self, key: str) -> Path:
        return self.abs(self.dirs[key])

    def style(self, name: str) -> Path:
        return self.dir("styles") / check_name(name)

    def project(self, name: str) -> Path:
        return self.dir("projects") / check_name(name)

    def ref(self, ref_id: str) -> Path:
        return self.dir("cache") / "refs" / check_name(ref_id)


def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad name {name!r}: use letters, digits, '.', '_' or '-'")
    return name


def load(config: Path = CONFIG) -> Paths:
    with open(config, "rb") as fh:
        data = tomllib.load(fh)
    root = os.environ.get(ENV_ROOT) or data.get("root")
    if not root:
        raise SystemExit(f"{config}: missing root")
    dirs = dict(data.get("dirs") or {})
    for key in ("styles", "projects", "cache"):
        dirs.setdefault(key, key)
    for key, value in dirs.items():
        if value.startswith(("/", "~")) or ".." in PurePosixPath(value).parts:
            raise SystemExit(f"{config}: dirs.{key} must be relative to root, got {value!r}")
    return Paths(Path(root).expanduser(), dirs)


def init(paths: Paths, style: str | None = None, project: str | None = None) -> list[Path]:
    made = []
    targets = [paths.dir(k) for k in ("styles", "projects", "cache")]
    if style:
        targets += [paths.style(style) / d for d in STYLE_SUBDIRS]
    if project:
        targets += [paths.project(project) / d for d in PROJECT_SUBDIRS]
    for t in targets:
        if not t.exists():
            t.mkdir(parents=True)
            made.append(t)
    return made


def _json_strings(value, where="$"):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _json_strings(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _json_strings(v, f"{where}[{i}]")


def find_absolute(path: Path) -> list[str]:
    """Report absolute paths inside one JSON/MD/TXT file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = []
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return [f"{path}: invalid JSON ({exc})"]
        for where, s in _json_strings(data):
            if s.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:[\\/]", s):
                hits.append(f"{path}: {where} = {s[:120]!r}")
    else:
        for n, line in enumerate(text.splitlines(), 1):
            if ABS_IN_TEXT.search(line):
                hits.append(f"{path}:{n}: {line.strip()[:120]}")
    return hits


def check(targets: list[Path]) -> list[str]:
    hits = []
    for t in targets:
        files = [t] if t.is_file() else sorted(
            p for p in t.rglob("*") if p.suffix in {".json", ".md", ".txt", ".toml"} and p.is_file()
        )
        for f in files:
            if f.resolve() == CONFIG.resolve():
                continue  # the one place an absolute root is allowed
            hits += find_absolute(f)
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p_init = sub.add_parser("init")
    p_init.add_argument("--style")
    p_init.add_argument("--project")
    p_res = sub.add_parser("resolve")
    p_res.add_argument("rel")
    p_chk = sub.add_parser("check")
    p_chk.add_argument("targets", nargs="+", type=Path)
    args = ap.parse_args(argv)

    paths = load()
    if args.cmd == "show":
        print(json.dumps({
            "root": str(paths.root),
            "root_source": ENV_ROOT if os.environ.get(ENV_ROOT) else "paths.toml",
            "root_exists": paths.root.exists(),
            "dirs": paths.dirs,
        }, ensure_ascii=False, indent=2))
    elif args.cmd == "init":
        for made in init(paths, args.style, args.project):
            print(f"created {paths.rel(made)}")
    elif args.cmd == "resolve":
        print(paths.abs(args.rel))
    elif args.cmd == "check":
        hits = check(args.targets)
        for h in hits:
            print(h)
        print(f"{len(hits)} absolute path(s) found", file=sys.stderr)
        return 1 if hits else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
