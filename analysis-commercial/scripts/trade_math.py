#!/usr/bin/env python3
"""Size a swing ticket and refuse plans that break book, gap, or capacity rules."""

from __future__ import annotations

import json
import math
import sys
from typing import Any


LONG_RISK = 0.01
SHORT_RISK = 0.005
GAP_CAP = 0.02
TACTICAL_CAP = 0.06
THEME_CAP = 0.025
SHORT_BOOK_CAP = 0.02
ATR_MULT = 1.5
LONG_PARTICIPATION = 0.10
SHORT_PARTICIPATION = 0.05
SQUEEZE_SI = 20.0
SQUEEZE_DTC = 5.0
LEVERAGE_TYPES = {"leveraged_etf", "inverse_etf", "option", "futures", "margin"}
VALID_BASIS = {"technical", "valuation_reversion", "event_reprice"}
FEE_TABLE = {
    "US": {"commission_rate": 0.0005, "stamp_tax_sell": 0.0, "slippage_bps": 5.0},
    "HK": {"commission_rate": 0.0008, "stamp_tax_sell": 0.0010, "slippage_bps": 8.0},
    "CN": {"commission_rate": 0.00025, "stamp_tax_sell": 0.0005, "slippage_bps": 8.0},
}


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100.0 if value <= 1.0 else value


