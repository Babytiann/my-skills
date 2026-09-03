#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Boundary tests for trade_math.py and evidence_gate.py."""

from __future__ import annotations

import unittest
from datetime import date

from evidence_gate import check_ledger
from trade_math import evaluate

WAIT = "\u7b49\u5f85\u9a8c\u8bc1"
NO_TRADE = "\u4e0d\u4ea4\u6613"
NO_TRADE_BOOK = "\u4e0d\u4ea4\u6613\uff08\u7ec4\u5408\u7ea6\u675f\uff09"
UNCAL = "\u672a\u6821\u51c6\u4f30\u8ba1"


def base_plan(**overrides):
    plan = {
        "account_equity": 1_000_000,
        "account_currency": "CNY",
        "ticker": "TEST",
        "direction": "long",
        "entry": 100.0,
        "stop": 94.0,
        "target": 114.4,
        "target_basis": "technical",
        "adv": 50_000_000,
        "atr20": 3.0,
        "gap_p95": 0.03,
        "theme": "demo",
        "market": "US",
        "instrument_type": "stock",
        "positions": [],
    }
    plan.update(overrides)
    return plan


class TradeMathTests(unittest.TestCase):
    def test_valid_long_sizes_inside_1pct_and_2r(self):
        result = evaluate(base_plan())
        self.assertEqual(result["status"], "ok")
        self.assertLessEqual(result["planned_loss_pct"], 0.01 + 1e-9)
        self.assertGreaterEqual(result["reward_risk_after_fees"], 2.0)
        self.assertEqual(result["win_rate_label"], UNCAL)
        self.assertIsNone(result["expected_value"])

    def test_missing_equity_fails(self):
        result = evaluate(base_plan(account_equity=None))
        self.assertEqual(result["status"], "reject")
        self.assertIn("missing_or_invalid_equity", result["reasons"])

    def test_missing_positions_fails(self):
        plan = base_plan()
        del plan["positions"]
        result = evaluate(plan)
        self.assertEqual(result["status"], "reject")
        self.assertIn("missing_positions", result["reasons"])

    def test_reverse_target_without_basis_rejected(self):
        result = evaluate(base_plan(target=112.0, target_basis=None))
        self.assertEqual(result["status"], "reject")
        self.assertIn("target_basis_required", result["reasons"])
        self.assertEqual(result["action_hint"], WAIT)

    def test_reverse_rr_token_rejected(self):
        result = evaluate(base_plan(target_basis="reverse_rr"))
        self.assertIn("target_basis_required", result["reasons"])

    def test_stop_tighter_than_atr(self):
        result = evaluate(
            base_plan(entry=50, stop=48, target=56, atr20=2, gap_p95=0.12, adv=60_000)
        )
        self.assertIn("stop_tighter_than_1_5_atr", result["reasons"])

    def test_gap_uses_downside_p95_not_fixed_stop(self):
        result = evaluate(
            base_plan(
                entry=50,
                stop=47,
                target=59,
                atr20=2,
                gap_p95=0.12,
                adv=80_000_000,
            )
        )
        self.assertAlmostEqual(result["gap_price"], 44.0, places=2)
        self.assertGreater(result["gap_loss"], result["planned_loss"] - 1e-6)

    def test_proposed_notional_over_3_days_rejected(self):
        result = evaluate(
            base_plan(
                entry=50,
                stop=47,
                target=59,
                atr20=2,
                gap_p95=0.12,
                adv=60_000,
                proposed_notional=100_000,
            )
        )
        self.assertIn("days_to_exit_over_3", result["reasons"])
        self.assertGreater(result["days_to_exit"], 3)

    def test_theme_heat_excludes_index_sleeve(self):
        result = evaluate(
            {
                "account_equity": 1_000_000,
                "account_currency": "CNY",
                "ticker": "AVGO",
                "direction": "long",
                "target_basis": "technical",
                "theme": "semiconductor",
                "proposed_risk_1r": 10_000,
                "instrument_type": "stock",
                "positions": [
                    {"ticker": "NVDA", "direction": "long", "risk_1r": 10_000, "theme": "semiconductor", "sleeve": "tactical"},
                    {"ticker": "AMD", "direction": "long", "risk_1r": 8_000, "theme": "semiconductor", "sleeve": "tactical"},
                    {"ticker": "TSM", "direction": "long", "risk_1r": 7_000, "theme": "semiconductor", "sleeve": "tactical"},
                    {"ticker": "QQQ", "direction": "long", "risk_1r": 450_000, "theme": "nasdaq", "sleeve": "index"},
                ],
            }
        )
        self.assertIn("theme_heat_over_2_5pct", result["reasons"])
        self.assertEqual(result["action_hint"], NO_TRADE_BOOK)
        self.assertAlmostEqual(result["theme_heat"], 35_000, places=1)
        self.assertAlmostEqual(result["theme_release_needed"], 10_000, places=1)
        self.assertLess(result["tactical_open_risk"], 100_000)

    def test_index_sleeve_does_not_block_unrelated_theme(self):
        result = evaluate(
            base_plan(
                theme="biotech",
                proposed_risk_1r=8_000,
                positions=[
                    {"ticker": "QQQ", "direction": "long", "risk_1r": 450_000, "theme": "nasdaq", "sleeve": "index"},
                ],
            )
        )
        self.assertNotIn("theme_heat_over_2_5pct", result["reasons"])

    def test_short_without_borrow_fails(self):
        result = evaluate(
            base_plan(
                direction="short",
                stop=106,
                target=88,
                short_interest_pct=5,
                days_to_cover=1,
            )
        )
        self.assertIn("short_borrow_fee_required", result["reasons"])

    def test_short_uses_half_risk_and_upward_gap(self):
        result = evaluate(
            base_plan(
                direction="short",
                stop=106,
                target=88,
                borrow_fee=0.03,
                short_interest_pct=5,
                days_to_cover=1,
                gap_p95=0.08,
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertLessEqual(result["planned_loss_pct"], 0.005 + 1e-9)
        self.assertGreater(result["gap_price"], 100)

    def test_short_squeeze_rejected(self):
        result = evaluate(
            base_plan(
                direction="short",
                stop=106,
                target=88,
                borrow_fee=0.08,
                short_interest_pct=25,
                days_to_cover=8,
                gap_p95=0.18,
            )
        )
        self.assertIn("short_squeeze_risk", result["reasons"])
        self.assertEqual(result["action_hint"], NO_TRADE)

    def test_leverage_forbidden(self):
        result = evaluate(base_plan(instrument_type="inverse_etf"))
        self.assertIn("leverage_instrument_forbidden", result["reasons"])

    def test_fees_change_reward_risk(self):
        cheap = evaluate(base_plan(fees={"commission_rate": 0.0, "stamp_tax_sell": 0.0, "slippage_bps": 0.0}))
        expensive = evaluate(base_plan(market="HK"))
        self.assertGreater(cheap["reward_risk_after_fees"], expensive["reward_risk_after_fees"])


class EvidenceGateTests(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "claim_id": "C1",
            "ticker": "TEST",
            "claim": "revenue",
            "value_unit": "10000000000 CNY FY25Q1",
            "source_tier": "S0",
            "url": "https://www.sec.gov/edgar/sample",
            "retrieved_at": "2026-09-01",
            "origin_cluster_id": "sec-10q-fy25q1",
            "polarity": "support",
            "evidence_grade": "verified",
            "decision_level": "A",
            "timestamps": {
                "occurred_at": "2026-06-01",
                "disclosed_at": "2026-06-05",
                "known_at": "2026-06-05",
                "priced_at": "2026-06-05",
            },
            "catalyst": "next earnings 2026-12-01",
            "exit_condition": "close below 20dma",
        }
        row.update(overrides)
        return row

    def test_pass_complete_a_level(self):
        result = check_ledger([self._row()], date(2026, 9, 3), strict=True)
        self.assertEqual(result["status"], "PASS")

    def test_missing_url_fails(self):
        result = check_ledger([self._row(url="")], date(2026, 9, 3), strict=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("missing_url" in g for g in result["blocking_gaps"]))

    def test_same_filing_two_aggregators_one_origin(self):
        a = self._row(
            source_tier="S3",
            url="https://macrotrends.net/x",
            origin_cluster_id="fy25q1-filing",
            claim_id="C1",
        )
        b = self._row(
            source_tier="S3",
            url="https://stockanalysis.com/x",
            origin_cluster_id="fy25q1-filing",
            claim_id="C2",
        )
        result = check_ledger([a, b], date(2026, 9, 3), strict=True)
        self.assertEqual(result["independent_origins"], 1)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("deduped" in g for g in result["gaps"]))

    def test_stale_s3_price_fails(self):
        row = self._row(source_tier="S3", retrieved_at="2026-08-01", decision_level="B")
        result = check_ledger([row], date(2026, 9, 3), strict=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("stale_S3" in g for g in result["blocking_gaps"]))

    def test_missing_exit_fails_strict(self):
        row = self._row()
        del row["exit_condition"]
        result = check_ledger([row], date(2026, 9, 3), strict=True)
        self.assertIn("exit_condition_not_registered", result["blocking_gaps"])

    def test_audit_mode_skips_catalyst(self):
        row = self._row()
        del row["catalyst"]
        del row["exit_condition"]
        result = check_ledger([row], date(2026, 9, 3), strict=False)
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
