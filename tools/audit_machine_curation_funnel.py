#!/usr/bin/env python3
"""Audit a machine-curation funnel and quantify avoided human row decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def integer(value: Any) -> int:
    return int(value or 0)


def first_integer(mapping: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in mapping:
            return integer(mapping.get(key))
    return 0


def build_report(
    filter_report: dict[str, Any],
    visual_report: dict[str, Any],
    packet_report: dict[str, Any],
    source_report: dict[str, Any] | None = None,
    certification_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    visual_totals = visual_report.get("totals") or visual_report
    packet_files = packet_report.get("files") or []
    if packet_files:
        checklist_rows = sum(integer(row.get("rows")) for row in packet_files)
        packet_issues = sum(
            integer(len(row.get("issues") or [])) for row in packet_files
        )
    else:
        checklist_rows = first_integer(
            packet_report.get("counts") or {}, "checklist_rows"
        ) or first_integer(
            packet_report, "passing_rows", "checklist_rows", "rows"
        )
        packet_issues = len(packet_report.get("issues") or [])
        if packet_report.get("valid") is False and not packet_issues:
            packet_issues = 1

    raw_rows = first_integer(
        filter_report,
        "input_rows",
        "candidate_rows",
        "source_rows",
    )
    pattern_kept = integer(filter_report.get("selected_rows"))
    pattern_held = integer(filter_report.get("held_rows"))
    visual_input = integer(visual_totals.get("input_rows"))
    visual_kept = first_integer(visual_totals, "kept_rows", "selected_rows")
    visual_held = integer(visual_totals.get("held_rows"))
    corrected = integer(visual_totals.get("corrected_rows"))
    machine_held = pattern_held + visual_held

    certification_totals = (certification_report or {}).get("counts") or (
        certification_report or {}
    )
    certification_input = integer(certification_totals.get("input_rows"))
    machine_calibration_rows = integer(
        certification_totals.get("auto_eligible_pending_calibration")
    )
    certification_human_rows = integer(certification_totals.get("human_required"))
    certification_held_rows = integer(certification_totals.get("reject_or_hold"))

    issues: list[str] = []
    if raw_rows <= 0:
        issues.append("raw_candidate_count_not_positive")
    if pattern_kept + pattern_held != raw_rows:
        issues.append("pattern_filter_count_mismatch")
    if visual_input != pattern_kept:
        issues.append("visual_input_does_not_match_pattern_output")
    if visual_kept + visual_held != visual_input:
        issues.append("visual_decision_count_mismatch")
    if certification_report is None:
        if checklist_rows != visual_kept:
            issues.append("packet_rows_do_not_match_visual_output")
        human_review_rows = visual_kept
    else:
        if certification_input != visual_kept:
            issues.append("certification_input_does_not_match_visual_output")
        if (
            machine_calibration_rows
            + certification_human_rows
            + certification_held_rows
            != certification_input
        ):
            issues.append("certification_disposition_count_mismatch")
        if checklist_rows != certification_human_rows:
            issues.append("packet_rows_do_not_match_certification_human_output")
        human_review_rows = certification_human_rows
    if packet_issues:
        issues.append("packet_has_structural_issues")
    if corrected > visual_kept:
        issues.append("corrected_rows_exceed_kept_rows")

    source_totals = (source_report or {}).get("totals") or {}
    registered_docs = first_integer(
        source_report or {}, "ready_records", "registered_source_docs"
    )
    if not registered_docs:
        registered_docs = integer(source_totals.get("sources"))
    if not registered_docs:
        registered_docs = len((source_report or {}).get("sources") or [])
    active_gold_modified = bool(
        filter_report.get("safe_to_merge_gold")
        or visual_report.get("safe_to_merge_gold")
        or (source_report or {}).get("active_gold_rows_modified")
        or (source_report or {}).get("active_gold_modified")
        or (certification_report or {}).get("active_gold_modified")
    )
    if active_gold_modified:
        issues.append("curation_funnel_must_not_modify_active_gold")

    return {
        "goal": "Gold v2.0 Global",
        "raw_candidate_rows": raw_rows,
        "machine_pattern_held_rows": pattern_held,
        "machine_visual_held_rows": visual_held,
        "machine_certification_pending_calibration_rows": machine_calibration_rows,
        "machine_certification_reject_or_hold_rows": certification_held_rows,
        "machine_row_decisions_removed": machine_held
        + machine_calibration_rows
        + certification_held_rows,
        "human_review_rows": human_review_rows,
        "human_work_reduction_rate": round(
            (
                machine_held
                + machine_calibration_rows
                + certification_held_rows
            )
            / raw_rows,
            6,
        )
        if raw_rows
        else 0.0,
        "machine_corrected_rows": corrected,
        "packet_checklist_rows": checklist_rows,
        "registered_source_docs": registered_docs,
        "active_gold_modified": active_gold_modified,
        "issues": issues,
        "valid": not issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filter-report", type=Path, required=True)
    parser.add_argument("--visual-report", type=Path, required=True)
    parser.add_argument("--packet-report", type=Path, required=True)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--certification-report", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args(argv)

    report = build_report(
        read_json(args.filter_report),
        read_json(args.visual_report),
        read_json(args.packet_report),
        read_json(args.source_report) if args.source_report else None,
        read_json(args.certification_report)
        if args.certification_report
        else None,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