def _as_decimal(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if value > 1.0 else value


def _fees(market: str, override: dict[str, Any] | None) -> dict[str, float]:
    base = dict(FEE_TABLE.get(str(market).upper(), FEE_TABLE["US"]))
    if override:
        for key in base:
            if key in override and override[key] is not None:
                base[key] = float(override[key])
    return base


def _round_trip_fee(notional: float, fee: dict[str, float], borrow_monthly: float = 0.0) -> float:
    commission = abs(notional) * fee["commission_rate"] * 2.0
    stamp = abs(notional) * fee["stamp_tax_sell"]
    slip = abs(notional) * (fee["slippage_bps"] / 10000.0) * 2.0
    return commission + stamp + slip + borrow_monthly


def evaluate(plan: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []

    equity = _num(plan.get("account_equity"))
    currency = plan.get("account_currency")
    if equity is None or equity <= 0:
        reasons.append("missing_or_invalid_equity")
    if not currency:
        reasons.append("missing_account_currency")
    if "positions" not in plan or plan.get("positions") is None:
        reasons.append("missing_positions")

    instrument = str(plan.get("instrument_type") or "stock").lower()
    if instrument in LEVERAGE_TYPES:
        reasons.append("leverage_instrument_forbidden")

    direction = str(plan.get("direction") or "long").lower()
    if direction not in {"long", "short"}:
        reasons.append("invalid_direction")
        direction = "long"

    target_basis = plan.get("target_basis")
    if target_basis not in VALID_BASIS:
        reasons.append("target_basis_required")

    positions = plan.get("positions") or []
    theme = plan.get("theme") or ""
    proposed_risk = _num(plan.get("proposed_risk_1r"), 0.0) or 0.0
    entry = _num(plan.get("entry"))
    stop = _num(plan.get("stop"))
    target = _num(plan.get("target"))
    atr20 = _num(plan.get("atr20"))
    adv = _num(plan.get("adv"))
    gap_p95 = _as_decimal(_num(plan.get("gap_p95")))
    proposed_notional = _num(plan.get("proposed_notional"))

    sizing = entry is not None and stop is not None and entry > 0
    stop_dist = abs(entry - stop) if sizing else None
    if sizing:
        if direction == "long" and stop >= entry:
            reasons.append("stop_must_be_below_entry_for_long")
        if direction == "short" and stop <= entry:
            reasons.append("stop_must_be_above_entry_for_short")
        if atr20 is None or atr20 <= 0:
            reasons.append("missing_atr20")
        elif stop_dist is not None and stop_dist < ATR_MULT * atr20:
            reasons.append("stop_tighter_than_1_5_atr")
        if adv is None or adv <= 0:
            reasons.append("missing_adv")
        if gap_p95 is None:
            reasons.append("missing_gap_p95")

    if direction == "short":
        if "borrow_fee" not in plan or plan.get("borrow_fee") is None:
            reasons.append("short_borrow_fee_required")
        si = _as_percent(_num(plan.get("short_interest_pct")))
        dtc = _num(plan.get("days_to_cover"))
        if (si is not None and si >= SQUEEZE_SI) or (dtc is not None and dtc >= SQUEEZE_DTC):
            reasons.append("short_squeeze_risk")

    fee = _fees(str(plan.get("market") or "US"), plan.get("fees"))
    borrow_rate = _as_decimal(_num(plan.get("borrow_fee"), 0.0)) or 0.0

    shares = 0
    notional = 0.0
    planned_loss = 0.0
    fee_amount = 0.0
    gap_price = None
    gap_loss = 0.0
    days_to_exit = None
    reward = None
    rr_after_fees = None
    new_risk = proposed_risk

    if sizing and stop_dist and stop_dist > 0 and equity and gap_p95 is not None:
        risk_pct = SHORT_RISK if direction == "short" else LONG_RISK
        risk_budget = equity * risk_pct
        if direction == "long":
            gap_price = entry * (1.0 - gap_p95)
            gap_loss_ps = max(entry - gap_price, stop_dist)
        else:
            gap_price = entry * (1.0 + gap_p95)
            gap_loss_ps = max(gap_price - entry, stop_dist)
        shares_risk = math.floor(risk_budget / stop_dist) if stop_dist else 0
        shares_gap = math.floor((equity * GAP_CAP) / gap_loss_ps) if gap_loss_ps else 0
        shares = max(0, min(shares_risk, shares_gap))
        # Shrink until fees keep planned loss inside the risk budget.
        while shares > 0:
            notional = shares * entry
            borrow_monthly = abs(notional) * borrow_rate / 12.0 if direction == "short" else 0.0
            fee_amount = _round_trip_fee(notional, fee, borrow_monthly)
            planned_loss = shares * stop_dist + fee_amount
            if planned_loss <= risk_budget + 1e-9:
                break
            shares -= 1
        notional = shares * entry
        borrow_monthly = abs(notional) * borrow_rate / 12.0 if direction == "short" else 0.0
        fee_amount = _round_trip_fee(notional, fee, borrow_monthly) if shares else 0.0
        planned_loss = shares * stop_dist + fee_amount if shares else 0.0
        gap_loss = shares * gap_loss_ps + fee_amount if shares else 0.0
        new_risk = planned_loss
        if target is not None:
            reward_ps = (target - entry) if direction == "long" else (entry - target)
            reward = shares * reward_ps - fee_amount if shares else 0.0
            if planned_loss > 0:
                rr_after_fees = reward / planned_loss
        participation = SHORT_PARTICIPATION if direction == "short" else LONG_PARTICIPATION
        check_notional = proposed_notional if proposed_notional else notional
        if adv and adv > 0 and check_notional:
            days_to_exit = check_notional / (adv * participation)
            if days_to_exit > 3:
                reasons.append("days_to_exit_over_3")
            elif days_to_exit > 1:
                warnings.append("days_to_exit_over_1")
        if gap_loss > equity * GAP_CAP + 1e-6:
            reasons.append("gap_loss_over_2pct")

    tactical_open = 0.0
    theme_heat = 0.0
    short_book = 0.0
    for pos in positions:
        sleeve = str(pos.get("sleeve") or "tactical").lower()
        if sleeve not in {"tactical", "swing"}:
            continue
        risk = _num(pos.get("risk_1r"), 0.0) or 0.0
        tactical_open += risk
        if theme and str(pos.get("theme") or "") == theme:
            theme_heat += risk
        if str(pos.get("direction") or "").lower() == "short":
            short_book += risk

    tactical_open += new_risk
    if theme:
        theme_heat += new_risk
    if direction == "short":
        short_book += new_risk

    if equity:
        if tactical_open / equity - TACTICAL_CAP > 1e-12:
            reasons.append("tactical_open_risk_over_6pct")
        if theme and theme_heat / equity - THEME_CAP > 1e-12:
            reasons.append("theme_heat_over_2_5pct")
        if short_book / equity - SHORT_BOOK_CAP > 1e-12:
            reasons.append("short_book_over_2pct")

    win_rate = _num(plan.get("win_rate"))
    win_rate_label = "\u672a\u6821\u51c6\u4f30\u8ba1"
    expected_value = None
    breakeven = None
    if rr_after_fees and rr_after_fees > 0:
        breakeven = 1.0 / (1.0 + rr_after_fees)
    if win_rate is not None and planned_loss > 0 and reward is not None:
        expected_value = win_rate * reward - (1.0 - win_rate) * planned_loss
        win_rate_label = "user_supplied_uncalibrated" if plan.get("win_rate_calibrated") else "\u672a\u6821\u51c6\u4f30\u8ba1"

    status = "reject" if reasons else "ok"
    if "theme_heat_over_2_5pct" in reasons or "tactical_open_risk_over_6pct" in reasons:
        action_hint = "\u4e0d\u4ea4\u6613\uff08\u7ec4\u5408\u7ea6\u675f\uff09"
    elif status == "reject" and "target_basis_required" in reasons:
        action_hint = "\u7b49\u5f85\u9a8c\u8bc1"
    elif status == "reject":
        action_hint = "\u4e0d\u4ea4\u6613"
    else:
        action_hint = "\u53ef\u6267\u884c\u5019\u9009"

    theme_cap_amount = (equity or 0.0) * THEME_CAP
    release_needed = max(0.0, theme_heat - theme_cap_amount) if equity else 0.0

    return {
        "status": status,
        "action_hint": action_hint,
        "reasons": reasons,
        "warnings": warnings,
        "ticker": plan.get("ticker"),
        "direction": direction,
        "shares": shares,
        "notional": round(notional, 2),
        "planned_loss": round(planned_loss, 2),
        "planned_loss_pct": round(planned_loss / equity, 6) if equity else None,
        "reward": None if reward is None else round(reward, 2),
        "reward_risk_after_fees": None if rr_after_fees is None else round(rr_after_fees, 4),
        "breakeven_win_rate": None if breakeven is None else round(breakeven, 4),
        "expected_value": None if expected_value is None else round(expected_value, 2),
        "win_rate_label": win_rate_label,
        "gap_price": None if gap_price is None else round(gap_price, 4),
        "gap_loss": round(gap_loss, 2),
        "gap_loss_pct": round(gap_loss / equity, 6) if equity else None,
        "days_to_exit": None if days_to_exit is None else round(days_to_exit, 4),
        "liquidity_warning": "days_to_exit_over_1" in warnings,
        "tactical_open_risk": round(tactical_open, 2),
        "tactical_open_risk_pct": round(tactical_open / equity, 6) if equity else None,
        "theme_heat": round(theme_heat, 2),
        "theme_heat_pct": round(theme_heat / equity, 6) if equity else None,
        "theme_release_needed": round(release_needed, 2),
        "short_book": round(short_book, 2),
        "index_sleeve_note": "index sleeve excluded from theme heat and tactical open risk",
        "fees": {"amount": round(fee_amount, 2), **fee},
        "target_basis": target_basis,
        "account_currency": currency,
    }


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in {"-", "--stdin"}:
        raw = open(sys.argv[1], encoding="utf-8").read()
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"status": "reject", "reasons": ["empty_input"]}, ensure_ascii=False))
        return 2
    plan = json.loads(raw)
    result = evaluate(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
