#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixture tests for scan.py. Run: python3 -I -B test_scan.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir

import scan  # noqa: E402

SECRET = "sk-should-never-appear"
NOW = time.time()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def age(path: Path, days: float) -> None:
    ts = NOW - days * 86400
    os.utime(path, (ts, ts))


class ScanFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.home = home = root / "home"
        claude = home / ".claude"
        live = root / "live"
        live.mkdir()

        # projects: one whose cwd exists, one whose cwd is gone
        recent, old = iso(NOW - 3600), iso(NOW - 40 * 86400)
        live_lines = [
            {"type": "user", "cwd": str(live), "timestamp": recent},
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Skill","input":{"skill":"alpha"}}]},"timestamp":"%s"}' % recent,
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t2","name":"Skill","input":{"skill":"alpha"}}]},"timestamp":"%s"}' % old,
            '{"type":"user","message":{"content":"<command-name>/beta</command-name>"},"timestamp":"%s"}' % recent,
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t3","name":"mcp__srv__do","input":{}}]},"timestamp":"%s"}' % recent,
        ]
        body = "\n".join(l if isinstance(l, str) else json.dumps(l) for l in live_lines) + "\n"
        live_dir = claude / "projects" / "-live"
        age(write(live_dir / "s1.jsonl", body), 2)
        gone_dir = claude / "projects" / "-gone"
        age(write(gone_dir / "s2.jsonl", json.dumps({"cwd": str(root / "gone"), "timestamp": recent}) + "\n"), 2)

        # memory: one indexed file, one missing target, one unindexed file
        mem = live_dir / "memory"
        write(mem / "MEMORY.md", "- [A](a.md) — a\n- [Missing](missing.md) — gone\n")
        write(mem / "a.md", "---\nname: a\n---\nfact\n")
        write(mem / "b.md", "---\nname: b\n---\nfact\n")

        # skills: block scalar, quoted (symlinked), plain stub, broken link
        skills = claude / "skills"
        write(skills / "alpha" / "SKILL.md", "---\nname: alpha\ndescription: |\n  Alpha does things.\n  Second line.\nmetadata:\n  user_invocable: true\n---\nbody\n")
        store = root / "store"
        write(store / "beta" / "SKILL.md", '---\nname: beta\ndescription: "Beta \\"quoted\\" skill."\n---\n')
        os.symlink(store / "beta", skills / "beta")
        write(skills / "gamma" / "SKILL.md", "---\nname: gamma\ndescription: 仅在显式指定时使用，相关请求统一交由 alpha 技能处理。\n---\n")
        long_ref = "x" * 300 + " 多维表格请改用 alpha。"
        write(skills / "delta" / "SKILL.md", f"---\nname: delta\ndescription: {long_ref}\n---\n")
        old_install = NOW - 30 * 86400
        os.utime(skills / "delta", (old_install, old_install), follow_symlinks=False)  # moves birthtime back on macOS
        write(skills / "epsilon" / "SKILL.md", "---\nname: epsilon\ndescription: fresh\n---\n")
        os.symlink(store / "nonexistent", skills / "ghost")

        write(claude / "settings.json", json.dumps({"cleanupPeriodDays": 14, "env": {"TOKEN": SECRET}}))
        write(home / ".claude.json", json.dumps({"mcpServers": {"srv": {"env": {"KEY": SECRET}}, "idle": {}}, "oauthAccount": SECRET}))
        age(write(claude / "plans" / "old.md", "plan"), 60)
        write(claude / "plans" / "new.md", "plan")

        # repo: one skill with problems, plus a tracked .DS_Store
        cls.repo = repo = root / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        write(repo / "good" / "SKILL.md", "---\nname: wrong\ndescription: \"ok\"\nlicense: MIT\n---\n[a](references/a.md) [m](references/missing.md) [web](https://x.com)\n```\n[fenced](nope.md)\n```\n")
        write(repo / "good" / "references" / "a.md", "a")
        write(repo / "good" / "references" / "orphan.md", "o")
        sourced = "".join(f"- 规则 {i}。（出处：用户提供，2026-11-01）\n" for i in range(31))
        write(repo / "rules" / "global.md", f"# 全局规则\n\n## Git\n\n{sourced}- 无出处的规则。\n")
        write(repo / ".DS_Store", "junk")
        subprocess.run(["git", "-C", str(repo), "add", ".DS_Store"], check=True)

        cls.report = scan.run(home, live, repo, None, 30, now=NOW)
        cls.checks = [f["check"] for f in cls.report["findings"]]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def skill(self, name):
        return next(s for s in self.report["context"]["skills"] if s["name"] == name)

    def test_frontmatter_styles(self):
        self.assertEqual(scan.parse_frontmatter("---\nname: a\ndescription: |\n  one\n  two\n---\n")["description"], "one\ntwo")
        self.assertEqual(scan.parse_frontmatter("---\ndescription: >-\n  one\n  two\n---\n")["description"], "one two")
        self.assertEqual(scan.parse_frontmatter('---\ndescription: "a \\"b\\""\n---\n')["description"], 'a "b"')
        self.assertEqual(scan.parse_frontmatter("---\ndescription: 'it''s'\n---\n")["description"], "it's")
        self.assertEqual(scan.parse_frontmatter("---\ndescription: plain\n  folded\n---\n")["description"], "plain folded")
        self.assertIsNone(scan.parse_frontmatter("no frontmatter"))

    def test_orphan_project(self):
        rows = {p["dir"]: p for p in self.report["disk"]["projects"]}
        self.assertFalse(rows["-gone"]["cwd_exists"])
        self.assertTrue(rows["-live"]["cwd_exists"])
        orphans = [f["text"] for f in self.report["findings"] if f["check"] == "orphan_project"]
        self.assertEqual(len(orphans), 1)
        self.assertIn("-gone", orphans[0])

    def test_settings_and_plans(self):
        disk = self.report["disk"]
        self.assertEqual(disk["cleanup_period_days"]["value"], 14)
        self.assertEqual(disk["plans"]["older_than_days"], ["old.md"])

    def test_usage_window(self):
        self.assertEqual(self.skill("alpha")["uses"], 1)  # the 40-day-old call is outside the window
        self.assertEqual(self.skill("beta")["uses"], 1)  # via slash command
        servers = {m["name"]: m["uses"] for m in self.report["context"]["mcp_servers"]}
        self.assertEqual(servers, {"srv": 1, "idle": 0})
        self.assertIn("unused_mcp", self.checks)

    def test_skill_sources_and_stubs(self):
        self.assertEqual(self.skill("alpha")["description_chars"], len("Alpha does things.\nSecond line."))
        self.assertEqual(self.skill("beta")["source"], "链接到 " + str(Path(os.path.realpath(self.home.parent / "store"))))
        self.assertEqual(self.skill("gamma")["stub_target"], "alpha")
        self.assertIsNone(self.skill("delta")["stub_target"])  # long description with a boundary note
        self.assertTrue(self.skill("ghost")["broken"])
        self.assertIn("broken_skill_link", self.checks)

    def test_unused_skips_recent_installs(self):
        self.assertFalse(self.skill("delta")["recent_install"])
        self.assertTrue(self.skill("epsilon")["recent_install"])
        unused = [f["text"] for f in self.report["findings"] if f["check"] == "unused_skill"]
        self.assertEqual(len(unused), 1)
        self.assertTrue(unused[0].startswith("1 个 skill"))
        self.assertIn("另有 1 个安装不到", unused[0])

    def test_memory_index(self):
        mem = next(m for m in self.report["context"]["memory"] if m["project"] == "-live")
        self.assertEqual(mem["missing"], ["missing.md"])
        self.assertEqual(mem["unindexed"], ["b.md"])

    def test_repo_structure(self):
        good = next(s for s in self.report["repo"]["skills"] if s["name"] == "good")
        self.assertEqual(good["broken_links"], ["SKILL.md → references/missing.md"])
        self.assertEqual(good["orphan_references"], ["references/orphan.md"])
        self.assertEqual(good["extra_fields"], ["license"])
        self.assertFalse(good["name_matches"])
        self.assertFalse(good["has_openai_yaml"])
        self.assertEqual(self.report["repo"]["tracked_junk"], [".DS_Store"])
        for check in ("broken_link", "orphan_reference", "frontmatter", "missing_openai_yaml", "tracked_junk"):
            self.assertIn(check, self.checks)

    def test_pending_rules(self):
        self.assertEqual(self.report["repo"]["rules"], [{"file": "rules/global.md", "rules": 32, "no_source": 1}])
        texts = {f["check"]: f["text"] for f in self.report["findings"]}
        self.assertIn("32 条待审阅规则", texts["pending_rules"])
        self.assertIn("1 条规则没写出处", texts["missing_provenance"])

    def test_no_secrets_or_descriptions_in_output(self):
        md = scan.render_md(self.report)
        js = json.dumps(scan.strip_private(self.report), ensure_ascii=False)
        for out in (md, js):
            self.assertNotIn(SECRET, out)
            self.assertNotIn("Alpha does things", out)

    def test_only_filter(self):
        report = scan.run(self.home, self.home, self.repo, "repo", 30, now=NOW)
        self.assertNotIn("disk", report)
        self.assertNotIn("context", report)
        self.assertTrue(all(f["group"] == "C" for f in report["findings"]))


if __name__ == "__main__":
    unittest.main()
