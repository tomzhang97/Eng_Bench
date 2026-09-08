#!/usr/bin/env python3
"""Reconcile remaining pre-agreement human actions without modifying Gold."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from process_human_completion_workbook import read_review_workbook


MACHINE_SHEET = "机器数据_勿改"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def identity(row: dict[str, Any]) -> str:
    return str(
        row.get("record_id")
        or row.get("candidate_id")
        or row.get("pair_id")
        or row.get("id")
        or ""
    ).strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assignment_rows(auditor_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    workbooks = sorted(auditor_dir.glob("AUDITOR_*.xlsx"))
    if not workbooks:
        return [], ["no_auditor_workbooks_found"]
    for workbook in workbooks:
        sheets = read_review_workbook(workbook)
        machine_rows = sheets.get(MACHINE_SHEET)
        if machine_rows is None:
            issues.append(f"missing_machine_sheet:{workbook.name}")
            continue
        for row in machine_rows:
            assigned = dict(row)
            assigned["assignment_workbook"] = workbook.name
            rows.append(assigned)
    return rows, issues


def reconcile_actions(
    contract_rows: list[dict[str, Any]],
    completion_rows: list[dict[str, Any]],
    issued_rows: list[dict[str, Any]],
    primary_capacity_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    issues: list[str] = []
    contract_ids = [identity(row) for row in contract_rows]
    completion_ids = [identity(row) for row in completion_rows]
    if any(not row_id for row_id in contract_ids):
        issues.append("blank_contract_identity")
    if len(contract_ids) != len(set(contract_ids)):
        issues.append("duplicate_contract_identity")
    if contract_ids != completion_ids:
        issues.append("completion_ledger_identity_or_order_mismatch")

    contract_by_id = {identity(row): row for row in contract_rows}
    issued_by_id: dict[str, dict[str, Any]] = {}
    for row in issued_rows:
        row_id = identity(row)
        if not row_id:
            issues.append("blank_issued_assignment_identity")
        elif row_id in issued_by_id:
            issues.append(f"duplicate_issued_assignment:{row_id}")
        else:
            issued_by_id[row_id] = row
    primary_by_id: dict[str, dict[str, Any]] = {}
    for row in primary_capacity_rows:
        row_id = identity(row)
        if not row_id:
            continue
        if row_id in primary_by_id:
            issues.append(f"duplicate_primary_capacity_identity:{row_id}")
        else:
            primary_by_id[row_id] = row

    screen_pending: list[dict[str, Any]] = []
    screen_issued: list[dict[str, Any]] = []
    screen_unissued: list[dict[str, Any]] = []
    primary_pending: list[dict[str, Any]] = []
    dual_complete: list[dict[str, Any]] = []
    pair_pass: list[dict[str, Any]] = []
    pair_nonpass: list[dict[str, Any]] = []
    for completion in completion_rows:
        row_id = identity(completion)
        contract = contract_by_id.get(row_id)
        if contract is None:
            continue
        screen_ok = bool(completion.get("candidate_screen_complete"))
        primary_ok = bool(completion.get("primary_promotion_review_complete"))
        if screen_ok and primary_ok:
            dual_complete.append(completion)
            if bool(completion.get("candidate_pair_pass")):
                pair_pass.append(completion)
            else:
                pair_nonpass.append(completion)
        if not screen_ok:
            action = dict(contract)
            action["agreement_action"] = "independent_candidate_screen"
            action["safe_to_merge_gold"] = False
            screen_pending.append(action)
            issued = issued_by_id.get(row_id)
            if issued is not None:
                action["issued_assignment_workbook"] = issued.get(
                    "assignment_workbook", ""
                )
                action["issued_assignment_display_index"] = issued.get(
                    "display_index", ""
                )
                screen_issued.append(action)
            else:
                screen_unissued.append(action)
        if not primary_ok:
            action = dict(contract)
            capacity = primary_by_id.get(row_id)
            action["agreement_action"] = "primary_promotion_review"
            action["current_primary_assignment_present"] = capacity is not None
            action["current_primary_index"] = (capacity or {}).get(
                "primary_index", contract.get("primary_index")
            )
            action["safe_to_merge_gold"] = False
            primary_pending.append(action)
            if capacity is None:
                issues.append(f"primary_pending_not_in_current_capacity:{row_id}")

    # Frozen channels describe issuance, not the number still pending after returns.
    for row in screen_pending:
        channel = str(row.get("agreement_channel") or "")
        if channel == "pending_issued_independent_screen" and identity(row) not in issued_by_id:
            issues.append(f"pending_issued_screen_assignment_missing:{identity(row)}")
        if channel == "completed_independent_screen":
            issues.append(f"frozen_completed_screen_missing:{identity(row)}")

    report = {
        "goal": "Gold v2.0 Global",
        "valid": not issues,
        "active_gold_modified": False,
        "contract_rows": len(contract_rows),
        "candidate_dual_check_complete": len(dual_complete),
        "candidate_dual_check_pending": len(contract_rows) - len(dual_complete),
        "candidate_pair_pass": len(pair_pass),
        "candidate_pair_nonpass": len(pair_nonpass),
        "screen_pending": len(screen_pending),
        "screen_pending_already_issued": len(screen_issued),
        "screen_pending_unissued": len(screen_unissued),
        "primary_pending": len(primary_pending),
        "primary_pending_in_current_capacity": sum(
            bool(row.get("current_primary_assignment_present"))
            for row in primary_pending
        ),
        "auditor_assignment_rows": len(issued_rows),
        "auditor_assignment_unique_ids": len(issued_by_id),
        "issues": issues,
        "safe_to_merge_gold": False,
        "next_actions": [
            f"Collect the {len(screen_issued)} still-pending issued independent screens from the current auditor package.",
            f"Assign the {len(screen_unissued)} unissued independent screens in a later supplemental auditor packet.",
            f"Complete the {len(primary_pending)} pending primary rows in the current primary assignment.",
            f"Do not build the formal two-reviewer agreement packet until all {len(contract_rows)} candidates are promoted into release-ready active Gold.",
        ],
    }
    return report, {
        "screen_pending": screen_pending,
        "screen_issued": screen_issued,
        "screen_unissued": screen_unissued,
        "primary_pending": primary_pending,
        "dual_complete": dual_complete,
        "pair_pass": pair_pass,
        "pair_nonpass": pair_nonpass,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agreement Candidate Remaining Actions",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Candidate dual checks complete: `{report['candidate_dual_check_complete']}/{report['contract_rows']}`",
        f"- Pass/pass candidate pairs: `{report['candidate_pair_pass']}`",
        f"- Completed non-pass pairs: `{report['candidate_pair_nonpass']}`",
        f"- Independent screens pending: `{report['screen_pending']}`",
        f"- Already issued screens: `{report['screen_pending_already_issued']}`",
        f"- Unissued screens: `{report['screen_pending_unissued']}`",
        f"- Primary decisions pending: `{report['primary_pending']}`",
        f"- Pending primary rows in current assignment: `{report['primary_pending_in_current_capacity']}`",
        "- Active Gold modified: `false`",
        "",
        "## Next Actions",
        "",
    ]
    lines.extend(f"- {item}" for item in report["next_actions"])
    lines.extend(["", "## Issues", ""])
    lines.extend(f"- {issue}" for issue in report["issues"])
    if not report["issues"]:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def resolve(root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (root / value).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--completion-ledger", type=Path, required=True)
    parser.add_argument("--auditor-dir", type=Path, required=True)
    parser.add_argument("--primary-capacity", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    contract_path = resolve(root, args.contract)
    completion_path = resolve(root, args.completion_ledger)
    auditor_dir = resolve(root, args.auditor_dir)
    primary_path = resolve(root, args.primary_capacity)
    output_dir = resolve(root, args.output_dir)
    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = file_sha256(gold_path)
    issued, assignment_issues = assignment_rows(auditor_dir)
    report, artifacts = reconcile_actions(
        read_jsonl(contract_path),
        read_jsonl(completion_path),
        issued,
        read_jsonl(primary_path),
    )
    report["issues"] = assignment_issues + report["issues"]
    report["valid"] = not report["issues"]
    report.update(
        {
            "date_label": args.date_label,
            "inputs": {
                "contract": contract_path.as_posix(),
                "contract_sha256": file_sha256(contract_path),
                "completion_ledger": completion_path.as_posix(),
                "completion_ledger_sha256": file_sha256(completion_path),
                "auditor_dir": auditor_dir.as_posix(),
                "primary_capacity": primary_path.as_posix(),
                "primary_capacity_sha256": file_sha256(primary_path),
            },
            "active_gold_sha256_before": gold_hash_before,
            "active_gold_sha256_after": file_sha256(gold_path),
        }
    )
    if report["active_gold_sha256_before"] != report["active_gold_sha256_after"]:
        report["issues"].append("active_gold_changed_during_reconciliation")
        report["valid"] = False
        report["active_gold_modified"] = True

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "screen_pending_all.jsonl", artifacts["screen_pending"])
    write_jsonl(
        output_dir / "screen_pending_already_issued.jsonl", artifacts["screen_issued"]
    )
    write_jsonl(
        output_dir / "screen_pending_unissued.jsonl", artifacts["screen_unissued"]
    )
    write_jsonl(output_dir / "primary_pending.jsonl", artifacts["primary_pending"])
    write_jsonl(output_dir / "dual_check_complete.jsonl", artifacts["dual_complete"])
    write_jsonl(output_dir / "candidate_pair_pass.jsonl", artifacts["pair_pass"])
    write_jsonl(output_dir / "candidate_pair_nonpass.jsonl", artifacts["pair_nonpass"])
    write_json(output_dir / "reconciliation_report.json", report)
    (output_dir / "reconciliation_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and not report["valid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
