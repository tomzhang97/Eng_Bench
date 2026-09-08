#!/usr/bin/env python3
"""Rebind completed replacement reviews to the current frozen contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_provenance_migration_readiness as readiness


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def alignment_fields(candidate: dict[str, Any]) -> tuple[str, ...]:
    task = str(candidate.get("replacement_for_task") or candidate.get("task") or "").lower()
    specific = (
        readiness.MICROTEXT_REVIEW_ALIGNMENT_FIELDS
        if task == "microtext"
        else readiness.VISUALDIFF_REVIEW_ALIGNMENT_FIELDS
    )
    return readiness.COMMON_REVIEW_ALIGNMENT_FIELDS + specific


def canonical_review_row(
    candidate: dict[str, Any], reviewed: dict[str, Any]
) -> dict[str, Any]:
    """Keep human decisions while replacing stale contract fields."""
    row = {**candidate, **reviewed}
    for field in alignment_fields(candidate):
        if field in candidate:
            row[field] = candidate[field]
        else:
            row.pop(field, None)

    status = readiness.review_status(reviewed)
    normalized = readiness.PROMOTABLE_STATUSES.get(status, status)
    row["review_status"] = normalized
    row["human_review_status"] = normalized
    row["safe_to_merge_gold"] = False
    row["provenance_replacement_safe_to_retire_active_row"] = False
    row["provenance_replacement_review_status"] = (
        "human_keep_pending_atomic_migration_gates"
        if normalized in set(readiness.PROMOTABLE_STATUSES.values())
        else "human_decision_blocks_atomic_migration"
    )
    row["promotion_state"] = "human_reviewed_pending_atomic_migration_gates"
    return row


def reconcile(
    candidate_rows: list[dict[str, Any]],
    reviewed_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_by_id = {
        readiness.candidate_identity(row): row
        for row in candidate_rows
        if readiness.candidate_identity(row)
    }
    source_by_id: dict[str, dict[str, Any]] = {}
    duplicate_source_ids: list[str] = []
    unknown_source_ids: list[str] = []
    missing_source_ids = 0
    for row in reviewed_rows:
        identity = readiness.candidate_identity(row)
        if not identity:
            missing_source_ids += 1
            continue
        if identity not in candidate_by_id:
            unknown_source_ids.append(identity)
            continue
        if identity in source_by_id:
            duplicate_source_ids.append(identity)
            continue
        source_by_id[identity] = row

    output: list[dict[str, Any]] = []
    promotable_ids: set[str] = set()
    rejected_ids: set[str] = set()
    unresolved_ids: set[str] = set()
    alignment_issues: list[dict[str, Any]] = []
    for identity in sorted(source_by_id):
        row = canonical_review_row(candidate_by_id[identity], source_by_id[identity])
        status = readiness.review_status(row)
        if status in readiness.PROMOTABLE_STATUSES:
            promotable_ids.add(identity)
        elif status in readiness.REJECTED_STATUSES:
            rejected_ids.add(identity)
        else:
            unresolved_ids.add(identity)
        issues = readiness.reviewed_candidate_alignment_issues(
            candidate_by_id[identity], row
        )
        if issues:
            alignment_issues.append({"candidate_id": identity, "issues": issues})
        output.append(row)

    contract_ids = set(candidate_by_id)
    outstanding_ids = sorted(contract_ids - promotable_ids)
    issues: list[str] = []
    if len(candidate_by_id) != len(candidate_rows):
        issues.append("replacement_contract_ids_missing_or_duplicate")
    if duplicate_source_ids:
        issues.append("current_contract_review_ids_duplicate")
    if alignment_issues:
        issues.append("reconciled_review_metadata_mismatch")

    report = {
        "goal": "Gold v2.0 Global",
        "contract_rows": len(candidate_rows),
        "source_review_rows": len(reviewed_rows),
        "source_review_unique_current_contract_rows": len(source_by_id),
        "source_review_rows_outside_current_contract": len(unknown_source_ids),
        "source_review_rows_missing_identity": missing_source_ids,
        "duplicate_current_contract_review_rows": len(duplicate_source_ids),
        "reconciled_review_rows": len(output),
        "promotable_review_rows": len(promotable_ids),
        "rejected_review_rows": len(rejected_ids),
        "unresolved_status_rows": len(unresolved_ids),
        "outstanding_review_rows": len(outstanding_ids),
        "unknown_source_examples": sorted(unknown_source_ids)[:20],
        "duplicate_source_examples": sorted(set(duplicate_source_ids))[:20],
        "rejected_examples": sorted(rejected_ids)[:20],
        "unresolved_examples": sorted(unresolved_ids)[:20],
        "outstanding_examples": outstanding_ids[:20],
        "alignment_issues": alignment_issues[:20],
        "issues": issues,
        "valid": not issues,
        "ready_for_atomic_migration": (
            not issues
            and len(promotable_ids) == len(candidate_rows)
            and not rejected_ids
            and not unresolved_ids
        ),
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "interpretation": (
            "Reconciled rows preserve completed human decisions but use the current frozen "
            "replacement contract metadata. They remain non-mergeable until every contract "
            "row is promotable and the atomic migration preview passes all release gates."
        ),
    }
    return output, report


def build_outstanding_rows(
    candidate_rows: list[dict[str, Any]],
    reconciled_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reviewed_by_id = {
        readiness.candidate_identity(row): row
        for row in reconciled_rows
        if readiness.candidate_identity(row)
    }
    promotable_ids = {
        identity
        for identity, row in reviewed_by_id.items()
        if readiness.review_status(row) in readiness.PROMOTABLE_STATUSES
    }
    output: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        identity = readiness.candidate_identity(candidate)
        if identity in promotable_ids:
            continue
        reviewed = reviewed_by_id.get(identity)
        rejected = bool(
            reviewed
            and readiness.review_status(reviewed) in readiness.REJECTED_STATUSES
        )
        row = dict(candidate)
        row.update(
            {
                "replacement_completion_status": (
                    "rejected_candidate_requires_replacement"
                    if rejected
                    else "human_review_required"
                ),
                "replacement_completion_priority": 1,
                "safe_to_merge_gold": False,
            }
        )
        output.append(row)
    return output


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Provenance Replacement Review Reconciliation",
            "",
            f"- Goal: **{report['goal']}**",
            f"- Current frozen contract: `{report['contract_rows']}` rows",
            f"- Prior returned review rows: `{report['source_review_rows']}`",
            f"- Reconciled current-contract rows: `{report['reconciled_review_rows']}`",
            f"- Accepted/edited rows: `{report['promotable_review_rows']}`",
            f"- Rejected rows: `{report['rejected_review_rows']}`",
            f"- Unresolved decision rows: `{report['unresolved_status_rows']}`",
            f"- Outstanding contract rows: `{report['outstanding_review_rows']}`",
            f"- Prior rows outside this contract: `{report['source_review_rows_outside_current_contract']}`",
            f"- Valid reconciliation: `{str(report['valid']).lower()}`",
            f"- Ready for atomic migration: `{str(report['ready_for_atomic_migration']).lower()}`",
            "",
            report["interpretation"],
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--reviewed", action="append", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--outstanding-jsonl")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    candidates_path = resolve(root, args.candidates)
    reviewed_paths = [resolve(root, value) for value in args.reviewed]
    output_path = resolve(root, args.output_jsonl)
    report_path = resolve(root, args.report_json)
    report_md_path = resolve(root, args.report_md)

    candidate_rows = read_jsonl(candidates_path)
    reviewed_rows = [row for path in reviewed_paths for row in read_jsonl(path)]
    output, report = reconcile(candidate_rows, reviewed_rows)
    report["inputs"] = {
        "candidates": candidates_path.as_posix(),
        "candidates_sha256": file_sha256(candidates_path),
        "reviewed": [path.as_posix() for path in reviewed_paths],
        "reviewed_sha256": [file_sha256(path) for path in reviewed_paths],
    }
    write_jsonl_atomic(output_path, output)
    report["output_jsonl"] = output_path.as_posix()
    report["output_jsonl_sha256"] = file_sha256(output_path)
    if args.outstanding_jsonl:
        outstanding_path = resolve(root, args.outstanding_jsonl)
        outstanding_rows = build_outstanding_rows(candidate_rows, output)
        write_jsonl_atomic(outstanding_path, outstanding_rows)
        report["outstanding_jsonl"] = outstanding_path.as_posix()
        report["outstanding_jsonl_sha256"] = file_sha256(outstanding_path)
        report["outstanding_rows_by_origin"] = dict(
            sorted(
                Counter(
                    str(row.get("replacement_origin_phase") or "unknown")
                    for row in outstanding_rows
                ).items()
            )
        )
        report["outstanding_rows_by_task_split"] = dict(
            sorted(
                Counter(
                    f"{row.get('replacement_for_task') or row.get('task')}:{row.get('replacement_for_split') or row.get('reserved_split')}"
                    for row in outstanding_rows
                ).items()
            )
        )
    write_json(report_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "reconciled_review_rows": report["reconciled_review_rows"],
                "promotable_review_rows": report["promotable_review_rows"],
                "outstanding_review_rows": report["outstanding_review_rows"],
                "valid": report["valid"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
