#!/usr/bin/env python3
"""Validate and stage a returned standalone MicroText review workbook.

The command is deliberately non-promoting. It binds the workbook's immutable
columns to the packet manifest and source queue, applies only valid human
decision columns, and leaves every output unsafe for Gold until the normal
promotion, provenance, split, leakage, and duplicate gates run.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import microtext_review_checklist
from process_human_completion_workbook import (
    read_review_workbook,
    read_sheet,
    shared_strings,
    sheet_targets,
    text,
)
import zipfile


DECISION_FIELDS = {
    "review_status",
    "corrected_text",
    "corrected_category",
    "review_notes",
}
IMMUTABLE_FIELDS = tuple(
    field
    for field in microtext_review_checklist.CHECKLIST_FIELDS
    if field not in DECISION_FIELDS
)
FINAL_STATUSES = microtext_review_checklist.ALLOWED_STATUSES
MERGEABLE_STATUSES = microtext_review_checklist.MERGEABLE_STATUSES

COMPACT_HEADER_ALIASES = {
    "#": "review_index",
    "review_index": "review_index",
    "机器类别": "category",
    "machine_category": "category",
    "机器文字": "proposed_text",
    "machine_text": "proposed_text",
    "review_status": "review_status",
    "corrected_text": "corrected_text",
    "corrected_category": "corrected_category",
}
COMPACT_VISIBLE_FIELDS = {
    "review_index",
    "candidate_id",
    "category",
    "proposed_text",
    *DECISION_FIELDS,
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(microtext_review_checklist.CHECKLIST_FIELDS) + [
        "human_review_return_status",
        "return_staging_reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalized(value: Any) -> str:
    return text(value).replace("\\", "/")


def compact_header_name(value: Any) -> str:
    header = text(value).strip().lower().replace("（", "(").replace("）", ")")
    if header.startswith("candidate_id"):
        return "candidate_id"
    if header.startswith("review_notes"):
        return "review_notes"
    return COMPACT_HEADER_ALIASES.get(header, "")


def compact_rows_from_matrix(
    matrix: list[list[str]], manifest_rows: list[dict[str, Any]]
) -> list[dict[str, str]] | None:
    """Read the compact embedded-evidence workbook and restore hidden bindings."""
    header_index = -1
    mapped_headers: list[str] = []
    for index, values in enumerate(matrix):
        candidate_headers = [compact_header_name(value) for value in values]
        present = {header for header in candidate_headers if header}
        if {"candidate_id", "category", "proposed_text", "review_status"} <= present:
            header_index = index
            mapped_headers = candidate_headers
            break
    if header_index < 0:
        return None

    compact_rows: list[dict[str, str]] = []
    for values in matrix[header_index + 1 :]:
        row = {
            header: text(values[index] if index < len(values) else "")
            for index, header in enumerate(mapped_headers)
            if header
        }
        if any(row.values()):
            compact_rows.append(row)

    expected_rows = microtext_review_checklist.export_rows(manifest_rows)
    hydrated: list[dict[str, str]] = []
    for index, compact in enumerate(compact_rows):
        expected = expected_rows[index] if index < len(expected_rows) else {}
        row = {
            field: text(expected.get(field))
            for field in microtext_review_checklist.CHECKLIST_FIELDS
        }
        for field in COMPACT_VISIBLE_FIELDS:
            if field in compact:
                row[field] = text(compact.get(field))
        hydrated.append(row)
    return hydrated


def read_microtext_review_rows(
    path: Path, manifest_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], str]:
    sheets = read_review_workbook(path)
    standard_rows = sheets.get("Review")
    if standard_rows is None:
        raise ValueError("workbook is missing required Review sheet")
    if standard_rows and {
        "review_index",
        "candidate_id",
        "review_status",
    } <= set(standard_rows[0]):
        return standard_rows, "expanded_checklist"

    with zipfile.ZipFile(path) as archive:
        targets = sheet_targets(archive)
        target = targets.get("Review")
        if target is None:
            raise ValueError("workbook is missing required Review sheet")
        matrix = read_sheet(archive, target, shared_strings(archive))
    compact_rows = compact_rows_from_matrix(matrix, manifest_rows)
    if compact_rows is None:
        raise ValueError(
            "Review sheet does not match the expanded or compact MicroText contract"
        )
    return compact_rows, "compact_embedded_evidence"


def immutable_mismatches(
    manifest_rows: list[dict[str, Any]], workbook_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    # The workbook contains normalized, packet-local paths plus a generated
    # review_index. Recreate that exported contract before comparing cells.
    expected_rows = microtext_review_checklist.export_rows(manifest_rows)
    mismatches: list[dict[str, str]] = []
    for index, (expected, actual) in enumerate(
        zip(expected_rows, workbook_rows, strict=False), start=1
    ):
        for field in IMMUTABLE_FIELDS:
            expected_value = normalized(expected.get(field))
            actual_value = normalized(actual.get(field))
            if expected_value != actual_value:
                mismatches.append(
                    {
                        "row": str(index),
                        "candidate_id": normalized(
                            actual.get("candidate_id") or expected.get("candidate_id")
                        ),
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
    return mismatches


def stage_rows(
    queue_rows: list[dict[str, Any]],
    manifest_rows: list[dict[str, Any]],
    workbook_rows: list[dict[str, str]],
    *,
    date_label: str,
    workbook_path: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    issues: list[str] = []
    queue_ids = [normalized(row.get("candidate_id")) for row in queue_rows]
    manifest_ids = [normalized(row.get("candidate_id")) for row in manifest_rows]
    workbook_ids = [normalized(row.get("candidate_id")) for row in workbook_rows]

    if len(queue_rows) != len(manifest_rows):
        issues.append(
            f"queue/manifest row-count mismatch: {len(queue_rows)} vs {len(manifest_rows)}"
        )
    if len(workbook_rows) != len(manifest_rows):
        issues.append(
            f"workbook/manifest row-count mismatch: {len(workbook_rows)} vs {len(manifest_rows)}"
        )
    if queue_ids != manifest_ids:
        issues.append("queue candidate order/identity differs from manifest")
    if workbook_ids != manifest_ids:
        issues.append("workbook candidate order/identity differs from manifest")
    if any(not candidate_id for candidate_id in workbook_ids):
        issues.append("workbook contains blank candidate_id values")
    if len(set(workbook_ids)) != len(workbook_ids):
        issues.append("workbook contains duplicate candidate_id values")

    mismatches = immutable_mismatches(manifest_rows, workbook_rows)
    if mismatches:
        issues.append(f"workbook has {len(mismatches)} immutable-cell mismatches")

    if issues:
        structural_holds = []
        for row in queue_rows:
            held = dict(row)
            held["safe_to_merge_gold"] = False
            held["promotion_state"] = "held_workbook_contract_error"
            held["return_staging_reason"] = "workbook_contract_error"
            structural_holds.append(held)
        report = {
            "goal": "Gold v2.0 Global",
            "valid": False,
            "complete": False,
            "active_gold_modified": False,
            "date_label": date_label,
            "workbook": workbook_path,
            "counts": {
                "queue_rows": len(queue_rows),
                "manifest_rows": len(manifest_rows),
                "workbook_rows": len(workbook_rows),
                "promotion_candidates": 0,
                "human_holds": len(structural_holds),
                "incomplete_rows": 0,
                "immutable_mismatches": len(mismatches),
            },
            "status_counts": {},
            "issues": issues,
            "apply_errors": [],
            "immutable_mismatch_examples": mismatches[:25],
            "interpretation": (
                "The workbook contract failed closed. No row is eligible for promotion "
                "staging until row identity and immutable evidence fields match."
            ),
        }
        return report, {
            "reviewed": structural_holds,
            "promotion": [],
            "holds": structural_holds,
            "incomplete": [],
        }

    updated, apply_stats, apply_errors = microtext_review_checklist.apply_checklist(
        queue_rows, workbook_rows
    )
    promotion: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reviewed: list[dict[str, Any]] = []
    workbook_by_id = {
        normalized(row.get("candidate_id")): row for row in workbook_rows
    }
    for row in updated:
        staged = dict(row)
        candidate_id = normalized(staged.get("candidate_id"))
        workbook_row = workbook_by_id[candidate_id]
        status = normalized(workbook_row.get("review_status")).lower()
        status_counts[status or "blank"] += 1
        staged["review_index"] = normalized(workbook_row.get("review_index"))
        staged["corrected_category"] = normalized(
            workbook_row.get("corrected_category")
        )
        staged["human_review_return_status"] = status
        staged["safe_to_merge_gold"] = False
        staged["human_review_workbook"] = workbook_path
        staged["human_review_date_label"] = date_label
        if status in FINAL_STATUSES:
            staged["promotion_state"] = "human_reviewed_pending_release_gates"
            reviewed.append(staged)
            if status in MERGEABLE_STATUSES:
                promotion.append(staged)
            else:
                staged["return_staging_reason"] = f"human_{status}"
                holds.append(staged)
        else:
            staged["promotion_state"] = "incomplete_human_review"
            staged["return_staging_reason"] = "blank_or_invalid_decision"
            reviewed.append(staged)
            incomplete.append(staged)

    complete = not apply_errors and not incomplete
    report = {
        "goal": "Gold v2.0 Global",
        "valid": not apply_errors,
        "complete": complete,
        "active_gold_modified": False,
        "date_label": date_label,
        "workbook": workbook_path,
        "counts": {
            "queue_rows": len(queue_rows),
            "manifest_rows": len(manifest_rows),
            "workbook_rows": len(workbook_rows),
            "promotion_candidates": len(promotion),
            "human_holds": len(holds),
            "incomplete_rows": len(incomplete),
            "immutable_mismatches": 0,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "apply_stats": dict(sorted(apply_stats.items())),
        "issues": [],
        "apply_errors": apply_errors,
        "immutable_mismatch_examples": [],
        "interpretation": (
            "Accepted and edited rows are staging candidates only. They remain unsafe "
            "for Gold until promotion, provenance, split, leakage, duplicate, and strict "
            "validators pass."
        ),
    }
    return report, {
        "reviewed": reviewed,
        "promotion": promotion,
        "holds": holds,
        "incomplete": incomplete,
    }


def render_markdown(report: dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    lines = [
        "# MicroText Review Workbook Return",
        "",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        f"- Complete: `{str(report.get('complete', False)).lower()}`",
        f"- Workbook rows: `{counts.get('workbook_rows', 0)}`",
        f"- Promotion staging candidates: `{counts.get('promotion_candidates', 0)}`",
        f"- Human holds: `{counts.get('human_holds', 0)}`",
        f"- Incomplete rows: `{counts.get('incomplete_rows', 0)}`",
        f"- Immutable mismatches: `{counts.get('immutable_mismatches', 0)}`",
        f"- Active Gold modified: `{str(report.get('active_gold_modified', False)).lower()}`",
        "",
        "## Issues",
        "",
    ]
    issues = list(report.get("issues") or []) + list(report.get("apply_errors") or [])
    lines.extend(f"- {issue}" for issue in issues)
    if not issues:
        lines.append("- None.")
    lines.extend(["", report.get("interpretation", ""), ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    workbook_path = args.workbook if args.workbook.is_absolute() else root / args.workbook
    queue_path = args.queue if args.queue.is_absolute() else root / args.queue
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir

    manifest_rows = load_jsonl(manifest_path)
    workbook_rows, workbook_layout = read_microtext_review_rows(
        workbook_path, manifest_rows
    )
    report, artifacts = stage_rows(
        load_jsonl(queue_path),
        manifest_rows,
        workbook_rows,
        date_label=args.date_label,
        workbook_path=workbook_path.as_posix(),
    )
    report["workbook_layout"] = workbook_layout
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "reviewed_rows.jsonl", artifacts["reviewed"])
    write_jsonl(output_dir / "promotion_candidates.jsonl", artifacts["promotion"])
    write_jsonl(output_dir / "human_holds.jsonl", artifacts["holds"])
    write_csv(output_dir / "incomplete_rows.csv", artifacts["incomplete"])
    write_json(output_dir / "return_report.json", report)
    (output_dir / "return_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
