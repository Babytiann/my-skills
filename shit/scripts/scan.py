#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only workspace scan for the shit skill.

Groups: A disk (~/.claude usage, orphaned transcripts), B always-loaded context
(skill descriptions, CLAUDE.md/rules, memory, MCP), C skill-repo structure.
Prints numbers, paths and names only: never transcript content or credentials.

Usage: python3 -I scan.py [--only disk|context|repo] [--days 30] [--format md|json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

# Single source for thresholds; references/checks.md points here.
THRESHOLDS = {
    "description_chars": 600,  # one skill description
    "description_total_chars": 10_000,  # all installed skill descriptions
    "always_loaded_lines": 200,  # one CLAUDE.md / AGENTS.md / rules file
    "memory_index_lines": 150,  # MEMORY.md
    "skill_md_lines": 300,  # one SKILL.md in the repo
    "project_dir_mb": 100,  # one ~/.claude/projects/<dir>
    "projects_total_mb": 500,  # all transcripts
    "cache_dir_mb": 200,  # file-history, shell-snapshots, telemetry, ...
    "pending_rules": 30,  # rules awaiting the user's review in <repo>/rules/
}

ACTIVE_HOURS = 24
STUB_MAX_CHARS = 200
RECENT_INSTALL_DAYS = 7  # too new for "never used" to mean anything
CACHE_DIRS = ("file-history", "shell-snapshots", "telemetry", "cache", "debug", "plugins", "paste-cache", "todos")
JUNK_NAMES = (".DS_Store", "Thumbs.db")
JUNK_PARTS = ("__pycache__",)
STUB_RE = re.compile(r"统一交由|转交给|请改用|已废弃|已弃用|deprecated|redirects? to|use \S+ instead", re.I)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)
CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
TS_RE = re.compile(r'"timestamp":"([^"]+)"')
SKILL_USE_RE = re.compile(r'"name":"Skill","input":\{"skill":"([^"]+)"')
COMMAND_RE = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
MCP_USE_RE = re.compile(r'"type":"tool_use","id":"[^"]*","name":"mcp__([A-Za-z0-9_.-]+?)__')
MB = 1024 * 1024


# ---------- helpers ----------

