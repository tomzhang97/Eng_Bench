#!/usr/bin/env python3
"""Plan auditable repairs for a primary-review workbook without editing it.

The tool tolerates only blanked immutable engineering-reason cells. It also
normalizes two unambiguous VisualDiff feedback values that were entered in the
wrong column: ``layout_only_no_change`` becomes decision 3 and ``unclear``
becomes decision 4. All other invalid or incomplete rows remain human rework.
No active Gold file is read for writing or modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from process_primary_intern_catchup_return import (
        cell,
        interpret_decision,
        normalized,
        read_workbook,
        text,
        validate_machine_sheet,
        validate_main_identity,
    )
except ModuleNotFoundError:
    from tools.process_primary_intern_catchup_return import (
        cell,
        interpret_decision,
        normalized,
        read_workbook,
        text,
        validate_machine_sheet,
        validate_main_identity,
    )


VISUAL_FEEDBACK_NORMALIZATION = {
    "layout_only_no_change": ("3", "rejected_no_change"),
    "unclear": ("4", "needs_context"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def normalize_visual_cells(cells: dict[int, str]) -> tuple[dict[int, str], dict[str, str] | None]:
    repaired = dict(cells)
    if cell(cells, 4) != "2":
        return repaired, None
    entered_type = normalized(cell(cells, 5))
    target = VISUAL_FEEDBACK_NORMALIZATION.get(entered_type)
    if target is None:
        return repaired, None
    target_code, target_status = target
    action = {
        "source_decision_code": "2",
        "source_corrected_change_type": cell(cells, 5),
        "source_corrected_description": cell(cells, 6),
        "target_decision_code": target_code,
        "target_status": target_status,
        "target_corrected_change_type": "",
        "target_corrected_description": "",
    }
    repaired[4] = target_code
    repaired[5] = ""
    repaired[6] = ""
    return repaired, action


def decision_export(decision: dict[str, Any]) -> dict[str, Any]:
    result = {
        "record_id": decision["record_id"],
        "primary_index": decision["primary_index"],
        "task": decision["task"],
        "decision_code": decision["decision_code"],
        "engineering_basis": decision["engineering_basis"],
        "ready": decision["ready"],
        "status": decision["status"],
        "decision_class": decision["decision_class"],
        "blocking_reasons": decision["blocking_reasons"],
    }
    if decision["task"] == "microtext":
        result.update(
            corrected_text=decision.get("corrected_text", ""),
            corrected_category=decision.get("corrected_category", ""),
        )
    else:
        result.update(
            corrected_change_type=decision.get("corrected_change_type", ""),
            corrected_description=decision.get("corrected_description", ""),
        )
    return result


def analyze(
    workbook: Path,
    payload_path: Path,
    specialist_report_path: Path,
    date_label: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload_rows = payload.get("rows") or []
    workbook_rows, parse_issues = read_workbook(workbook)
    if parse_issues:
        raise ValueError(f"workbook parse issues: {parse_issues}")

    micro_rows = [row for row in payload_rows if row.get("task") == "microtext"]
    visual_rows = [row for row in payload_rows if row.get("task") == "visualdiff"]
    structural = validate_machine_sheet(workbook_rows.get("机器数据_勿改", {}), payload_rows)
    structural.extend(
        validate_main_identity("主审_MicroText", workbook_rows.get("主审_MicroText", {}), micro_rows)
    )
    structural.extend(
        validate_main_identity("主审_VisualDiff", workbook_rows.get("主审_VisualDiff", {}), visual_rows)
    )
    metadata_repairs: list[dict[str, Any]] = []
    fatal: list[dict[str, Any]] = []
    for issue in structural:
        tolerated = (
            issue.get("sheet") == "主审_VisualDiff"
            and issue.get("field") == "engineering_reason"
            and issue.get("reason") == "immutable_value_changed"
            and text(issue.get("actual")) == ""
            and text(issue.get("expected")) != ""
        )
        if tolerated:
            metadata_repairs.append(
                {
                    "sheet": issue["sheet"],
                    "row": int(issue["row"]),
                    "cell": f"H{issue['row']}",
                    "record_id": issue.get("record_id", ""),
                    "field": issue["field"],
                    "source_value": issue.get("actual", ""),
                    "target_value": issue.get("expected", ""),
                    "repair_reason": "restore_blanked_frozen_engineering_reason",
                }
            )
        else:
            fatal.append(issue)
    if fatal:
        raise ValueError(f"unsupported structural changes: {fatal[:10]}")

    decisions: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []
    for task, rows, sheet_name in (
        ("microtext", micro_rows, "主审_MicroText"),
        ("visualdiff", visual_rows, "主审_VisualDiff"),
    ):
        sheet = workbook_rows.get(sheet_name, {})
        for offset, payload_row in enumerate(rows, 7):
            cells = dict(sheet.get(offset, {}))
            action = None
            if task == "visualdiff":
                cells, action = normalize_visual_cells(cells)
            decision = interpret_decision(payload_row, cells)
            decisions.append(decision_export(decision))
            if action is not None:
                normalizations.append(
                    {
                        **action,
                        "sheet": sheet_name,
                        "row": offset,
                        "record_id": payload_row["record_id"],
                        "engineering_basis": cell(cells, 8),
                    }
                )

    specialist = json.loads(specialist_report_path.read_text(encoding="utf-8"))
    workbook_sha = sha256_file(workbook)
    payload_sha = sha256_file(payload_path)
    specialist_hash_ok = (
        text(specialist.get("workbook_sha256")) == workbook_sha
        and text(specialist.get("payload_sha256")) == payload_sha
    )
    if not specialist.get("complete") or not specialist_hash_ok:
        raise ValueError("specialist report is incomplete or not bound to this workbook/payload")
    specialist_total = int((specialist.get("totals") or {}).get("expected") or 0)
    specialist_ready = int((specialist.get("totals") or {}).get("ready") or 0)

    ready = [row for row in decisions if row["ready"]]
    rework = [row for row in decisions if not row["ready"]]
    visual_holds = []
    payload_by_id = {text(row.get("record_id")): row for row in payload_rows}
    normalized_ids = {row["record_id"] for row in normalizations}
    for decision in decisions:
        if decision["task"] != "visualdiff" or decision["decision_class"] == "keep":
            continue
        source = dict(payload_by_id[decision["record_id"]])
        source.update(
            {
                "primary_reviewer_decision_code": decision["decision_code"],
                "primary_reviewer_status": decision["status"],
                "engineering_review_basis": decision["engineering_basis"],
                "feedback_normalized": decision["record_id"] in normalized_ids,
                "hold_reason": (
                    "primary_reviewer_no_engineering_change_or_alignment_only"
                    if decision["decision_class"] == "reject"
                    else "primary_reviewer_cross_location_or_insufficient_context"
                ),
                "promotion_state": "human_reviewed_machine_repair_or_hold",
                "safe_to_merge_gold": False,
            }
        )
        visual_holds.append(source)

    summary = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "input_workbook": str(workbook),
        "input_workbook_sha256": workbook_sha,
        "payload": str(payload_path),
        "payload_sha256": payload_sha,
        "specialist_report": str(specialist_report_path),
        "main_rows": len(decisions),
        "main_ready_after_normalization": len(ready),
        "main_rework_after_normalization": len(rework),
        "specialist_ready": specialist_ready,
        "specialist_total": specialist_total,
        "remaining_human_actions": len(rework) + specialist_total - specialist_ready,
        "metadata_repairs": len(metadata_repairs),
        "visual_feedback_normalizations": len(normalizations),
        "visual_machine_repair_or_holds": len(visual_holds),
        "status_counts": dict(Counter(text(row.get("status")) for row in decisions)),
        "rework_reason_counts": dict(
            Counter(reason for row in rework for reason in row["blocking_reasons"])
        ),
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
    }
    plan = {
        "summary": summary,
        "metadata_repairs": metadata_repairs,
        "visual_feedback_normalizations": normalizations,
        "rework_rows": rework,
    }
    reconciliation = {
        "summary": {
            "matched_current_rows": len(decisions),
            "ready_carryover_rows": len(ready),
            "carryover_rework_rows": len(rework),
            "specialist_ready_rows": specialist_ready,
            "specialist_total": specialist_total,
            "remaining_human_actions": summary["remaining_human_actions"],
            "safe_to_merge_gold": False,
        },
        "rows": decisions,
    }
    return plan, reconciliation, visual_holds, rework


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--specialist-report", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--date-label", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    workbook = Path(args.workbook).resolve()
    payload = (root / args.payload).resolve()
    specialist_report = (root / args.specialist_report).resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    plan, reconciliation, visual_holds, rework = analyze(
        workbook, payload, specialist_report, args.date_label
    )
    write_json(output_dir / "normalization_plan.json", plan)
    write_json(output_dir / "carryover_decisions.json", reconciliation)
    write_jsonl(output_dir / "visualdiff_machine_repair_or_holds.jsonl", visual_holds)
    write_csv(
        output_dir / "remaining_human_rework.csv",
        rework,
        [
            "primary_index",
            "record_id",
            "task",
            "decision_code",
            "status",
            "engineering_basis",
            "blocking_reasons",
        ],
    )
    print(json.dumps(plan["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
