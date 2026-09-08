#!/usr/bin/env python3
"""Process a returned calibration XLSX through a read-only Gold preview.

The pipeline binds the human workbook to the frozen remaining checklist,
merges it with exact reused decisions, materializes the current strict-ready
cohort, and delegates to the normal calibration return controller. It never
edits active Gold.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import extract_machine_calibration_workbook as workbook_extractor
import process_machine_calibration_return as return_controller
import preview_reviewed_gold_promotion as promotion_preview


RESPONSE_FIELDS = (
    "reviewer_decision",
    "corrected_text",
    "corrected_category",
    "reviewer_notes",
)
VALID_DECISIONS = {"correct", "incorrect", "unclear"}


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def artifact_path(root: Path, record: dict[str, Any]) -> Path:
    value = str(record.get("path") or "").strip()
    if not value:
        raise ValueError("artifact_path_missing")
    path = resolve(root, value)
    expected = str(record.get("sha256") or "").strip().lower()
    if not path.is_file():
        raise ValueError(f"artifact_missing:{value}")
    if expected != file_sha256(path).lower():
        raise ValueError(f"artifact_sha256_mismatch:{value}")
    return path


def verify_remaining_contract(
    packet_checklist: Path, frozen_remaining: Path
) -> dict[str, Any]:
    packet_fields, packet_rows = read_csv(packet_checklist)
    remaining_fields, remaining_rows = read_csv(frozen_remaining)
    issues: list[str] = []
    packet_ids = [row.get("candidate_id", "") for row in packet_rows]
    remaining_ids = [row.get("candidate_id", "") for row in remaining_rows]
    if len(packet_rows) != len(remaining_rows):
        issues.append("packet_remaining_row_count_mismatch")
    if packet_ids != remaining_ids:
        issues.append("packet_remaining_candidate_order_mismatch")
    if len(packet_ids) != len(set(packet_ids)) or any(not value for value in packet_ids):
        issues.append("packet_candidate_ids_invalid")
    for field in remaining_fields:
        if field not in packet_fields:
            issues.append(f"packet_missing_field:{field}")
    for index, (packet, frozen) in enumerate(
        zip(packet_rows, remaining_rows, strict=False), 1
    ):
        for field in remaining_fields:
            if field in RESPONSE_FIELDS:
                continue
            if packet.get(field, "") != frozen.get(field, ""):
                issues.append(f"packet_immutable_mismatch:{index}:{field}")
    return {
        "valid": not issues,
        "issues": issues,
        "packet_rows": len(packet_rows),
        "frozen_remaining_rows": len(remaining_rows),
        "candidate_ids_sha256": hashlib.sha256(
            "\n".join(packet_ids).encode("utf-8")
        ).hexdigest(),
    }


def merge_completed_checklists(
    *, prefilled_path: Path, completed_remaining_path: Path, output_path: Path
) -> dict[str, Any]:
    fields, prefilled = read_csv(prefilled_path)
    returned_fields, returned = read_csv(completed_remaining_path)
    issues: list[str] = []
    if any(field not in returned_fields for field in RESPONSE_FIELDS):
        issues.append("returned_response_fields_missing")
    by_id = {row.get("candidate_id", ""): row for row in returned}
    returned_ids = [row.get("candidate_id", "") for row in returned]
    if len(by_id) != len(returned) or any(not value for value in returned_ids):
        issues.append("returned_candidate_ids_invalid")
    pending_ids = [
        row.get("candidate_id", "")
        for row in prefilled
        if not str(row.get("reviewer_decision") or "").strip()
    ]
    if returned_ids != pending_ids:
        issues.append("returned_rows_do_not_match_prefilled_pending_rows")

    immutable_fields = [
        field
        for field in returned_fields
        if field not in RESPONSE_FIELDS and field in fields
    ]
    merged = copy.deepcopy(prefilled)
    reused = 0
    newly_completed = 0
    for index, row in enumerate(merged, 1):
        candidate_id = row.get("candidate_id", "")
        returned_row = by_id.get(candidate_id)
        if returned_row is None:
            if str(row.get("reviewer_decision") or "").strip().lower() in VALID_DECISIONS:
                reused += 1
            continue
        for field in immutable_fields:
            if row.get(field, "") != returned_row.get(field, ""):
                issues.append(f"returned_immutable_mismatch:{index}:{field}")
        for field in RESPONSE_FIELDS:
            row[field] = str(returned_row.get(field) or "").strip()
        newly_completed += 1

    decisions = [str(row.get("reviewer_decision") or "").strip().lower() for row in merged]
    invalid = [index for index, decision in enumerate(decisions, 1) if decision not in VALID_DECISIONS]
    if invalid:
        issues.append("combined_checklist_incomplete_or_invalid")
    if issues:
        return {
            "valid": False,
            "issues": issues,
            "rows": len(merged),
            "reused_rows": reused,
            "newly_completed_rows": newly_completed,
        }
    write_csv(output_path, fields, merged)
    return {
        "valid": True,
        "issues": [],
        "rows": len(merged),
        "reused_rows": reused,
        "newly_completed_rows": newly_completed,
        "decision_counts": dict(sorted(Counter(decisions).items())),
        "output": output_path.as_posix(),
        "output_sha256": file_sha256(output_path),
    }


def materialize_current_cohort(
    *,
    root: Path,
    original_report_path: Path,
    strict_ready_path: Path,
    readiness_report_path: Path,
    reuse_report_path: Path,
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    original = read_json(original_report_path)
    strict_rows = read_jsonl(strict_ready_path)
    if not strict_rows:
        raise ValueError("strict_ready_cohort_empty")
    candidate_ids = [str(row.get("candidate_id") or "").strip() for row in strict_rows]
    if len(candidate_ids) != len(set(candidate_ids)) or any(not value for value in candidate_ids):
        raise ValueError("strict_ready_candidate_ids_invalid")

    eligible_path = output_dir / "auto_eligible_pending_calibration.jsonl"
    nonpin_path = output_dir / "auto_eligible_balance_closing_nonpin.jsonl"
    deferred_pin_path = output_dir / "auto_eligible_deferred_pin.jsonl"
    write_jsonl(eligible_path, strict_rows)
    shutil.copy2(eligible_path, nonpin_path)
    write_jsonl(deferred_pin_path, [])

    report = copy.deepcopy(original)
    report["date_label"] = date_label
    report["active_gold_modified"] = False
    report["counts"].update(
        {
            "input_rows": len(strict_rows),
            "auto_eligible_pending_calibration": len(strict_rows),
            "auto_eligible_nonpin": len(strict_rows),
            "auto_eligible_deferred_pin": 0,
            "human_required": 0,
            "reject_or_hold": 0,
            "auto_eligible_categories": dict(
                sorted(Counter(str(row.get("category") or "") for row in strict_rows).items())
            ),
            "splits": {"train": len(strict_rows)},
            "tasks": {"microtext": len(strict_rows)},
            "source_documents_checked": len(
                {str(row.get("doc_id") or "") for row in strict_rows if row.get("doc_id")}
            ),
        }
    )
    report["artifacts"]["auto_eligible"] = {
        "path": display(root, eligible_path),
        "sha256": file_sha256(eligible_path),
    }
    report["artifacts"]["auto_eligible_balance_closing_nonpin"] = {
        "path": display(root, nonpin_path),
        "sha256": file_sha256(nonpin_path),
    }
    report["artifacts"]["auto_eligible_deferred_pin"] = {
        "path": display(root, deferred_pin_path),
        "sha256": file_sha256(deferred_pin_path),
    }
    report["materialization"] = {
        "schema": "eng_bench_current_machine_certification_cohort_v1",
        "original_eligibility_report": display(root, original_report_path),
        "original_eligibility_report_sha256": file_sha256(original_report_path),
        "reuse_report": display(root, reuse_report_path),
        "reuse_report_sha256": file_sha256(reuse_report_path),
        "precalibration_readiness_report": display(root, readiness_report_path),
        "precalibration_readiness_report_sha256": file_sha256(readiness_report_path),
        "strict_ready_source": display(root, strict_ready_path),
        "strict_ready_source_sha256": file_sha256(strict_ready_path),
        "strict_ready_rows": len(strict_rows),
        "safe_to_merge_gold": False,
    }
    report_path = output_dir / "eligibility_report.json"
    write_json(report_path, report)
    report_sha = file_sha256(report_path)
    write_json(
        output_dir / "cohort_lock.json",
        {
            "schema": "eng_bench_machine_certification_cohort_lock_v1",
            "eligibility_report": display(root, report_path),
            "eligibility_report_sha256": report_sha,
            "eligible_jsonl": display(root, eligible_path),
            "eligible_jsonl_sha256": file_sha256(eligible_path),
        },
    )
    return {
        "rows": len(strict_rows),
        "eligibility_report": display(root, report_path),
        "eligibility_report_sha256": report_sha,
        "eligible_jsonl": display(root, eligible_path),
        "eligible_jsonl_sha256": file_sha256(eligible_path),
    }


def run_pipeline(
    *,
    root: Path,
    workbook_path: Path,
    handoff_dir: Path,
    responsibility_report_path: Path,
    output_dir: Path,
    date_label: str,
    minimum_sample_rows: int = 300,
    confidence: float = 0.95,
    minimum_precision_bound: float = 0.99,
) -> dict[str, Any]:
    root = root.resolve()
    workbook_path = workbook_path.resolve()
    handoff_dir = handoff_dir.resolve()
    responsibility_report_path = responsibility_report_path.resolve()
    output_dir = output_dir.resolve()
    quality_root = (root / "derived" / "quality").resolve()
    if output_dir != quality_root and quality_root not in output_dir.parents:
        raise ValueError("output-dir must be under derived/quality")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    active_before = promotion_preview.active_hashes(root)
    issues: list[str] = []

    responsibility = read_json(responsibility_report_path)
    if responsibility.get("status") != "PASS" or responsibility.get("issues"):
        issues.append("machine_responsibility_report_not_passing")
    if responsibility.get("active_gold_modified") is not False:
        issues.append("machine_responsibility_report_modified_gold")
    inputs = responsibility.get("inputs") or {}
    reuse_ref = inputs.get("calibration_reuse") or {}
    readiness_ref = inputs.get("nonpin_precalibration_readiness") or {}
    try:
        reuse_report_path = artifact_path(
            root,
            {"path": reuse_ref.get("path"), "sha256": reuse_ref.get("sha256")},
        )
        readiness_report_path = artifact_path(
            root,
            {"path": readiness_ref.get("path"), "sha256": readiness_ref.get("sha256")},
        )
    except ValueError as error:
        issues.append(str(error))
        reuse_report_path = Path()
        readiness_report_path = Path()

    packet_checklist = handoff_dir / "calibration_checklist.csv"
    returned_snapshot = output_dir / "returned_workbook.xlsx"
    if workbook_path.is_file():
        shutil.copy2(workbook_path, returned_snapshot)
    else:
        issues.append("returned_workbook_missing")

    contract: dict[str, Any] = {"valid": False, "issues": ["not_run"]}
    extraction: dict[str, Any] = {"valid": False, "issues": ["not_run"]}
    merge: dict[str, Any] = {"valid": False, "issues": ["not_run"]}
    materialized: dict[str, Any] = {}
    control: dict[str, Any] = {
        "ready_for_separate_hash_locked_apply": False,
        "state": "not_run",
    }

    reuse: dict[str, Any] = {}
    readiness: dict[str, Any] = {}
    if not issues:
        reuse = read_json(reuse_report_path)
        readiness = read_json(readiness_report_path)
        if reuse.get("active_gold_modified") is not False:
            issues.append("reuse_report_modified_gold")
        if int((reuse.get("counts") or {}).get("conflict_rows") or 0):
            issues.append("reuse_report_has_conflicts")
        if readiness.get("active_gold_modified") is not False:
            issues.append("readiness_report_modified_gold")
        if not readiness.get("precalibration_forecast_valid"):
            issues.append("precalibration_forecast_not_valid")
        if not all(bool(value) for value in (readiness.get("gates") or {}).values()):
            issues.append("precalibration_forecast_gate_failed")

    frozen_remaining = Path()
    prefilled = Path()
    strict_ready = Path()
    original_report_path = Path()
    if not issues:
        try:
            frozen_remaining = artifact_path(
                root, (reuse.get("artifacts") or {}).get("remaining_checklist") or {}
            )
            prefilled = artifact_path(
                root, (reuse.get("artifacts") or {}).get("prefilled_checklist") or {}
            )
            strict_ready = artifact_path(
                root,
                {
                    "path": (readiness.get("artifacts") or {}).get("strict_ready"),
                    "sha256": (readiness.get("artifact_sha256") or {}).get("strict_ready"),
                },
            )
            original_report_path = artifact_path(
                root,
                {
                    "path": (reuse.get("cohort") or {}).get("eligibility_report"),
                    "sha256": (reuse.get("cohort") or {}).get(
                        "eligibility_report_sha256"
                    ),
                },
            )
        except ValueError as error:
            issues.append(str(error))

    if not issues:
        contract = verify_remaining_contract(packet_checklist, frozen_remaining)
        issues.extend(contract["issues"])

    extraction_dir = output_dir / "xlsx_extraction"
    if not issues:
        extraction = workbook_extractor.extract(
            workbook_path=returned_snapshot,
            expected_path=packet_checklist,
            output_path=extraction_dir / "completed_remaining_checklist.csv",
            report_path=extraction_dir / "extraction_report.json",
        )
        issues.extend(extraction["issues"])

    combined_checklist = output_dir / "completed_calibration_checklist_300.csv"
    if not issues:
        merge = merge_completed_checklists(
            prefilled_path=prefilled,
            completed_remaining_path=extraction_dir / "completed_remaining_checklist.csv",
            output_path=combined_checklist,
        )
        issues.extend(merge["issues"])

    if not issues:
        materialized = materialize_current_cohort(
            root=root,
            original_report_path=original_report_path,
            strict_ready_path=strict_ready,
            readiness_report_path=readiness_report_path,
            reuse_report_path=reuse_report_path,
            output_dir=output_dir / "current_strict_ready_cohort",
            date_label=f"{date_label}-current-strict-ready",
        )
        control = return_controller.run_control(
            root=root,
            cohort_dir=output_dir / "current_strict_ready_cohort",
            completed_checklist=combined_checklist,
            output_dir=output_dir / "return_control",
            date_label=date_label,
            minimum_sample_rows=minimum_sample_rows,
            confidence=confidence,
            minimum_precision_bound=minimum_precision_bound,
        )
        issues.extend(control.get("issues") or [])

    active_after = promotion_preview.active_hashes(root)
    active_modified = active_before != active_after
    if active_modified:
        issues.append("active_gold_files_changed_during_pipeline")
    ready = bool(
        not issues
        and control.get("ready_for_separate_hash_locked_apply")
        and not active_modified
    )
    report = {
        "schema": "eng_bench_machine_calibration_workbook_return_pipeline_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_workbook_to_promotion_preview",
        "safe_to_merge_gold": False,
        "active_gold_modified": active_modified,
        "ready_for_separate_hash_locked_apply": ready,
        "issues": list(dict.fromkeys(issues)),
        "inputs": {
            "returned_workbook": display(root, workbook_path),
            "returned_workbook_snapshot": display(root, returned_snapshot),
            "returned_workbook_sha256": (
                file_sha256(returned_snapshot) if returned_snapshot.is_file() else ""
            ),
            "handoff_dir": display(root, handoff_dir),
            "machine_responsibility_report": display(
                root, responsibility_report_path
            ),
            "machine_responsibility_report_sha256": file_sha256(
                responsibility_report_path
            ),
            "reuse_report": display(root, reuse_report_path)
            if reuse_report_path.is_file()
            else "",
            "precalibration_readiness_report": display(root, readiness_report_path)
            if readiness_report_path.is_file()
            else "",
        },
        "remaining_contract": contract,
        "xlsx_extraction": extraction,
        "combined_checklist": merge,
        "materialized_cohort": materialized,
        "return_control": control,
        "active_file_hashes_before": active_before,
        "active_file_hashes_after": active_after,
        "interpretation": (
            "A green result authorizes only a separate hash-locked apply transaction. "
            "Any workbook, lineage, calibration, duplicate, split, leakage, or preview "
            "failure leaves active Gold untouched."
        ),
    }
    write_json(output_dir / "workbook_return_pipeline_report.json", report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--handoff-dir", type=Path, required=True)
    parser.add_argument("--responsibility-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--minimum-sample-rows", type=int, default=300)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--minimum-precision-bound", type=float, default=0.99)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    report = run_pipeline(
        root=root,
        workbook_path=resolve(root, args.workbook),
        handoff_dir=resolve(root, args.handoff_dir),
        responsibility_report_path=resolve(root, args.responsibility_report),
        output_dir=resolve(root, args.output_dir),
        date_label=args.date_label,
        minimum_sample_rows=max(1, args.minimum_sample_rows),
        confidence=args.confidence,
        minimum_precision_bound=args.minimum_precision_bound,
    )
    print(
        json.dumps(
            {
                "goal": report["goal"],
                "ready_for_separate_hash_locked_apply": report[
                    "ready_for_separate_hash_locked_apply"
                ],
                "active_gold_modified": report["active_gold_modified"],
                "verified_embedded_crops": report["xlsx_extraction"].get(
                    "verified_embedded_crops", 0
                ),
                "combined_calibration_rows": report["combined_checklist"].get(
                    "rows", 0
                ),
                "certified_rows_staged": (
                    (report["return_control"].get("finalization") or {})
                    .get("counts", {})
                    .get("certified_output_rows", 0)
                ),
                "issues": report["issues"],
                "report": display(
                    root, resolve(root, args.output_dir) / "workbook_return_pipeline_report.json"
                ),
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if args.require_ready and not report["ready_for_separate_hash_locked_apply"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
