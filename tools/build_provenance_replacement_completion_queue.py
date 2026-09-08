#!/usr/bin/env python3
"""Build the exact provenance-replacement backlog not covered by a primary assignment."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged_capacity
from audit_active_gold_provenance import file_sha256
from prepare_incremental_human_audit_round import evidence_materializable, identifier, split_name, task


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def record_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("record_id") or "").strip()


def contract_task(row: dict[str, Any]) -> str:
    return str(
        row.get("replacement_for_task")
        or row.get("replacement_target_task")
        or task(row)
    ).strip()


def contract_split(row: dict[str, Any]) -> str:
    return str(
        row.get("replacement_for_split")
        or row.get("replacement_target_split")
        or row.get("replacement_reserved_split")
        or split_name(row)
    ).strip()


def string_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def specialist_ids(primary_payload: dict[str, Any]) -> set[str]:
    specialist = primary_payload.get("specialist", {})
    if not isinstance(specialist, dict):
        return set()
    return {
        str(row.get("record_id") or "").strip()
        for rows in specialist.values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict) and str(row.get("record_id") or "").strip()
    }


def validated_payload_coverage(
    primary_payload: dict[str, Any],
    *,
    primary_ids: set[str],
    contract_ids: set[str],
) -> tuple[dict[str, set[str]], list[str]]:
    """Validate direct, specialist, and exact-evidence alias coverage declarations."""
    declared = primary_payload.get("provenance_replacement_coverage", {})
    if not isinstance(declared, dict):
        declared = {}

    declared_direct = string_set(declared.get("direct_primary", []))
    declared_specialist = string_set(declared.get("specialist", []))
    declared_alias = string_set(declared.get("exact_evidence_alias", []))
    declared_missing = string_set(declared.get("missing", []))
    present_specialist = specialist_ids(primary_payload)

    alias_rows = primary_payload.get("provenance_replacement_aliases", [])
    if not isinstance(alias_rows, list):
        alias_rows = []
    structurally_valid_aliases = {
        str(row.get("record_id") or "").strip()
        for row in alias_rows
        if isinstance(row, dict)
        and str(row.get("record_id") or "").strip()
        and str(row.get("review_via_record_id") or "").strip() in primary_ids
        and str(row.get("evidence_sha256") or "").strip()
        and str(row.get("coverage_basis") or "").strip() == "exact_rendered_evidence_sha256"
        and not bool(row.get("safe_to_merge_gold"))
    }

    # Replacement contracts can be rebuilt after the payload was issued. Any
    # ordinary row actually present in that payload is directly covered by its
    # eventual review even when an older declaration did not name it.
    direct = primary_ids & contract_ids
    specialist = present_specialist & contract_ids
    aliases = structurally_valid_aliases & contract_ids

    issues: list[str] = []
    if declared_direct - primary_ids:
        issues.append("declared_direct_coverage_missing_primary_review_rows")
    if declared_specialist - present_specialist:
        issues.append("declared_specialist_coverage_missing_specialist_actions")
    if declared_alias - structurally_valid_aliases:
        issues.append("declared_alias_coverage_missing_valid_review_links")
    if (declared_missing & contract_ids) & (direct | specialist | aliases):
        issues.append("declared_missing_coverage_overlaps_covered_ids")
    if (direct & specialist) or (direct & aliases) or (specialist & aliases):
        issues.append("payload_coverage_modes_overlap")

    return {
        "direct_primary": direct,
        "specialist": specialist,
        "exact_evidence_alias": aliases,
    }, issues


def build_queue(
    root: Path,
    *,
    replacement_rows: list[dict[str, Any]],
    primary_payload: dict[str, Any],
    future_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    replacement_ids = [record_id(row) for row in replacement_rows]
    primary_ids = {
        str(row.get("record_id") or "").strip()
        for row in primary_payload.get("rows", [])
        if str(row.get("record_id") or "").strip()
    }
    future_by_id = {record_id(row): row for row in future_rows if record_id(row)}
    contract_by_id = {record_id(row): row for row in replacement_rows if record_id(row)}
    coverage, coverage_issues = validated_payload_coverage(
        primary_payload,
        primary_ids=primary_ids,
        contract_ids=set(contract_by_id),
    )
    covered_ids = set().union(*coverage.values())
    remaining_ids = set(contract_by_id) - covered_ids
    missing_from_future = sorted(remaining_ids - set(future_by_id))

    queue: list[dict[str, Any]] = []
    evidence_blocked: list[str] = []
    for identity in sorted(remaining_ids):
        if identity not in future_by_id:
            continue
        contract = contract_by_id[identity]
        row = {**future_by_id[identity], **contract}
        target_task = contract_task(row)
        target_split = contract_split(row)
        row.update(
            {
                "task": target_task,
                "reserved_split": target_split,
                "replacement_for_task": target_task,
                "replacement_for_split": target_split,
                "replacement_completion_status": "human_review_required",
                "replacement_completion_priority": 1,
                "replacement_completion_reason": "retire_rights_blocked_active_gold_row",
                "review_status": "needs_review",
                "promotion_state": "human_review_required",
                "safe_to_merge_gold": False,
            }
        )
        if not evidence_materializable(root, row):
            evidence_blocked.append(identity)
        queue.append(row)

    queue_ids = [record_id(row) for row in queue]
    issues: list[str] = list(coverage_issues)
    if not all(replacement_ids) or len(replacement_ids) != len(set(replacement_ids)):
        issues.append("replacement_contract_ids_missing_or_duplicate")
    if missing_from_future:
        issues.append("remaining_replacements_missing_from_future_capacity")
    if evidence_blocked:
        issues.append("remaining_replacements_missing_materializable_evidence")
    if len(queue_ids) != len(set(queue_ids)):
        issues.append("completion_queue_duplicate_ids")
    if set(queue_ids) & primary_ids:
        issues.append("completion_queue_overlaps_primary_assignment")
    if any(bool(row.get("safe_to_merge_gold")) for row in queue):
        issues.append("completion_queue_contains_mergeable_rows")
    if len(covered_ids) + len(queue) != len(replacement_rows):
        issues.append("replacement_contract_not_fully_partitioned")

    report = {
        "goal": "Gold v2.0 Global",
        "exact_replacement_candidates": len(replacement_rows),
        "covered_by_current_primary": len(covered_ids),
        "coverage_by_mode": {key: len(value) for key, value in coverage.items()},
        "remaining_review_rows": len(queue),
        "replacement_contract_partition_total": len(covered_ids) + len(queue),
        "missing_from_future_capacity": len(missing_from_future),
        "missing_from_future_examples": missing_from_future[:20],
        "evidence_blocked_rows": len(evidence_blocked),
        "evidence_blocked_examples": evidence_blocked[:20],
        "task_split_counts": {
            f"{key[0]}:{key[1]}": value
            for key, value in sorted(
                Counter((contract_task(row), contract_split(row)) for row in queue).items()
            )
        },
        "replacement_source_units": len(
            {
                str(row.get("replacement_source_unit") or "").strip()
                for row in queue
                if str(row.get("replacement_source_unit") or "").strip()
            }
        ),
        "issues": issues,
        "valid": not issues,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "interpretation": (
            "These rows finish the one-for-one rights-blocker replacement review contract "
            "after the current primary assignment. Human acceptance and atomic migration gates remain required."
        ),
    }
    return queue, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Provenance Replacement Completion Queue",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Exact replacement contract: `{report['exact_replacement_candidates']}` rows",
        f"- Covered by current primary workbook: `{report['covered_by_current_primary']}`",
        f"- Direct primary coverage: `{report['coverage_by_mode']['direct_primary']}`",
        f"- Specialist coverage: `{report['coverage_by_mode']['specialist']}`",
        f"- Exact-evidence alias coverage: `{report['coverage_by_mode']['exact_evidence_alias']}`",
        f"- Remaining human-review rows: `{report['remaining_review_rows']}`",
        f"- Evidence-blocked rows: `{report['evidence_blocked_rows']}`",
        f"- Missing from canonical future capacity: `{report['missing_from_future_capacity']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "",
        "## Remaining Work",
        "",
        "| Task and split | Rows |",
        "| --- | ---: |",
    ]
    for key, count in report["task_split_counts"].items():
        lines.append(f"| `{key}` | {count} |")
    lines.extend(
        [
            "",
            "This queue is review-only. Active rights-blocked rows remain in Gold until accepted replacements pass strict provenance, evidence, split, leakage, deduplication, and atomic migration checks.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--replacement-candidates", type=Path, required=True)
    parser.add_argument("--primary-payload", type=Path, required=True)
    parser.add_argument("--future-capacity", type=Path, required=True)
    parser.add_argument(
        "--additional-capacity",
        type=Path,
        action="append",
        default=[],
        help="Additional review-capacity registry; repeat as needed.",
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    replacements_path = resolve_path(root, args.replacement_candidates)
    primary_path = resolve_path(root, args.primary_payload)
    future_path = resolve_path(root, args.future_capacity)
    additional_capacity_paths = [
        resolve_path(root, value) for value in args.additional_capacity
    ]
    output_path = resolve_path(root, args.output_jsonl)
    report_json_path = resolve_path(root, args.report_json)
    report_md_path = resolve_path(root, args.report_md)
    capacity_paths = [future_path, *additional_capacity_paths]
    capacity_rows = [
        row
        for capacity_path in capacity_paths
        for row in staged_capacity.read_rows(capacity_path)
    ]
    queue, report = build_queue(
        root,
        replacement_rows=staged_capacity.read_rows(replacements_path),
        primary_payload=json.loads(primary_path.read_text(encoding="utf-8")),
        future_rows=capacity_rows,
    )
    write_jsonl_atomic(output_path, queue)
    report.update(
        {
            "replacement_candidates": replacements_path.as_posix(),
            "replacement_candidates_sha256": file_sha256(replacements_path),
            "primary_payload": primary_path.as_posix(),
            "primary_payload_sha256": file_sha256(primary_path),
            "future_capacity": future_path.as_posix(),
            "future_capacity_sha256": file_sha256(future_path),
            "additional_capacity": [
                {
                    "path": path.as_posix(),
                    "sha256": file_sha256(path),
                }
                for path in additional_capacity_paths
            ],
            "output_jsonl": output_path.as_posix(),
            "output_sha256": file_sha256(output_path),
        }
    )
    write_json(report_json_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "covered_by_current_primary": report["covered_by_current_primary"],
                "remaining_review_rows": report["remaining_review_rows"],
                "evidence_blocked_rows": report["evidence_blocked_rows"],
                "valid": report["valid"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
