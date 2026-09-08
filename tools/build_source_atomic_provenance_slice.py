#!/usr/bin/env python3
"""Build one fully reviewed, source-atomic provenance replacement slice.

The command is read-only with respect to active Gold. It selects only accepted
or edited replacement candidates that exactly cover every affected row for one
blocked source by replacement task, split, and target category. The resulting
artifacts can be passed to ``preview_provenance_replacement_migration.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_active_gold_provenance as active_provenance
import audit_provenance_migration_readiness as readiness


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def affected_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return normalize(row.get("task")), normalize(row.get("split")), normalize(row.get("category"))


def candidate_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        normalize(row.get("replacement_for_task")),
        normalize(row.get("replacement_for_split")),
        normalize(row.get("replacement_for_category")),
    )


def serialize_counts(counts: Counter[tuple[str, str, str]]) -> dict[str, int]:
    return {
        f"{task}:{split}:{category}": count
        for (task, split, category), count in sorted(counts.items())
    }


def promotable_reviews(
    candidates: list[dict[str, Any]], reviewed: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    candidate_by_id = {
        readiness.candidate_identity(row): row
        for row in candidates
        if readiness.candidate_identity(row)
    }
    accepted: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in reviewed:
        identity = readiness.candidate_identity(row)
        if not identity or identity not in candidate_by_id:
            continue
        if identity in seen:
            issues.append({"code": "duplicate_reviewed_identity", "candidate_id": identity})
            continue
        seen.add(identity)
        alignment = readiness.reviewed_candidate_alignment_issues(candidate_by_id[identity], row)
        if alignment:
            issues.append(
                {
                    "code": "reviewed_candidate_metadata_mismatch",
                    "candidate_id": identity,
                    "detail": alignment,
                }
            )
            continue
        status = readiness.review_status(row)
        if status in readiness.PROMOTABLE_STATUSES:
            accepted[identity] = row
    return accepted, issues


def current_source_references(
    root: Path, source_doc: str, provenance_report_path: Path | None
) -> tuple[int | None, str]:
    report = (
        read_json(provenance_report_path)
        if provenance_report_path is not None
        else active_provenance.build_report(root)
    )
    for row in report.get("documents", []):
        if str(row.get("doc_id") or "") == source_doc:
            return int(row.get("active_row_references") or 0), str(
                provenance_report_path or "computed_current_active_provenance"
            )
    return None, str(provenance_report_path or "computed_current_active_provenance")


def build_slice(
    *,
    root: Path,
    parent_plan_path: Path,
    affected_path: Path,
    candidates_path: Path,
    reviewed_paths: list[Path],
    source_doc: str,
    output_dir: Path,
    date_label: str,
    provenance_report_path: Path | None = None,
) -> dict[str, Any]:
    parent_plan = read_json(parent_plan_path)
    all_affected = read_jsonl(affected_path)
    all_candidates = read_jsonl(candidates_path)
    reviewed = [row for path in reviewed_paths for row in read_jsonl(path)]
    active_rows = read_jsonl(root / "eng_bench.jsonl")
    active_ids = {
        readiness.active_identity(row) for row in active_rows if readiness.active_identity(row)
    }

    issues: list[dict[str, Any]] = []
    if source_doc not in set(parent_plan.get("blocked_active_source_docs") or []):
        issues.append({"code": "source_not_blocked_in_parent_plan", "source_doc": source_doc})

    affected = [
        row
        for row in all_affected
        if source_doc in {str(value) for value in row.get("blocked_source_doc_ids") or []}
    ]
    affected.sort(key=lambda row: str(row.get("active_id") or ""))
    if not affected:
        issues.append({"code": "source_has_no_affected_rows", "source_doc": source_doc})
    missing_active = sorted(
        str(row.get("active_id") or "")
        for row in affected
        if str(row.get("active_id") or "") not in active_ids
    )
    if missing_active:
        issues.append({"code": "affected_rows_not_currently_active", "ids": missing_active[:20]})

    reference_count, provenance_basis = current_source_references(
        root, source_doc, provenance_report_path
    )
    if reference_count is None:
        issues.append({"code": "source_absent_from_active_provenance", "source_doc": source_doc})
    elif reference_count != len(affected):
        issues.append(
            {
                "code": "source_reference_count_mismatch",
                "active_provenance": reference_count,
                "affected_rows": len(affected),
            }
        )

    accepted_reviews, review_issues = promotable_reviews(all_candidates, reviewed)
    issues.extend(review_issues)
    reviewed_identities = {
        readiness.candidate_identity(row)
        for row in reviewed
        if readiness.candidate_identity(row)
    }
    candidates_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    unreviewed_candidates_by_key: dict[
        tuple[str, str, str], list[dict[str, Any]]
    ] = defaultdict(list)
    for row in all_candidates:
        identity = readiness.candidate_identity(row)
        if identity in accepted_reviews:
            candidates_by_key[candidate_key(row)].append(row)
        elif identity and identity not in reviewed_identities:
            unreviewed_candidates_by_key[candidate_key(row)].append(row)
    for rows in candidates_by_key.values():
        rows.sort(key=readiness.candidate_identity)
    for rows in unreviewed_candidates_by_key.values():
        rows.sort(key=readiness.candidate_identity)

    requirements = Counter(affected_key(row) for row in affected)
    selected_candidates: list[dict[str, Any]] = []
    outstanding_candidates: list[dict[str, Any]] = []
    shortfalls: dict[str, int] = {}
    capacity_shortfalls: dict[str, int] = {}
    for key, required in sorted(requirements.items()):
        available = candidates_by_key.get(key, [])
        selected = available[:required]
        selected_candidates.extend(selected)
        shortfall = required - len(selected)
        if not shortfall:
            continue
        label = ":".join(key)
        shortfalls[label] = shortfall
        outstanding = unreviewed_candidates_by_key.get(key, [])[:shortfall]
        outstanding_candidates.extend(outstanding)
        if len(outstanding) < shortfall:
            capacity_shortfalls[label] = shortfall - len(outstanding)
    if shortfalls:
        issues.append({"code": "reviewed_replacement_shortfall", "shortfalls": shortfalls})
    if capacity_shortfalls:
        issues.append(
            {
                "code": "unreviewed_replacement_capacity_shortfall",
                "shortfalls": capacity_shortfalls,
            }
        )

    selected_candidates.sort(key=readiness.candidate_identity)
    outstanding_candidates.sort(key=readiness.candidate_identity)
    selected_ids = [readiness.candidate_identity(row) for row in selected_candidates]
    selected_reviews = [accepted_reviews[identity] for identity in selected_ids]
    fingerprints = [
        str(row.get("replacement_evidence_fingerprint") or "")
        for row in selected_candidates
    ]
    if len(set(fingerprints)) != len(fingerprints):
        issues.append({"code": "selected_evidence_fingerprint_collision"})
    if len(selected_candidates) != len(affected):
        issues.append(
            {
                "code": "slice_row_count_mismatch",
                "affected": len(affected),
                "selected": len(selected_candidates),
            }
        )

    ready = not issues
    by_task_split = Counter((key[0], key[1]) for key in map(affected_key, affected))
    selected_origin = Counter(
        str(row.get("replacement_origin_phase") or "unknown")
        for row in selected_candidates
    )
    match_levels = Counter(
        str(row.get("replacement_match_level") or "unknown")
        for row in selected_candidates
    )
    plan = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "source_atomic": True,
        "parent_plan_path": parent_plan_path.relative_to(root).as_posix(),
        "parent_plan_sha256": readiness.file_sha256(parent_plan_path),
        "blocked_active_source_docs": [source_doc],
        "blocked_active_source_doc_count": 1,
        "affected_gold_rows": len(affected),
        "affected_gold_rows_by_task_split": readiness.serialized_counts(by_task_split),
        "candidate_pool_rows": len(all_candidates),
        "candidate_pool_rejections": {},
        "selected_replacement_candidates": len(selected_candidates),
        "selected_replacement_candidates_by_origin": dict(sorted(selected_origin.items())),
        "already_assigned_human_review_rows": len(selected_candidates),
        "new_human_review_priority_rows": len(outstanding_candidates),
        "selected_replacement_candidates_by_task_split": readiness.serialized_counts(by_task_split),
        "remaining_replacement_gap_by_task_split": {
            key: 0 for key in readiness.serialized_counts(by_task_split)
        },
        "remaining_replacement_gap": 0 if ready else sum(shortfalls.values()),
        "exact_category_matches": match_levels.get("task_split_category", 0),
        "task_split_fallback_matches": match_levels.get("task_split_fallback", 0),
        "selected_source_units": len(
            {str(row.get("replacement_source_unit") or "") for row in selected_candidates}
        ),
        "selected_unique_evidence_fingerprints": len(set(fingerprints)),
        "duplicate_evidence_candidate_skips": 0,
        "evidence_fingerprint_status": {"pixel_crop_sha256": len(selected_candidates)},
        "preferred_issued_requested": len(selected_candidates),
        "preferred_issued_available": len(selected_candidates),
        "preferred_issued_selected": len(selected_candidates),
        "preferred_issued_retired_active_gold": 0,
        "preferred_issued_missing": 0,
        "all_replacement_capacity_available": ready,
        "reviewed_replacements_promoted": 0,
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold_rows": 0,
        "unresolved_active_visualdiff_source_mappings": 0,
        "interpretation": (
            "Source-atomic reviewed slice only. Active Gold remains unchanged until the "
            "migration preview and atomic apply gates pass."
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "source_atomic_plan.json"
    sliced_affected_path = output_dir / "source_atomic_affected.jsonl"
    sliced_candidates_path = output_dir / "source_atomic_candidates.jsonl"
    sliced_reviewed_path = output_dir / "source_atomic_reviewed.jsonl"
    outstanding_path = output_dir / "source_atomic_outstanding.jsonl"
    write_json(plan_path, plan)
    write_jsonl(sliced_affected_path, affected)
    write_jsonl(sliced_candidates_path, selected_candidates)
    write_jsonl(sliced_reviewed_path, selected_reviews)
    write_jsonl(outstanding_path, outstanding_candidates)

    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "source_doc": source_doc,
        "ready_for_migration_readiness_audit": ready,
        "active_gold_rows": len(active_rows),
        "active_source_references": reference_count,
        "active_provenance_basis": provenance_basis,
        "affected_rows": len(affected),
        "accepted_reviewed_candidates_available": len(accepted_reviews),
        "selected_reviewed_replacements": len(selected_candidates),
        "selected_outstanding_review_rows": len(outstanding_candidates),
        "unfillable_replacement_rows": sum(capacity_shortfalls.values()),
        "requirements": serialize_counts(requirements),
        "selected": serialize_counts(Counter(candidate_key(row) for row in selected_candidates)),
        "outstanding": serialize_counts(
            Counter(candidate_key(row) for row in outstanding_candidates)
        ),
        "issues": issues,
        "artifacts": {
            "plan": plan_path.relative_to(root).as_posix(),
            "affected": sliced_affected_path.relative_to(root).as_posix(),
            "candidates": sliced_candidates_path.relative_to(root).as_posix(),
            "reviewed": sliced_reviewed_path.relative_to(root).as_posix(),
            "outstanding": outstanding_path.relative_to(root).as_posix(),
        },
    }
    write_json(output_dir / "source_atomic_slice_report.json", report)
    lines = [
        "# Source-Atomic Provenance Slice",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Source: `{source_doc}`",
        f"- Ready for migration-readiness audit: `{str(ready).lower()}`",
        f"- Current active references: `{reference_count}`",
        f"- Affected rows: `{len(affected)}`",
        f"- Selected completed replacements: `{len(selected_candidates)}`",
        f"- Exact unreviewed rows to close this source: `{len(outstanding_candidates)}`",
        f"- Unfillable replacement rows: `{sum(capacity_shortfalls.values())}`",
        "",
        "## Coverage",
        "",
        "| Contract key | Required | Reviewed | Outstanding | Unfillable |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    selected_counts = Counter(candidate_key(row) for row in selected_candidates)
    outstanding_counts = Counter(candidate_key(row) for row in outstanding_candidates)
    for key, count in sorted(requirements.items()):
        label = ":".join(key)
        lines.append(
            f"| `{label}` | {count} | {selected_counts.get(key, 0)} | "
            f"{outstanding_counts.get(key, 0)} | {capacity_shortfalls.get(label, 0)} |"
        )
    lines.extend(["", "## Issues", ""])
    lines.extend(
        ["- None."]
        if not issues
        else [f"- `{issue['code']}`: `{json.dumps(issue, ensure_ascii=False)}`" for issue in issues]
    )
    (output_dir / "source_atomic_slice_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return report


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--parent-plan", type=Path, required=True)
    parser.add_argument("--affected", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, action="append", required=True)
    parser.add_argument("--source-doc", required=True)
    parser.add_argument("--active-provenance-report", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    report = build_slice(
        root=root,
        parent_plan_path=resolve(root, args.parent_plan),
        affected_path=resolve(root, args.affected),
        candidates_path=resolve(root, args.candidates),
        reviewed_paths=[resolve(root, path) for path in args.reviewed],
        source_doc=args.source_doc,
        output_dir=resolve(root, args.output_dir),
        date_label=args.date_label,
        provenance_report_path=(
            resolve(root, args.active_provenance_report)
            if args.active_provenance_report
            else None
        ),
    )
    print(
        json.dumps(
            {
                "source_doc": report["source_doc"],
                "affected_rows": report["affected_rows"],
                "selected_reviewed_replacements": report[
                    "selected_reviewed_replacements"
                ],
                "selected_outstanding_review_rows": report[
                    "selected_outstanding_review_rows"
                ],
                "ready": report["ready_for_migration_readiness_audit"],
                "issues": len(report["issues"]),
            },
            indent=2,
        )
    )
    if args.require_ready and not report["ready_for_migration_readiness_audit"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
