#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forward-test the 12 offline evals against scripts plus skill text."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from evidence_gate import check_ledger
from trade_math import evaluate

WAIT = "\u7b49\u5f85\u9a8c\u8bc1"
NO_TRADE = "\u4e0d\u4ea4\u6613"
NO_TRADE_BOOK = "\u4e0d\u4ea4\u6613\uff08\u7ec4\u5408\u7ea6\u675f\uff09"
UNCAL = "\u672a\u6821\u51c6\u4f30\u8ba1"

ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (ROOT / "SKILL.md").read_text(encoding="utf-8")
REF_TEXT = "\n".join(
    path.read_text(encoding="utf-8") for path in sorted((ROOT / "references").glob("*.md"))
)
ALL_TEXT = SKILL_TEXT + "\n" + REF_TEXT


def _ok(condition: bool, message: str) -> str | None:
    return None if condition else message


def eval_01() -> list[str]:
    result = evaluate(
        {
            "account_equity": 1_000_000,
            "account_currency": "CNY",
            "ticker": "AVGO",
            "direction": "long",
            "target_basis": "technical",
            "theme": "semiconductor",
            "proposed_risk_1r": 10_000,
            "positions": [
                {"ticker": "NVDA", "direction": "long", "risk_1r": 10_000, "theme": "semiconductor", "sleeve": "tactical"},
                {"ticker": "AMD", "direction": "long", "risk_1r": 8_000, "theme": "semiconductor", "sleeve": "tactical"},
                {"ticker": "TSM", "direction": "long", "risk_1r": 7_000, "theme": "semiconductor", "sleeve": "tactical"},
                {"ticker": "QQQ", "direction": "long", "risk_1r": 450_000, "theme": "nasdaq", "sleeve": "index"},
            ],
        }
    )
    return list(
        filter(
            None,
            [
                _ok(result["action_hint"] == NO_TRADE_BOOK, "e1 action"),
                _ok(abs(result["theme_heat"] - 35_000) < 1, "e1 theme heat 35000"),
                _ok(abs(result["theme_release_needed"] - 10_000) < 1, "e1 release 10000"),
                _ok(result["tactical_open_risk"] < 100_000, "e1 QQQ excluded"),
            ],
        )
    )


def eval_02() -> list[str]:
    result = evaluate(
        {
            "account_equity": 1_000_000,
            "account_currency": "CNY",
            "direction": "long",
            "entry": 100,
            "stop": 94,
            "target": 112,
            "target_basis": None,
            "adv": 10_000_000,
            "atr20": 3,
            "gap_p95": 0.04,
            "positions": [],
        }
    )
    return list(
        filter(
            None,
            [
                _ok(result["action_hint"] == WAIT, "e2 wait"),
                _ok("target_basis_required" in result["reasons"], "e2 reverse target"),
                _ok(result["win_rate_label"] == UNCAL, "e2 uncalibrated"),
                _ok(result["expected_value"] is None, "e2 no forged EV"),
            ],
        )
    )


def eval_03() -> list[str]:
    result = evaluate(
        {
            "account_equity": 1_000_000,
            "account_currency": "CNY",
            "direction": "long",
            "entry": 50,
            "stop": 48,
            "target": 56,
            "target_basis": "technical",
            "atr20": 2,
            "gap_p95": 0.12,
            "adv": 60_000,
            "proposed_notional": 100_000,
            "positions": [],
        }
    )
    return list(
        filter(
            None,
            [
                _ok("stop_tighter_than_1_5_atr" in result["reasons"], "e3 atr"),
                _ok(abs((result["gap_price"] or 0) - 44) < 0.01, "e3 downward gap 44"),
                _ok("days_to_exit_over_3" in result["reasons"], "e3 liquidity"),
                _ok(result["action_hint"] == NO_TRADE, "e3 no trade"),
            ],
        )
    )