def dir_size(path: Path) -> int:
    """Total bytes under path without following symlinks."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    if not path.is_dir() or path.is_symlink():
        return st.st_size
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def fmt_size(n: int) -> str:
    for unit, div in (("G", 1024**3), ("M", MB), ("K", 1024)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n}B"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def iso_day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Top-level keys of a YAML frontmatter block, values as plain strings.

    Handles quoted, plain (possibly folded over several lines) and block (| or >)
    scalars; nested mappings come back as their raw indented text.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    body = lines[1:end]
    out: dict[str, str] = {}
    i = 0
    while i < len(body):
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", body[i])
        if not m:
            i += 1
            continue
        key, raw = m.group(1), m.group(2).strip()
        i += 1
        cont: list[str] = []
        while i < len(body) and (body[i].startswith((" ", "\t")) or not body[i].strip()):
            cont.append(body[i])
            i += 1
        if raw[:1] in ("|", ">"):
            parts = [c.strip() for c in cont]
            joiner = "\n" if raw[0] == "|" else " "
            out[key] = joiner.join(parts).strip()
        elif raw[:1] == '"':
            joined = " ".join([raw] + [c.strip() for c in cont]).strip()
            try:
                out[key] = json.loads(joined)
            except ValueError:
                out[key] = joined.strip('"')
        elif raw[:1] == "'":
            joined = " ".join([raw] + [c.strip() for c in cont]).strip()
            out[key] = joined[1:-1].replace("''", "'") if joined.endswith("'") else joined[1:]
        else:
            out[key] = " ".join([raw] + [c.strip() for c in cont if c.strip()]).strip()
    return out


def first_cwd(project_dir: Path) -> str | None:
    """cwd recorded in the newest transcripts of a ~/.claude/projects/<dir>."""
    files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:5]:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh):
                    if n > 200:
                        break
                    m = CWD_RE.search(line)
                    if m:
                        try:
                            return json.loads(f'"{m.group(1)}"')
                        except ValueError:
                            return m.group(1)
        except OSError:
            continue
    return None


def newest_mtime(path: Path) -> float:
    newest = 0.0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue
    return newest


# ---------- A: disk ----------

def scan_disk(home: Path, days: int, now: float) -> dict:
    claude = home / ".claude"
    top = []
    if claude.is_dir():
        for entry in sorted(claude.iterdir()):
            top.append({"name": entry.name, "bytes": dir_size(entry)})
    top.sort(key=lambda r: r["bytes"], reverse=True)

    projects = []
    pdir = claude / "projects"
    if pdir.is_dir():
        for d in sorted(p for p in pdir.iterdir() if p.is_dir()):
            cwd = first_cwd(d)
            mtime = newest_mtime(d)
            projects.append({
                "dir": d.name,
                "path": str(d),
                "cwd": cwd,
                "cwd_exists": bool(cwd) and Path(cwd).exists(),
                "bytes": dir_size(d),
                "sessions": len(list(d.glob("*.jsonl"))),
                "has_memory": (d / "memory").is_dir(),
                "newest": iso_day(mtime) if mtime else None,
                "active": bool(mtime) and now - mtime < ACTIVE_HOURS * 3600,
            })
    projects.sort(key=lambda r: r["bytes"], reverse=True)

    cleanup = {"value": 30, "source": "默认"}
    settings = claude / "settings.json"
    if settings.is_file():
        try:
            value = json.loads(read_text(settings)).get("cleanupPeriodDays")
            if value is not None:
                cleanup = {"value": value, "source": "settings.json"}
        except ValueError:
            cleanup = {"value": None, "source": "settings.json 解析失败"}

    plans_dir = claude / "plans"
    plan_files = list(plans_dir.glob("*.md")) if plans_dir.is_dir() else []
    cutoff = now - days * 86400
    old_plans = sorted(p.name for p in plan_files if p.stat().st_mtime < cutoff)
    plans = {
        "count": len(plan_files),
        "oldest": iso_day(min(p.stat().st_mtime for p in plan_files)) if plan_files else None,
        "older_than_days": old_plans,
    }

    codex_sessions = home / ".codex" / "sessions"
    return {
        "claude_total": sum(r["bytes"] for r in top),
        "top": top,
        "projects": projects,
        "cleanup_period_days": cleanup,
        "plans": plans,
        "codex_sessions_bytes": dir_size(codex_sessions) if codex_sessions.exists() else None,
    }


# ---------- B: context ----------

def classify_source(entry: Path, target: Path | None, home: Path, repo: Path | None) -> str:
    if target is None:
        return "手动安装"
    t = str(target)
    if repo and (t == str(repo) or t.startswith(str(repo) + os.sep)):
        return "my-skills"
    if "/.agents/skills/" in t + "/":
        return "npx skills"
    if "/.cc-switch/" in t:
        return "cc-switch"
    return f"链接到 {target.parent}"


def collect_skills(base: Path, scope: str, home: Path, repo: Path | None) -> list[dict]:
    rows = []
    if not base.is_dir():
        return rows
    for entry in sorted(base.iterdir()):
        if entry.name.startswith("."):
            continue
        is_link = entry.is_symlink()
        target = Path(os.path.realpath(entry)) if is_link else None
        st = entry.lstat()
        installed = getattr(st, "st_birthtime", st.st_mtime)  # the link's own creation time on macOS
        row = {"name": entry.name, "scope": scope, "path": str(entry), "broken": False, "installed": installed}
        if is_link and not target.exists():
            row.update(broken=True, source=f"坏链接 → {os.readlink(entry)}")
            rows.append(row)
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        fm = parse_frontmatter(read_text(skill_md)) or {}
        desc = fm.get("description", "")
        row.update(
            skill_name=fm.get("name", entry.name),
            description_chars=len(desc),
            source=classify_source(entry, target, home, repo),
            _description=desc,
        )
        rows.append(row)
    return rows


def count_usage(projects_dir: Path, days: int, now: float) -> tuple[Counter, Counter, Counter, int]:
    """Skill-tool calls, slash commands and MCP calls in transcripts within the window."""
    skills, commands, mcp = Counter(), Counter(), Counter()
    cutoff_ts = now - days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    files = 0
    if not projects_dir.is_dir():
        return skills, commands, mcp, files
    for f in projects_dir.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff_ts:
                continue
            files += 1
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "Skill" not in line and "command-name" not in line and "mcp__" not in line:
                        continue
                    # The entry's own timestamp follows its message, so take the last match.
                    stamps = TS_RE.findall(line)
                    if stamps and stamps[-1] < cutoff_iso:
                        continue
                    for m in SKILL_USE_RE.finditer(line):
                        skills[m.group(1)] += 1
                    for m in COMMAND_RE.finditer(line):
                        commands[m.group(1)] += 1
                    for m in MCP_USE_RE.finditer(line):
                        mcp[m.group(1)] += 1
        except OSError:
            continue
    return skills, commands, mcp, files


def mcp_servers(home: Path, project: Path) -> list[dict]:
    """Server names only; the config files also hold tokens, which are never read out."""
    rows = []
    cfg = home / ".claude.json"
    if cfg.is_file():
        try:
            data = json.loads(read_text(cfg))
            for name in (data.get("mcpServers") or {}):
                rows.append({"name": name, "where": "~/.claude.json（用户级）"})
            proj = (data.get("projects") or {}).get(str(project)) or {}
            for name in (proj.get("mcpServers") or {}):
                rows.append({"name": name, "where": "~/.claude.json（本项目）"})
        except ValueError:
            pass
    mcp_json = project / ".mcp.json"
    if mcp_json.is_file():
        try:
            for name in (json.loads(read_text(mcp_json)).get("mcpServers") or {}):
                rows.append({"name": name, "where": ".mcp.json"})
        except ValueError:
            pass
    return rows


def always_loaded(home: Path, project: Path) -> list[dict]:
    candidates: list[Path] = [home / ".claude" / "CLAUDE.md"]
    rules = home / ".claude" / "rules"
    if rules.is_dir():
        candidates += sorted(rules.rglob("*.md"))
    for d in [project, *project.parents]:
        candidates += [d / "CLAUDE.md", d / "CLAUDE.local.md", d / ".claude" / "CLAUDE.md"]
        if d == home:
            break
    prules = project / ".claude" / "rules"
    if prules.is_dir():
        candidates += sorted(prules.rglob("*.md"))
    candidates += [project / "AGENTS.md", home / ".codex" / "AGENTS.md"]
    rows, seen = [], set()
    for p in candidates:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        text = read_text(p)
        fm = parse_frontmatter(text) if p.parent.name == "rules" or "rules" in p.parts else None
        rows.append({
            "path": str(p),
            "lines": len(text.splitlines()),
            "chars": len(text),
            "conditional": bool(fm and fm.get("paths")),
            "agent": "codex" if p.name == "AGENTS.md" else "claude",
        })
    return rows


def memory_health(projects_dir: Path) -> list[dict]:
    rows = []
    if not projects_dir.is_dir():
        return rows
    for mem in sorted(projects_dir.glob("*/memory")):
        index = mem / "MEMORY.md"
        text = read_text(index) if index.is_file() else ""
        linked = {m.group(1) for m in re.finditer(r"\]\(([^)\s]+\.md)\)", text)}
        files = {p.name for p in mem.glob("*.md") if p.name != "MEMORY.md"}
        rows.append({
            "project": mem.parent.name,
            "index_lines": len(text.splitlines()),
            "files": len(files),
            "missing": sorted(l for l in linked if not (mem / l).is_file()),
            "unindexed": sorted(f for f in files if f not in {Path(l).name for l in linked}),
        })
    return rows


def find_stub_target(desc: str, own: str, names: set[str]) -> str | None:
    # Long descriptions that merely point elsewhere for one sub-case are boundaries, not stubs.
    if len(desc) > STUB_MAX_CHARS or not STUB_RE.search(desc):
        return None
    for name in sorted(names, key=len, reverse=True):
        if name != own and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", desc):
            return name
    return "?"


def scan_context(home: Path, project: Path, repo: Path | None, days: int, now: float) -> dict:
    skills = collect_skills(home / ".claude" / "skills", "全局", home, repo)
    if project.resolve() != home.resolve():
        skills += collect_skills(project / ".claude" / "skills", "项目", home, repo)
    used, commands, mcp_used, files = count_usage(home / ".claude" / "projects", days, now)

    names = ({s["name"] for s in skills} | {s.get("skill_name", "") for s in skills}) - {""}
    for s in skills:
        s["recent_install"] = now - s["installed"] < RECENT_INSTALL_DAYS * 86400
        s["installed"] = iso_day(s["installed"])
        if s["broken"]:
            continue
        keys = {s["name"], s.get("skill_name", s["name"])}
        s["uses"] = sum(n for k, n in used.items() if k in keys or k.split(":")[-1] in keys)
        s["uses"] += sum(n for k, n in commands.items() if k in keys or k.split(":")[-1] in keys)
        s["stub_target"] = find_stub_target(s["_description"], s["name"], names)
    dupes = [n for n, c in Counter(s["name"] for s in skills).items() if c > 1]

    servers = mcp_servers(home, project)
    for srv in servers:
        srv["uses"] = mcp_used.get(srv["name"], 0)

    return {
        "skills": skills,
        "description_total": sum(s.get("description_chars", 0) for s in skills),
        "duplicate_names": dupes,
        "usage_files_scanned": files,
        "mcp_servers": servers,
        "always_loaded": always_loaded(home, project),
        "memory": memory_health(home / ".claude" / "projects"),
    }


# ---------- C: repo ----------

def git(repo: Path, *args: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return [l for l in out.stdout.splitlines() if l.strip()]


def find_repo(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if (p / ".git").exists() else None
    top = git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
    return Path(top[0]) if top else None


def scan_repo(repo: Path | None) -> dict:
    if repo is None:
        return {"repo": None, "skills": [], "rules": [], "uncommitted": [], "tracked_junk": []}
    rows = []
    for skill in sorted(p for p in repo.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        skill_md = skill / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = read_text(skill_md)
        fm = parse_frontmatter(text)
        docs = [p for p in skill.rglob("*") if p.is_file() and p.suffix in (".md", ".html", ".yaml", ".py")]
        broken = []
        for doc in (p for p in docs if p.suffix == ".md"):
            for m in LINK_RE.finditer(FENCE_RE.sub("", read_text(doc))):
                href = unquote(m.group(1).split("#", 1)[0])
                if not href or re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I):
                    continue
                if not (doc.parent / href).exists():
                    broken.append(f"{doc.relative_to(skill)} → {href}")
        refs_dir = skill / "references"
        corpus = {p: read_text(p) for p in docs}
        orphans = []
        if refs_dir.is_dir():
            for ref in sorted(refs_dir.rglob("*.md")):
                if not any(ref.name in body for p, body in corpus.items() if p != ref):
                    orphans.append(str(ref.relative_to(skill)))
        rows.append({
            "name": skill.name,
            "skill_md_lines": len(text.splitlines()),
            "frontmatter_ok": fm is not None and "description" in fm,
            "extra_fields": sorted(set(fm or {}) - {"name", "description"}),
            "name_matches": (fm or {}).get("name") == skill.name,
            "broken_links": broken,
            "orphan_references": orphans,
            "has_openai_yaml": (skill / "agents" / "openai.yaml").is_file(),
        })
    # rules/*.md: one rule per "- " line, each ending with its 出处; the user reviews and promotes them.
    rules = []
    rules_dir = repo / "rules"
    if rules_dir.is_dir():
        for p in sorted(rules_dir.glob("*.md")):
            items = [l for l in read_text(p).splitlines() if l.lstrip().startswith("- ")]
            no_source = sum(not re.search(r"出处[：:]", l) for l in items)
            rules.append({"file": f"rules/{p.name}", "rules": len(items), "no_source": no_source})
    tracked = git(repo, "ls-files")
    junk = [p for p in tracked if Path(p).name in JUNK_NAMES or any(part in p.split("/") for part in JUNK_PARTS)]
    return {
        "repo": str(repo),
        "skills": rows,
        "rules": rules,
        "uncommitted": git(repo, "status", "--porcelain"),
        "tracked_junk": junk,
    }


# ---------- findings ----------

def build_findings(disk: dict | None, ctx: dict | None, repo: dict | None) -> list[dict]:
    t = THRESHOLDS
    out: list[dict] = []

    def add(group: str, check: str, text: str, gain: str = "") -> None:
        n = sum(1 for f in out if f["group"] == group) + 1
        out.append({"id": f"{group}{n}", "group": group, "check": check, "text": text, "gain": gain})

    if disk:
        for p in disk["projects"]:
            if p["cwd"] and not p["cwd_exists"] and not p["active"]:
                mem = "，含 memory/" if p["has_memory"] else ""
                add("A", "orphan_project", f"{p['dir']}：原路径 {p['cwd']} 已不存在{mem}", fmt_size(p["bytes"]))
            elif p["bytes"] > t["project_dir_mb"] * MB:
                add("A", "large_project", f"{p['dir']}：{p['sessions']} 个会话，最近写入 {p['newest']}", fmt_size(p["bytes"]))
        proj_total = sum(p["bytes"] for p in disk["projects"])
        if proj_total > t["projects_total_mb"] * MB:
            cp = disk["cleanup_period_days"]
            add("A", "cleanup_period", f"会话记录合计 {fmt_size(proj_total)}，cleanupPeriodDays = {cp['value']}（{cp['source']}）")
        for r in disk["top"]:
            if r["name"] in CACHE_DIRS and r["bytes"] > t["cache_dir_mb"] * MB:
                add("A", "cache_dir", f"~/.claude/{r['name']}", fmt_size(r["bytes"]))
        if disk["plans"]["older_than_days"]:
            add("A", "old_plans", f"{len(disk['plans']['older_than_days'])} 个计划文件超过统计窗口未修改")
        codex = disk["codex_sessions_bytes"]
        if codex and codex > t["projects_total_mb"] * MB:
            add("A", "codex_sessions", "~/.codex/sessions", fmt_size(codex))

    if ctx:
        skills = [s for s in ctx["skills"] if not s["broken"]]
        if ctx["description_total"] > t["description_total_chars"]:
            add("B", "skill_budget", f"{len(skills)} 个 skill，description 合计 {ctx['description_total']} 字", "")
        for s in ctx["skills"]:
            if s["broken"]:
                add("B", "broken_skill_link", f"{s['name']}（{s['scope']}）：{s['source']}")
        for s in sorted(skills, key=lambda s: -s["description_chars"]):
            if s["description_chars"] > t["description_chars"]:
                add("B", "long_description", f"{s['name']}：{s['description_chars']} 字（{s['source']}）")
        for s in skills:
            if s.get("stub_target"):
                add("B", "redirect_stub", f"{s['name']} → {s['stub_target']}（{s['source']}）", f"{s['description_chars']} 字")
        idle = [s for s in skills if s.get("uses", 0) == 0 and not s.get("stub_target")]
        unused = [s for s in idle if not s["recent_install"]]
        if unused:
            chars = sum(s["description_chars"] for s in unused)
            fresh = len(idle) - len(unused)
            note = f"；另有 {fresh} 个安装不到 {RECENT_INSTALL_DAYS} 天，未计入" if fresh else ""
            add("B", "unused_skill", f"{len(unused)} 个 skill 在统计窗口内没有调用记录（仅供参考{note}）", f"{chars} 字")
        for name in ctx["duplicate_names"]:
            add("B", "duplicate_name", f"{name} 同时装在全局和项目里")
        for srv in ctx["mcp_servers"]:
            if srv["uses"] == 0:
                add("B", "unused_mcp", f"{srv['name']}（{srv['where']}）统计窗口内没有调用")
        for f in ctx["always_loaded"]:
            if f["lines"] > t["always_loaded_lines"] and not f["conditional"]:
                add("B", "always_loaded", f"{f['path']}：{f['lines']} 行，{f['chars']} 字")
        for m in ctx["memory"]:
            if m["index_lines"] > t["memory_index_lines"]:
                add("B", "memory_index", f"{m['project']}/memory/MEMORY.md：{m['index_lines']} 行")
            if m["missing"]:
                add("B", "memory_index", f"{m['project']}：索引指向不存在的文件 {', '.join(m['missing'])}")
            if m["unindexed"]:
                add("B", "memory_index", f"{m['project']}：没进索引的文件 {', '.join(m['unindexed'])}")

    if repo and repo["repo"]:
        for s in repo["skills"]:
            if s["skill_md_lines"] > t["skill_md_lines"]:
                add("C", "skill_md_long", f"{s['name']}/SKILL.md：{s['skill_md_lines']} 行")
            if not s["frontmatter_ok"] or s["extra_fields"] or not s["name_matches"]:
                bits = []
                if not s["frontmatter_ok"]:
                    bits.append("缺 frontmatter 或 description")
                if s["extra_fields"]:
                    bits.append("多余字段 " + ", ".join(s["extra_fields"]))
                if not s["name_matches"]:
                    bits.append("name 与目录名不一致")
                add("C", "frontmatter", f"{s['name']}：{'；'.join(bits)}")
            for b in s["broken_links"]:
                add("C", "broken_link", f"{s['name']}/{b}")
            for o in s["orphan_references"]:
                add("C", "orphan_reference", f"{s['name']}/{o} 没有被任何文件引用")
            if not s["has_openai_yaml"]:
                add("C", "missing_openai_yaml", f"{s['name']} 缺 agents/openai.yaml")
        pending = sum(r["rules"] for r in repo["rules"])
        if pending > t["pending_rules"]:
            per_file = "，".join(f"{r['file']} {r['rules']}" for r in repo["rules"])
            add("C", "pending_rules", f"rules/ 下有 {pending} 条待审阅规则（{per_file}）")
        for r in repo["rules"]:
            if r["no_source"]:
                add("C", "missing_provenance", f"{r['file']} 有 {r['no_source']} 条规则没写出处")
        if repo["uncommitted"]:
            add("C", "uncommitted", f"仓库有 {len(repo['uncommitted'])} 项未提交改动")
        if repo["tracked_junk"]:
            add("C", "tracked_junk", "git 跟踪了 " + ", ".join(repo["tracked_junk"]))
    return out


# ---------- render ----------

def render_md(report: dict) -> str:
    meta = report["meta"]
    lines = [f"# 工作区扫描（{meta['time']}，统计窗口 {meta['days']} 天）", ""]
    lines.append("阈值：" + "，".join(f"{k}={v}" for k, v in THRESHOLDS.items()))
    disk, ctx, repo = report.get("disk"), report.get("context"), report.get("repo")

    if disk:
        lines += ["", f"## A 磁盘（~/.claude 合计 {fmt_size(disk['claude_total'])}）", "", "| 目录 | 大小 |", "|---|---|"]
        lines += [f"| {r['name']} | {fmt_size(r['bytes'])} |" for r in disk["top"][:12]]
        lines += ["", "| projects 子目录 | 原路径 | 状态 | 大小 | 会话 | 最近写入 |", "|---|---|---|---|---|---|"]
        for p in disk["projects"]:
            if p["cwd"] and not p["cwd_exists"]:
                state = "孤儿（24h 内有写入）" if p["active"] else "孤儿"
            else:
                state = "活跃" if p["active"] else ("存在" if p["cwd"] else "未知")
            lines.append(f"| {p['dir']} | {p['cwd'] or '-'} | {state} | {fmt_size(p['bytes'])} | {p['sessions']} | {p['newest'] or '-'} |")
        cp = disk["cleanup_period_days"]
        lines += ["", f"- cleanupPeriodDays：{cp['value']}（{cp['source']}）"]
        pl = disk["plans"]
        lines.append(f"- plans：{pl['count']} 个，最早 {pl['oldest'] or '-'}，超过窗口未修改 {len(pl['older_than_days'])} 个")
        if disk["codex_sessions_bytes"] is not None:
            lines.append(f"- ~/.codex/sessions：{fmt_size(disk['codex_sessions_bytes'])}")

    if ctx:
        skills = ctx["skills"]
        ok = [s for s in skills if not s["broken"]]
        sources = Counter(s["source"] for s in ok)
        lines += ["", "## B 常驻上下文", ""]
        lines.append(f"- skill：{len(ok)} 个（" + "，".join(f"{k} {v}" for k, v in sources.most_common()) + f"），description 合计 {ctx['description_total']} 字")
        lines.append(f"- 调用统计：扫描 {ctx['usage_files_scanned']} 个会话文件；只统计 Skill 工具和斜杠命令，模型直接读 SKILL.md、Codex 中的使用都不计，是下限")
        lines += ["", "| skill | 字数 | 来源 | 安装 | 调用 | 备注 |", "|---|---|---|---|---|---|"]
        for s in sorted(ok, key=lambda s: -s["description_chars"])[:15]:
            note = f"桩 → {s['stub_target']}" if s.get("stub_target") else ""
            lines.append(f"| {s['name']} | {s['description_chars']} | {s['source']} | {s['installed']} | {s.get('uses', 0)} | {note} |")
        used = sorted((s for s in ok if s.get("uses")), key=lambda s: -s["uses"])
        lines.append("")
        lines.append("- 有调用的：" + ("，".join(f"{s['name']}×{s['uses']}" for s in used) or "无"))
        unused = [s["name"] + ("（新装）" if s["recent_install"] else "") for s in ok if not s.get("uses")]
        lines.append(f"- 无调用的（{len(unused)}）：" + ("，".join(unused) or "无"))
        if ctx["mcp_servers"]:
            lines.append("- MCP：" + "，".join(f"{m['name']}（{m['where']}，调用 {m['uses']}）" for m in ctx["mcp_servers"]))
        lines += ["", "| 常驻文件 | 行 | 字 | 备注 |", "|---|---|---|---|"]
        for f in ctx["always_loaded"] or [{"path": "（无）", "lines": 0, "chars": 0, "conditional": False, "agent": ""}]:
            note = "按路径条件加载" if f["conditional"] else ("Codex" if f["agent"] == "codex" else "")
            lines.append(f"| {f['path']} | {f['lines']} | {f['chars']} | {note} |")
        if ctx["memory"]:
            lines += ["", "| memory | MEMORY.md 行 | 文件 | 缺失 | 未索引 |", "|---|---|---|---|---|"]
            for m in ctx["memory"]:
                lines.append(f"| {m['project']} | {m['index_lines']} | {m['files']} | {len(m['missing'])} | {len(m['unindexed'])} |")

    if repo:
        lines += ["", f"## C 仓库结构（{repo['repo'] or '未找到仓库，已跳过'}）"]
        if repo["repo"]:
            lines += ["", "| skill | SKILL.md 行 | frontmatter | 坏链接 | 孤儿 reference | openai.yaml |", "|---|---|---|---|---|---|"]
            for s in repo["skills"]:
                fm = "ok" if s["frontmatter_ok"] and not s["extra_fields"] and s["name_matches"] else "有问题"
                lines.append(f"| {s['name']} | {s['skill_md_lines']} | {fm} | {len(s['broken_links'])} | {len(s['orphan_references'])} | {'有' if s['has_openai_yaml'] else '缺'} |")
            rules = "，".join(f"{r['file']} {r['rules']} 条（缺出处 {r['no_source']}）" for r in repo["rules"]) or "无"
            lines += [
                "",
                f"- 待审阅规则：{rules}",
                f"- 未提交改动：{len(repo['uncommitted'])} 项",
                f"- git 跟踪的垃圾文件：{', '.join(repo['tracked_junk']) or '无'}",
            ]

    lines += ["", "## 发现", ""]
    if not report["findings"]:
        lines.append("无。")
    for f in report["findings"]:
        gain = f"（{f['gain']}）" if f["gain"] else ""
        lines.append(f"- **{f['id']}** `{f['check']}` {f['text']}{gain}")
    return "\n".join(lines) + "\n"


def strip_private(obj):
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def run(home: Path, project: Path, repo: Path | None, only: str | None, days: int, now: float | None = None) -> dict:
    now = now or time.time()
    report: dict = {"meta": {
        "time": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
        "days": days, "home": str(home), "project": str(project), "thresholds": THRESHOLDS,
    }}
    disk = scan_disk(home, days, now) if only in (None, "disk") else None
    ctx = scan_context(home, project, repo, days, now) if only in (None, "context") else None
    rep = scan_repo(repo) if only in (None, "repo") else None
    report.update({k: v for k, v in (("disk", disk), ("context", ctx), ("repo", rep)) if v is not None})
    report["findings"] = build_findings(disk, ctx, rep)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only agent workspace scan.")
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--repo", default=None, help="skill repo; default: the git repo containing this script")
    ap.add_argument("--only", choices=("disk", "context", "repo"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--format", choices=("md", "json"), default="md")
    args = ap.parse_args(argv)

    home = Path(args.home).expanduser().resolve()
    report = run(home, Path(args.project).expanduser().resolve(), find_repo(args.repo), args.only, args.days)
    if args.format == "json":
        json.dump(strip_private(report), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_md(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
