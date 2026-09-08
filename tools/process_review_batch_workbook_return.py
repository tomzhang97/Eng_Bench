#!/usr/bin/env python3
"""Validate and stage returned generic review-batch XLSX workbooks."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import microtext_review_checklist
import visualdiff_review_checklist
from extract_machine_calibration_workbook import (
    shared_strings,
    sheet_paths,
    worksheet_cells,
)
from verify_human_audit_embedded_evidence import verify_workbook


DECISION_CODES = {"1": "accepted", "2": "edited", "3": "rejected", "4": "needs_full_page"}
TASK_STATUS = {
    "microtext": {
        "accepted": "accepted",
        "edited": "edited",
        "rejected": "rejected",
        "needs_full_page": "needs_full_page",
    },
    "visualdiff": {
        "accepted": "valid",
        "edited": "edit",
        "rejected": "reject_unclear",
        "needs_full_page": "needs_full_page",
    },
}
TASK_EVIDENCE_FIELDS = {"microtext": "crop_path", "visualdiff": "panel_path"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def normalize_decision(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return DECISION_CODES.get(text, "")


def bool_text(value: Any) -> str:
    return str(bool(value)).lower()


def extract_task_decisions(
    *,
    task: str,
    manifest_rows: list[dict[str, Any]],
    review_cells: dict[tuple[int, int], str],
    control_cells: dict[tuple[int, int], str],
) -> tuple[list[dict[str, str]], dict[str, int], list[str], list[str]]:
    checklist: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    binding_issues: list[str] = []
    decision_issues: list[str] = []
    for index, row in enumerate(manifest_rows, start=1):
        review_row = index + 6
        control_row = index + 1
        row_id = identity(row)
        visible_id = str(review_cells.get((review_row, 10), "")).strip()
        control_id = str(control_cells.get((control_row, 2), "")).strip()
        if visible_id != row_id:
            binding_issues.append(f"visible_id_mismatch:{index}:{visible_id}:{row_id}")
        if control_id != row_id:
            binding_issues.append(f"control_id_mismatch:{index}:{control_id}:{row_id}")
        expected_split = str(row.get("replacement_for_split") or "")
        expected_source = str(row.get("replacement_source_unit") or "")
        if str(control_cells.get((control_row, 3), "")) != expected_split:
            binding_issues.append(f"control_split_mismatch:{index}")
        if str(control_cells.get((control_row, 4), "")) != expected_source:
            binding_issues.append(f"control_source_unit_mismatch:{index}")
        if str(control_cells.get((control_row, 9), "")).strip().lower() != "false":
            binding_issues.append(f"control_safe_to_merge_mismatch:{index}")
        if str(control_cells.get((control_row, 10), "")).strip() != "needs_review":
            binding_issues.append(f"control_review_status_mismatch:{index}")
        expected_rewrite = bool_text(row.get("description_rewrite_required"))
        if str(control_cells.get((control_row, 11), "")).strip().lower() != expected_rewrite:
            binding_issues.append(f"control_rewrite_flag_mismatch:{index}")

        decision = normalize_decision(review_cells.get((review_row, 5), ""))
        corrected_one = str(review_cells.get((review_row, 6), "")).strip()
        corrected_two = str(review_cells.get((review_row, 7), "")).strip()
        notes = str(review_cells.get((review_row, 8), "")).strip()
        if not decision:
            counts["blank_or_invalid"] += 1
            decision_issues.append(f"invalid_or_missing_decision:{index}")
            status = ""
        else:
            counts[decision] += 1
            status = TASK_STATUS[task][decision]
        if decision == "edited" and not (corrected_one and corrected_two):
            decision_issues.append(f"edited_requires_both_corrections:{index}")
        if task == "visualdiff" and row.get("description_rewrite_required"):
            if decision != "edited" or not (corrected_one and corrected_two):
                decision_issues.append(f"mandatory_rewrite_incomplete:{index}")

        if task == "microtext":
            checklist.append(
                {
                    "candidate_id": row_id,
                    "proposed_text": str(row.get("proposed_text") or row.get("target_text") or ""),
                    "review_status": status,
                    "corrected_text": corrected_one,
                    "corrected_category": corrected_two,
                    "review_notes": notes,
                }
            )
        else:
            checklist.append(
                {
                    "pair_id": row_id,
                    "current_description": str(row.get("description") or row.get("change_desc_gt") or ""),
                    "human_status": status,
                    "corrected_change_type": corrected_one,
                    "human_description": corrected_two,
                    "human_notes": notes,
                }
            )
    return checklist, dict(sorted(counts.items())), binding_issues, decision_issues


def resolve_manifest_path(batch_dir: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (batch_dir / path).resolve()


def process_batch(
    *, root: Path, batch_dir: Path, returned_dir: Path, output_dir: Path
) -> dict[str, Any]:
    root = root.resolve()
    batch_dir = batch_dir.resolve()
    returned_dir = returned_dir.resolve()
    output_dir = output_dir.resolve()
    payload = json.loads((batch_dir / "workbook_build_payload.json").read_text(encoding="utf-8"))
    with (batch_dir / "NEXT_REVIEW_BATCH_MANIFEST.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        manifest_rows = list(csv.DictReader(handle))
    manifest_by_kind = {str(row.get("kind") or row.get("task") or ""): row for row in manifest_rows}
    reports: list[dict[str, Any]] = []
    fatal_issues: list[str] = []
    decision_issues: list[str] = []

    for task in ("microtext", "visualdiff"):
        task_payload = payload.get(task) or {}
        expected_rows = int(task_payload.get("rows") or 0)
        if expected_rows == 0:
            continue
        batch_manifest = manifest_by_kind.get(task) or {}
        source_value = str(batch_manifest.get("source_jsonl") or "")
        if not source_value:
            fatal_issues.append(f"{task}:missing_source_jsonl")
            continue
        source_path = Path(source_value)
        source_path = source_path if source_path.is_absolute() else root / source_path
        source_rows = read_jsonl(source_path)
        pack_manifest_path = resolve_manifest_path(batch_dir, str(task_payload["manifest"]))
        pack_rows = read_jsonl(pack_manifest_path)
        source_ids = [identity(row) for row in source_rows]
        pack_ids = [identity(row) for row in pack_rows]
        if source_ids != pack_ids:
            fatal_issues.append(f"{task}:source_manifest_identity_order_mismatch")
        if len(pack_rows) != expected_rows:
            fatal_issues.append(f"{task}:manifest_row_count:{len(pack_rows)}:{expected_rows}")

        workbook_path = returned_dir / str(task_payload["workbook"])
        workbook_fatal: list[str] = []
        try:
            with zipfile.ZipFile(workbook_path) as archive:
                bad = archive.testzip()
                if bad:
                    workbook_fatal.append(f"xlsx_crc_failure:{bad}")
                paths = sheet_paths(archive)
                required = {str(task_payload["sheet"]), "机器数据_勿改"}
                for missing in sorted(required - set(paths)):
                    workbook_fatal.append(f"missing_sheet:{missing}")
                if workbook_fatal:
                    review_cells: dict[tuple[int, int], str] = {}
                    control_cells: dict[tuple[int, int], str] = {}
                else:
                    strings = shared_strings(archive)
                    review_cells = worksheet_cells(
                        archive, paths[str(task_payload["sheet"])], strings
                    )
                    control_cells = worksheet_cells(
                        archive, paths["机器数据_勿改"], strings
                    )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            workbook_fatal.append(f"xlsx_read_error:{exc}")
            review_cells = {}
            control_cells = {}

        evidence_rows = []
        evidence_field = TASK_EVIDENCE_FIELDS[task]
        for row in pack_rows:
            evidence_rows.append(
                {
                    "evidence_path": (
                        pack_manifest_path.parent / str(row.get(evidence_field) or "")
                    ).resolve()
                }
            )
        evidence_report, evidence_issues = verify_workbook(
            workbook_path, str(task_payload["sheet"]), evidence_rows
        )
        workbook_fatal.extend(evidence_issues)
        checklist, counts, binding, decisions = extract_task_decisions(
            task=task,
            manifest_rows=pack_rows,
            review_cells=review_cells,
            control_cells=control_cells,
        )
        workbook_fatal.extend(binding)
        fatal_issues.extend(f"{task}:{issue}" for issue in workbook_fatal)
        decision_issues.extend(f"{task}:{issue}" for issue in decisions)

        if task == "microtext":
            updated, apply_stats, apply_errors = microtext_review_checklist.apply_checklist(
                source_rows, checklist
            )
        else:
            updated, apply_stats, apply_errors = visualdiff_review_checklist.apply_checklist(
                source_rows, checklist
            )
            corrections = {
                row["pair_id"]: row["corrected_change_type"]
                for row in checklist
                if row.get("human_status") == "edit"
                and row.get("corrected_change_type")
            }
            for row in updated:
                row_id = identity(row)
                if row_id in corrections:
                    row["change_type"] = corrections[row_id]
        fatal_issues.extend(f"{task}:apply:{issue}" for issue in apply_errors)
        for row in updated:
            row["safe_to_merge_gold"] = False
        output_jsonl = output_dir / f"{task}_reviewed.jsonl"
        checklist_csv = output_dir / f"{task}_extracted_checklist.csv"
        output_written = not workbook_fatal and not apply_errors
        if output_written:
            write_jsonl(output_jsonl, updated)
            write_csv(checklist_csv, checklist)
        reports.append(
            {
                "task": task,
                "workbook": workbook_path.as_posix(),
                "source_jsonl": source_path.resolve().as_posix(),
                "rows": len(pack_rows),
                "decision_counts": counts,
                "binding_issues": workbook_fatal,
                "decision_issues": decisions,
                "apply_stats": dict(sorted(apply_stats.items())),
                "apply_errors": apply_errors,
                "exact_evidence": evidence_report,
                "output_written": output_written,
                "output_jsonl": output_jsonl.as_posix() if output_written else "",
                "checklist_csv": checklist_csv.as_posix() if output_written else "",
            }
        )

    total_rows = sum(report["rows"] for report in reports)
    completed_rows = sum(
        sum(value for key, value in report["decision_counts"].items() if key != "blank_or_invalid")
        for report in reports
    )
    mergeable_rows = sum(
        int(report["apply_stats"].get("mergeable", 0)) for report in reports
    )
    valid = not fatal_issues
    complete = valid and not decision_issues and completed_rows == total_rows and total_rows > 0
    return {
        "goal": "Gold v2.0 Global",
        "batch_dir": batch_dir.as_posix(),
        "returned_dir": returned_dir.as_posix(),
        "output_dir": output_dir.as_posix(),
        "valid_bindings_and_evidence": valid,
        "complete": complete,
        "total_rows": total_rows,
        "completed_rows": completed_rows,
        "remaining_rows": total_rows - completed_rows,
        "mergeable_rows_staged": mergeable_rows,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "fatal_issues": fatal_issues,
        "decision_issues": decision_issues,
        "reports": reports,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--returned-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    batch_dir = args.batch_dir if args.batch_dir.is_absolute() else root / args.batch_dir
    returned_dir = args.returned_dir or batch_dir
    if not returned_dir.is_absolute():
        returned_dir = root / returned_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = process_batch(
            root=root,
            batch_dir=batch_dir,
            returned_dir=returned_dir,
            output_dir=output_dir,
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "goal": "Gold v2.0 Global",
            "valid_bindings_and_evidence": False,
            "complete": False,
            "safe_to_merge_gold": False,
            "active_gold_modified": False,
            "fatal_issues": [str(exc)],
            "decision_issues": [],
        }
    report_path = output_dir / "workbook_return_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    failed = not report.get("valid_bindings_and_evidence") or not report.get("complete")
    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