def eval_04() -> list[str]:
    return list(
        filter(
            None,
            [
                _ok("6 \u5468" in ALL_TEXT and "\u51cf\u534a" in ALL_TEXT, "e4 event window"),
                _ok("48 \u5c0f\u65f6" in ALL_TEXT, "e4 post catalyst"),
                _ok("\u65f6\u95f4\u6b62\u635f" in ALL_TEXT, "e4 time stop"),
            ],
        )
    )


def eval_05() -> list[str]:
    return list(
        filter(
            None,
            [
                _ok("\u5df2\u89e6\u53d1\u9000\u51fa" in SKILL_TEXT, "e5 held action"),
                _ok("\u4e0d\u91cd\u65b0\u8bba\u8bc1" in SKILL_TEXT, "e5 no re-argue"),
                _ok("\u4e0d\u6539\u53e3\u957f\u6301" in SKILL_TEXT or "\u6539\u53e3\u6210\u957f\u7ebf" in ALL_TEXT, "e5 no style drift"),
            ],
        )
    )


def eval_06() -> list[str]:
    result = evaluate(
        {
            "account_equity": 1_000_000,
            "account_currency": "CNY",
            "direction": "short",
            "entry": 100,
            "stop": 106,
            "target": 88,
            "target_basis": "technical",
            "atr20": 3,
            "gap_p95": 0.18,
            "adv": 20_000_000,
            "borrow_fee": 0.1,
            "short_interest_pct": 25,
            "days_to_cover": 8,
            "positions": [],
        }
    )
    return list(
        filter(
            None,
            [
                _ok("short_squeeze_risk" in result["reasons"], "e6 squeeze"),
                _ok(result["action_hint"] == NO_TRADE, "e6 no trade"),
                _ok("0.5%" in ALL_TEXT and "\u5411\u4e0a" in ALL_TEXT, "e6 short rules present"),
                _ok("\u7981\u6b62\u6760\u6746" in SKILL_TEXT, "e6 no leverage"),
            ],
        )
    )


def eval_07() -> list[str]:
    return list(
        filter(
            None,
            [
                _ok("\u5f00\u59cb\u6216\u7ee7\u7eed\u5206\u6279\u914d\u7f6e" in SKILL_TEXT, "e7 default accumulate"),
                _ok("\u6682\u4e0d\u914d\u7f6e" in ALL_TEXT and "\u5386\u53f2\u6781\u503c" in ALL_TEXT, "e7 extreme bar"),
                _ok("\u5bbd\u57fa\u8896\u4e0d\u8ba1\u5165\u4e3b\u9898\u70ed\u91cf" in SKILL_TEXT, "e7 sleeve split"),
            ],
        )
    )


def eval_08() -> list[str]:
    rows = [
        {
            "claim_id": "C1",
            "ticker": "FOO",
            "claim": "quarterly revenue 10bn",
            "value_unit": "10000000000 CNY",
            "source_tier": "S3",
            "url": "",
            "retrieved_at": "",
            "origin_cluster_id": "same-10q",
            "polarity": "support",
            "evidence_grade": "strong",
            "decision_level": "A",
        },
        {
            "claim_id": "C2",
            "ticker": "FOO",
            "claim": "quarterly revenue 10bn",
            "value_unit": "10000000000 CNY",
            "source_tier": "S3",
            "url": "",
            "retrieved_at": "",
            "origin_cluster_id": "same-10q",
            "polarity": "support",
            "evidence_grade": "strong",
            "decision_level": "A",
        },
    ]
    result = check_ledger(rows, date(2026, 9, 3), strict=True)
    return list(
        filter(
            None,
            [
                _ok(result["status"] == "FAIL", "e8 fail"),
                _ok(result["independent_origins"] == 1, "e8 one origin"),
                _ok(result["action_hint"] == WAIT, "e8 wait"),
                _ok(any("missing_url" in g for g in result["blocking_gaps"]), "e8 url"),
            ],
        )
    )


