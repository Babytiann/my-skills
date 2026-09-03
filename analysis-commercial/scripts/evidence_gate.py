#!/usr/bin/env python3
"""Machine-check an evidence ledger before any executable call."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


REQUIRED = [
    "claim_id",
    "ticker",
    "claim",
    "value_unit",
    "source_tier",
    "url",
    "retrieved_at",
    "origin_cluster_id",
    "polarity",
    "evidence_grade",
]
SOURCE_TIERS = {"S0", "S1", "S2", "S3", "S4"}
CANONICAL = {"S0", "S1", "S2"}
POLARITY = {"support", "oppose"}
GRADES = {"verified", "strong", "weak", "rumor"}
FRESHNESS_DAYS = {"S0": 90, "S1": 45, "S2": 45, "S3": 4, "S4": 7}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def check_ledger(rows: list[dict[str, Any]], as_of: date, strict: bool) -> dict[str, Any]:
    gaps: list[str] = []
    if not rows:
        gaps.append("ledger_empty")

    clusters: dict[str, list[str]] = {}
    has_a = False
    has_catalyst = False
    has_exit = False
    a_canonical = 0

    for i, row in enumerate(rows, start=1):
        prefix = f"row_{i}"
        for field in REQUIRED:
            if not _present(row.get(field)):
                gaps.append(f"{prefix}_missing_{field}")
        tier = str(row.get("source_tier") or "")
        if tier and tier not in SOURCE_TIERS:
            gaps.append(f"{prefix}_bad_source_tier")
        if row.get("polarity") not in POLARITY and _present(row.get("polarity")):
            gaps.append(f"{prefix}_bad_polarity")
        if row.get("evidence_grade") not in GRADES and _present(row.get("evidence_grade")):
            gaps.append(f"{prefix}_bad_evidence_grade")
        url = str(row.get("url") or "")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            gaps.append(f"{prefix}_url_not_http")
        retrieved = _parse_date(row.get("retrieved_at"))
        if row.get("retrieved_at") and retrieved is None:
            gaps.append(f"{prefix}_bad_retrieved_at")
        if retrieved and tier in FRESHNESS_DAYS:
            age = (as_of - retrieved).days
            if age > FRESHNESS_DAYS[tier]:
                gaps.append(f"{prefix}_stale_{tier}")
        cluster = str(row.get("origin_cluster_id") or "")
        if cluster:
            clusters.setdefault(cluster, []).append(url)
        level = str(row.get("decision_level") or "B").upper()
        if level == "A":
            has_a = True
            if tier not in CANONICAL:
                gaps.append(f"{prefix}_a_level_needs_s0_s2")
            if not _present(row.get("url")):
                gaps.append(f"{prefix}_a_level_needs_url")
            ts = row.get("timestamps")
            if ts is None or ts == {} or ts == "missing":
                # explicit missing is allowed only if the string is provided
                if ts != "missing" and not (isinstance(ts, dict) and ts.get("explicit_missing")):
                    gaps.append(f"{prefix}_a_level_needs_timestamps_or_explicit_missing")
            if tier in CANONICAL:
                a_canonical += 1
        if _present(row.get("catalyst")):
            has_catalyst = True
        if _present(row.get("exit_condition")):
            has_exit = True

    for cluster, urls in clusters.items():
        if len(urls) > 1:
            gaps.append(f"origin_cluster_{cluster}_deduped_to_one_source")

    independent = len(clusters)
    if has_a and a_canonical < 1:
        gaps.append("a_level_missing_canonical_source")
    if strict:
        if not has_catalyst:
            gaps.append("catalyst_not_registered")
        if not has_exit:
            gaps.append("exit_condition_not_registered")
        if has_a and independent < 1:
            gaps.append("no_independent_origin_after_dedup")

    # Dedup notes are informational when the rest of the row is valid.
    blocking = [g for g in gaps if not g.startswith("origin_cluster_")]
    # Same-cluster duplicates become blocking if they were offered as two sources
    # without a canonical S0/S2 cluster.
    if any(g.startswith("origin_cluster_") for g in gaps) and a_canonical < 1 and has_a:
        blocking.append("duplicate_origins_without_canonical_source")

    status = "FAIL" if blocking else "PASS"
    return {
        "status": status,
        "gaps": gaps,
        "blocking_gaps": blocking,
        "independent_origins": independent,
        "row_count": len(rows),
        "as_of": as_of.isoformat(),
        "action_hint": "\u7b49\u5f85\u9a8c\u8bc1" if status == "FAIL" else "gate_pass",
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            rows.append({"_parse_error": f"line_{line_no}:{exc}"})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a JSONL evidence ledger")
    parser.add_argument("ledger")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--audit", action="store_true", help="schema only; skip catalyst/exit")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now().date()
    path = Path(args.ledger)
    if not path.exists():
        print(json.dumps({"status": "FAIL", "gaps": ["ledger_file_missing"]}, ensure_ascii=False))
        return 2
    rows = load_jsonl(path)
    parse_errors = [r["_parse_error"] for r in rows if "_parse_error" in r]
    rows = [r for r in rows if "_parse_error" not in r]
    result = check_ledger(rows, as_of, strict=not args.audit)
    result["gaps"].extend(parse_errors)
    if parse_errors:
        result["status"] = "FAIL"
        result["blocking_gaps"] = result.get("blocking_gaps", []) + parse_errors
        result["action_hint"] = "\u7b49\u5f85\u9a8c\u8bc1"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
