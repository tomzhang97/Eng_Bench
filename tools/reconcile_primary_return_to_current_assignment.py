#!/usr/bin/env python3
"""Reconcile an older validated primary return into the current assignment.

The command is deliberately review-only. It preserves reviewer-entered cells for
identities that still exist, revalidates them against the current payload, and
records obsolete or incomplete rows without touching active Gold.
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
        interpret_decision,
        read_workbook,
        text,
        validate_machine_sheet,
        validate_main_identity,
    )
except ModuleNotFoundError:  # Imported as tools.* in tests.
    from tools.process_primary_intern_catchup_return import (
        interpret_decision,
        read_workbook,
        text,
        validate_machine_sheet,
        validate_main_identity,
    )


STABLE_FIELDS = (
    "task",
    "reserved_split",
    "source_group",
    "category",
    "change_type",
    "proposed_text",
    "change_description",
)


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


def payload_rows_by_task(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        task: [row for row in payload["rows"] if text(row.get("task")) == task]
        for task in ("microtext", "visualdiff")
    }


def returned_cells_by_id(
    workbook_rows: dict[str, dict[int, dict[int, str]]],
    returned_payload: dict[str, Any],
) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {}
    task_rows = payload_rows_by_task(returned_payload)
    for task, sheet_name in (
        ("microtext", "主审_MicroText"),
        ("visualdiff", "主审_VisualDiff"),
    ):
        sheet = workbook_rows.get(sheet_name, {})
        for offset, row in enumerate(task_rows[task], 7):
            result[text(row["record_id"])] = dict(sheet.get(offset, {}))
    return result


def decision_export(decision: dict[str, Any]) -> dict[str, Any]:
    row = {
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
        row.update(
            corrected_text=decision.get("corrected_text", ""),
            corrected_category=decision.get("corrected_category", ""),
        )
    else:
        row.update(
            corrected_change_type=decision.get("corrected_change_type", ""),
            corrected_description=decision.get("corrected_description", ""),
        )
    return row


def reconcile(
    workbook: Path,
    returned_payload: dict[str, Any],
    current_payload: dict[str, Any],
    processor_summary: dict[str, Any],
    date_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    workbook_rows, parse_issues = read_workbook(workbook)
    returned_task_rows = payload_rows_by_task(returned_payload)
    structural: list[dict[str, Any]] = [{"reason": issue} for issue in parse_issues]
    if workbook_rows:
        structural.extend(validate_machine_sheet(workbook_rows.get("机器数据_勿改", {}), returned_payload["rows"]))
        structural.extend(
            validate_main_identity("主审_MicroText", workbook_rows.get("主审_MicroText", {}), returned_task_rows["microtext"])
        )
        structural.extend(
            validate_main_identity("主审_VisualDiff", workbook_rows.get("主审_VisualDiff", {}), returned_task_rows["visualdiff"])
        )
    if structural:
        raise ValueError(f"returned workbook contract failed with {len(structural)} structural issues")

    workbook_sha = sha256_file(workbook)
    expected_workbook_sha = text(processor_summary.get("workbook_sha256"))
    if expected_workbook_sha and workbook_sha != expected_workbook_sha:
        raise ValueError("returned workbook hash does not match the processor summary")

    cells_by_id = returned_cells_by_id(workbook_rows, returned_payload)
    returned_by_id = {text(row["record_id"]): row for row in returned_payload["rows"]}
    current_by_id = {text(row["record_id"]): row for row in current_payload["rows"]}
    if len(returned_by_id) != len(returned_payload["rows"]):
        raise ValueError("returned payload contains duplicate record IDs")
    if len(current_by_id) != len(current_payload["rows"]):
        raise ValueError("current payload contains duplicate record IDs")

    original_ready = 0
    original_rework = 0
    for identity, row in returned_by_id.items():
        decision = interpret_decision(row, cells_by_id[identity])
        if decision["ready"]:
            original_ready += 1
        else:
            original_rework += 1
    expected_totals = processor_summary.get("totals") or {}
    if original_ready != int(expected_totals.get("ready_decisions") or -1):
        raise ValueError("recomputed ready-decision count differs from processor summary")
    if original_rework != int(expected_totals.get("rework_rows") or -1):
        raise ValueError("recomputed rework count differs from processor summary")

    carried: list[dict[str, Any]] = []
    obsolete: list[dict[str, Any]] = []
    metadata_drift: list[dict[str, Any]] = []
    for identity, returned_row in returned_by_id.items():
        current_row = current_by_id.get(identity)
        if current_row is None:
            decision = interpret_decision(returned_row, cells_by_id[identity])
            obsolete.append(
                {
                    **decision_export(decision),
                    "reason": "returned_identity_not_in_current_assignment",
                }
            )
            continue
        drift = [
            field
            for field in STABLE_FIELDS
            if returned_row.get(field) != current_row.get(field)
        ]
        decision = interpret_decision(current_row, cells_by_id[identity])
        exported = decision_export(decision)
        exported.update(
            {
                "current_primary_index": current_row["primary_index"],
                "current_engineering_required": bool(current_row.get("engineering_required")),
                "returned_workbook_sha256": workbook_sha,
                "review_lineage": "validated_primary_return_carried_forward",
            }
        )
        if drift:
            exported["ready"] = False
            exported["blocking_reasons"] = sorted(
                set(exported["blocking_reasons"] + [f"current_metadata_drift:{field}" for field in drift])
            )
            metadata_drift.append(
                {"record_id": identity, "fields": ";".join(drift)}
            )
        carried.append(exported)

    ready = [row for row in carried if row["ready"]]
    rework = [row for row in carried if not row["ready"]]
    specialist_total = int((current_payload.get("counts") or {}).get("specialist_total") or 0)
    current_total = len(current_payload["rows"])
    summary = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "returned_workbook": str(workbook),
        "returned_workbook_sha256": workbook_sha,
        "returned_rows": len(returned_payload["rows"]),
        "returned_ready_under_original_contract": original_ready,
        "returned_rework_under_original_contract": original_rework,
        "current_main_rows": current_total,
        "current_specialist_rows": specialist_total,
        "matched_current_rows": len(carried),
        "ready_carryover_rows": len(ready),
        "carryover_rework_rows": len(rework),
        "obsolete_returned_rows": len(obsolete),
        "metadata_drift_rows": len(metadata_drift),
        "unmatched_current_main_rows": current_total - len(carried),
        "remaining_human_actions": (current_total - len(ready)) + specialist_total,
        "status_counts": dict(Counter(text(row.get("status")) for row in carried)),
        "task_counts": dict(Counter(text(row.get("task")) for row in carried)),
        "interpretation": (
            "Validated reviewer decisions are reusable only for unchanged current identities. "
            "Rework, obsolete, unmatched, and specialist rows remain human-owned. No Gold file changed."
        ),
    }
    return carried, obsolete, metadata_drift, summary


def render_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Primary Return To Current Assignment Reconciliation",
            "",
            f"- Goal: **{summary['goal']}**",
            f"- Returned rows: **{summary['returned_rows']}**",
            f"- Original-contract ready/rework: **{summary['returned_ready_under_original_contract']} / {summary['returned_rework_under_original_contract']}**",
            f"- Current identities matched: **{summary['matched_current_rows']}**",
            f"- Ready carryover: **{summary['ready_carryover_rows']}**",
            f"- Carryover rework: **{summary['carryover_rework_rows']}**",
            f"- Obsolete returned assignments: **{summary['obsolete_returned_rows']}**",
            f"- Unmatched current main rows: **{summary['unmatched_current_main_rows']}**",
            f"- Remaining human actions including specialists: **{summary['remaining_human_actions']}**",
            f"- Gold rows modified: **{summary['gold_rows_modified']}**",
            "",
            summary["interpretation"],
            "",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--returned-payload", required=True)
    parser.add_argument("--current-payload", required=True)
    parser.add_argument("--processor-summary", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--date-label", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    workbook = Path(args.workbook).resolve()
    returned_payload_path = (root / args.returned_payload).resolve()
    current_payload_path = (root / args.current_payload).resolve()
    processor_summary_path = (root / args.processor_summary).resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    returned_payload = json.loads(returned_payload_path.read_text(encoding="utf-8"))
    current_payload = json.loads(current_payload_path.read_text(encoding="utf-8"))
    processor_summary = json.loads(processor_summary_path.read_text(encoding="utf-8"))
    carried, obsolete, metadata_drift, summary = reconcile(
        workbook,
        returned_payload,
        current_payload,
        processor_summary,
        args.date_label,
    )
    ready = [row for row in carried if row["ready"]]
    rework = [row for row in carried if not row["ready"]]

    write_json(output_dir / "carryover_decisions.json", {"summary": summary, "rows": carried})
    write_jsonl(output_dir / "ready_carryover.jsonl", ready)
    write_csv(
        output_dir / "carryover_rework.csv",
        rework,
        [
            "current_primary_index", "record_id", "task", "decision_code", "status",
            "engineering_basis", "blocking_reasons",
        ],
    )
    write_csv(
        output_dir / "obsolete_returned_rows.csv",
        obsolete,
        ["primary_index", "record_id", "task", "decision_code", "status", "ready", "reason"],
    )
    write_csv(output_dir / "metadata_drift.csv", metadata_drift, ["record_id", "fields"])
    write_json(output_dir / "reconciliation_summary.json", summary)
    (output_dir / "reconciliation_summary.md").write_text(
        render_markdown(summary), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