def eval_09() -> list[str]:
    return list(
        filter(
            None,
            [
                _ok("\u5927\u90e8\u5206\u6da8\u5e45\u82e5\u5728\u62ab\u9732\u524d\u5b8c\u6210" in ALL_TEXT, "e9 timeline rule"),
                _ok("\u6761\u4ef6\u652f\u6301" in ALL_TEXT and "\u8bc1\u636e\u4e0d\u8db3" in ALL_TEXT, "e9 grades"),
                _ok("\u6218\u4e89" in ALL_TEXT and "\u5b9e\u9645\u5229\u7387" in ALL_TEXT, "e9 gold alts"),
                _ok("\u53ef\u590d\u7528" in ALL_TEXT or "\u4e0b\u6b21\u5f00\u6218" in ALL_TEXT, "e9 no law"),
            ],
        )
    )


def eval_10() -> list[str]:
    return list(
        filter(
            None,
            [
                _ok("\u80cc\u666f\u8106\u5f31\u6027" in SKILL_TEXT and "\u653e\u5927\u673a\u5236" in SKILL_TEXT, "e10 split"),
                _ok("\u8fc7\u53bb\u4e0a\u6da8\u4e0d\u662f\u7ee7\u7eed\u4e0a\u6da8" in SKILL_TEXT, "e10 no extrapolation"),
                _ok("\u8dcc\u591a\u4e86" in ALL_TEXT, "e10 no catch knife"),
            ],
        )
    )


def eval_11() -> list[str]:
    result = evaluate(
        {
            "account_equity": 1_000_000,
            "account_currency": "CNY",
            "direction": "long",
            "entry": 100,
            "stop": 94,
            "target": 114.4,
            "target_basis": "technical",
            "atr20": 3,
            "gap_p95": 0.03,
            "adv": 80_000_000,
            "theme": "industry",
            "positions": [],
        }
    )
    return list(
        filter(
            None,
            [
                _ok(result["status"] == "ok", "e11 ok " + str(result["reasons"])),
                _ok(result["target_basis"] == "technical", "e11 basis"),
                _ok((result["reward_risk_after_fees"] or 0) >= 2, "e11 rr"),
                _ok("\u4fdd\u8bc1\u6536\u76ca" in SKILL_TEXT, "e11 guarantee banned"),
            ],
        )
    )


def eval_12() -> list[str]:
    return list(
        filter(
            None,
            [
                _ok("\u9ad8\u5ea6\u652f\u6301\u4ecd\u4e0d\u662f\u4ea4\u6613\u8bb8\u53ef" in ALL_TEXT or "\u6536\u8d2d\u8df3\u6da8" in ALL_TEXT, "e12 split"),
                _ok("\u65e0\u98ce\u9669\u5957\u5229" in ALL_TEXT, "e12 not arb"),
                _ok(WAIT in SKILL_TEXT, "e12 wait available"),
            ],
        )
    )


def redlines() -> list[str]:
    # worked-examples.md quotes forbidden phrases as counterexamples.
    instructional = SKILL_TEXT + "\n" + "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "references").glob("*.md"))
        if path.name != "worked-examples.md"
    )
    hits = []
    if "C0\u2013C6" in instructional or "C0-C6" in instructional:
        hits.append("old C0-C6 protocol leaked")
    if "\u7a33\u8d5a" in instructional:
        hits.append("guaranteed profit")
    if "\u4e00\u5b9a\u8d5a\u94b1" in instructional:
        hits.append("guaranteed profit 2")
    if "\u5386\u53f2\u80dc\u7387 58" in instructional:
        hits.append("fabricated win rate")
    if "\u8986\u76d6 financial-data" in instructional:
        hits.append("claims to override financial-data at runtime")
    return hits


def main() -> int:
    fns = [
        eval_01,
        eval_02,
        eval_03,
        eval_04,
        eval_05,
        eval_06,
        eval_07,
        eval_08,
        eval_09,
        eval_10,
        eval_11,
        eval_12,
    ]
    report = {"passed": [], "failed": []}
    for idx, fn in enumerate(fns, start=1):
        errors = fn()
        if errors:
            report["failed"].append({"eval": idx, "errors": errors})
        else:
            report["passed"].append(idx)
    red = redlines()
    report["redlines"] = red
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failed"] and not red else 1


if __name__ == "__main__":
    sys.exit(main())
