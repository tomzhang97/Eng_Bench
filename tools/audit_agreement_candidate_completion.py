#!/usr/bin/env python3
"""Join frozen agreement-candidate returns without modifying active Gold."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_release_safe_agreement_contract import (
    file_sha256,
    identity,
    read_jsonl,
    write_json,
    write_jsonl,
)


def decision_mapping(
    groups: Iterable[Iterable[dict[str, Any]]], label: str
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group:
            row_id = identity(row)
            if not row_id:
                raise ValueError(f"{label} contains blank identity")
            if row_id in output:
                raise ValueError(f"{label} contains duplicate identity: {row_id}")
            output[row_id] = row
    return output


def valid_decision(row: dict[str, Any], expected_task: str) -> bool:
    task = str(row.get("task_type") or row.get("task") or "").strip()
    code = str(row.get("decision_code") or "").strip()
    return task == expected_task and code in {"1", "2", "3"}


def build_completion(
    *,
    contract_rows: list[dict[str, Any]],
    screen_decisions: dict[str, dict[str, Any]],
    primary_decisions: dict[str, dict[str, Any]],
    contract_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[str] = []
    contract_ids = [identity(row) for row in contract_rows]
    if any(not row_id for row_id in contract_ids):
        issues.append("blank_contract_identity")
    if len(contract_ids) != len(set(contract_ids)):
        issues.append("duplicate_contract_identity")
    contract_set = set(contract_ids)
    extra_screen_ids = sorted(set(screen_decisions) - contract_set)
    extra_primary_ids = sorted(set(primary_decisions) - contract_set)
    ledger: list[dict[str, Any]] = []
    screen_complete = 0
    primary_complete = 0
    paired_complete = 0
    paired_pass = 0
    paired_issue = 0
    paired_unclear = 0
    channel_counts: Counter[str] = Counter()
    screen_pending_by_channel: Counter[str] = Counter()

    for row in contract_rows:
        row_id = identity(row)
        task = str(row.get("task") or "")
        channel = str(row.get("agreement_channel") or "")
        channel_counts[channel] += 1
        screen = screen_decisions.get(row_id)
        primary = primary_decisions.get(row_id)
        screen_ok = screen is not None and valid_decision(screen, task)
        primary_ok = primary is not None and valid_decision(primary, task)
        if screen is not None and not screen_ok:
            issues.append(f"screen_decision_invalid:{row_id}")
        if primary is not None and not primary_ok:
            issues.append(f"primary_decision_invalid:{row_id}")
        if channel == "completed_independent_screen" and not screen_ok:
            issues.append(f"frozen_completed_screen_missing:{row_id}")
        if screen_ok:
            reviewer = str(screen.get("reviewer_id") or "")
            if not reviewer or reviewer == "primary_reviewer":
                issues.append(f"screen_reviewer_not_independent:{row_id}")
                screen_ok = False
        if primary_ok:
            reviewer = str(primary.get("reviewer_id") or "")
            role = str(primary.get("reviewer_role") or "")
            if reviewer != "primary_reviewer" or role != "primary":
                issues.append(f"primary_reviewer_metadata_invalid:{row_id}")
                primary_ok = False
        if screen_ok and primary_ok:
            if str(screen.get("reviewer_id") or "") == str(primary.get("reviewer_id") or ""):
                issues.append(f"reviewer_identity_collision:{row_id}")
                screen_ok = False
                primary_ok = False

        screen_complete += int(screen_ok)
        primary_complete += int(primary_ok)
        paired_complete += int(screen_ok and primary_ok)
        pair_outcome = "pending"
        if screen_ok and primary_ok:
            screen_code = str(screen.get("decision_code") or "")
            primary_code = str(primary.get("decision_code") or "")
            if screen_code == "1" and primary_code == "1":
                pair_outcome = "pass"
                paired_pass += 1
            elif "3" in {screen_code, primary_code}:
                pair_outcome = "unclear"
                paired_unclear += 1
            else:
                pair_outcome = "issue"
                paired_issue += 1
        if not screen_ok:
            screen_pending_by_channel[channel] += 1
        output = {
            "agreement_contract_index": row.get("agreement_contract_index"),
            "record_id": row_id,
            "task": task,
            "reserved_split": row.get("reserved_split"),
            "agreement_evidence_fingerprint": row.get("agreement_evidence_fingerprint"),
            "agreement_contract_sha256": contract_sha256,
            "candidate_screen_complete": screen_ok,
            "candidate_screen_reviewer_id": str((screen or {}).get("reviewer_id") or ""),
            "candidate_screen_decision_code": str((screen or {}).get("decision_code") or ""),
            "primary_promotion_review_complete": primary_ok,
            "primary_decision_code": str((primary or {}).get("decision_code") or ""),
            "candidate_dual_check_complete": screen_ok and primary_ok,
            "candidate_pair_outcome": pair_outcome,
            "candidate_pair_pass": pair_outcome == "pass",
            "formal_detailed_agreement_complete": False,
            "promotion_state": "candidate_return_checks_pending",
            "safe_to_merge_gold": False,
        }
        ledger.append(output)

    report = {
        "status": "PASS" if not issues else "FAIL",
        "goal": "Gold v2.0 Global",
        "candidate_contract_rows": len(contract_rows),
        "candidate_screen_complete": screen_complete,
        "candidate_screen_pending": len(contract_rows) - screen_complete,
        "primary_promotion_review_complete": primary_complete,
        "primary_promotion_review_pending": len(contract_rows) - primary_complete,
        "candidate_dual_check_complete": paired_complete,
        "candidate_dual_check_pending": len(contract_rows) - paired_complete,
        "candidate_pair_pass": paired_pass,
        "candidate_pair_issue": paired_issue,
        "candidate_pair_unclear": paired_unclear,
        "candidate_pair_nonpass": paired_issue + paired_unclear,
        "formal_detailed_agreement_complete_rows": 0,
        "formal_detailed_agreement_required_rows": len(contract_rows),
        "formal_detailed_reviewer_actions_remaining": len(contract_rows) * 2,
        "channel_counts": dict(sorted(channel_counts.items())),
        "screen_pending_by_channel": dict(sorted(screen_pending_by_channel.items())),
        "screen_decisions_supplied": len(screen_decisions),
        "primary_decisions_supplied": len(primary_decisions),
        "screen_decisions_outside_contract": len(extra_screen_ids),
        "primary_decisions_outside_contract": len(extra_primary_ids),
        "issues": issues,
        "candidate_returns_complete": paired_complete == len(contract_rows) and not issues,
        "ready_for_final_agreement_packet": False,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "interpretation": (
            "This ledger reconciles candidate screening and primary promotion review only. "
            "Only pass/pass candidate pairs may proceed toward promotion; issue or unclear "
            "pairs require replacement or adjudication. These checks never substitute for "
            "the later detailed two-reviewer audit."
        ),
    }
    return ledger, report


def resolve(root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (root / value).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--screen-decisions", type=Path, action="append", default=[], required=True)
    parser.add_argument("--primary-decisions", type=Path, action="append", default=[])
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--strict-complete", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    contract_path = resolve(root, args.contract)
    screen_paths = [resolve(root, value) for value in args.screen_decisions]
    primary_paths = [resolve(root, value) for value in args.primary_decisions]
    output_jsonl = resolve(root, args.output_jsonl)
    report_json = resolve(root, args.report_json)
    report_md = resolve(root, args.report_md)
    for path in (output_jsonl, report_json, report_md):
        if path.exists():
            raise FileExistsError(f"output already exists: {path}")
    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = file_sha256(gold_path)
    contract_sha256 = file_sha256(contract_path)
    ledger, report = build_completion(
        contract_rows=read_jsonl(contract_path),
        screen_decisions=decision_mapping(
            [read_jsonl(path) for path in screen_paths], "screen decisions"
        ),
        primary_decisions=decision_mapping(
            [read_jsonl(path) for path in primary_paths], "primary decisions"
        ),
        contract_sha256=contract_sha256,
    )
    write_jsonl(output_jsonl, ledger)
    report.update(
        {
            "contract": str(contract_path),
            "contract_sha256": contract_sha256,
            "screen_decision_inputs": [str(path) for path in screen_paths],
            "screen_decision_sha256": [file_sha256(path) for path in screen_paths],
            "primary_decision_inputs": [str(path) for path in primary_paths],
            "primary_decision_sha256": [file_sha256(path) for path in primary_paths],
            "completion_ledger": str(output_jsonl),
            "completion_ledger_sha256": file_sha256(output_jsonl),
            "eng_bench_sha256_before": gold_hash_before,
            "eng_bench_sha256_after": file_sha256(gold_path),
        }
    )
    if report["eng_bench_sha256_before"] != report["eng_bench_sha256_after"]:
        report["issues"].append("active_gold_changed_during_return_audit")
        report["status"] = "FAIL"
        report["candidate_returns_complete"] = False
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(
        "# Agreement Candidate Return Controller\n\n"
        f"- Goal: **{report['goal']}**\n"
        f"- Status: **{report['status']}**\n"
        f"- Candidate screens complete: **{report['candidate_screen_complete']}/{report['candidate_contract_rows']}**\n"
        f"- Primary promotion reviews complete: **{report['primary_promotion_review_complete']}/{report['candidate_contract_rows']}**\n"
        f"- Paired candidate checks complete: **{report['candidate_dual_check_complete']}/{report['candidate_contract_rows']}**\n"
        f"- Pass/pass candidate pairs: **{report['candidate_pair_pass']}**\n"
        f"- Paired issues: **{report['candidate_pair_issue']}**\n"
        f"- Paired unclear: **{report['candidate_pair_unclear']}**\n"
        "- Formal detailed agreement complete: **0/185**\n"
        "- Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    complete = report["candidate_returns_complete"] and report["status"] == "PASS"
    return 1 if args.strict_complete and not complete else 0


if __name__ == "__main__":
    raise SystemExit(main())
