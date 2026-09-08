#!/usr/bin/env python3
"""Audit an active-provenance replacement plan before atomic Gold migration."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import build_provenance_replacement_plan as planner


PROMOTABLE_STATUSES = {
    "accept": "accepted",
    "accepted": "accepted",
    "edit": "edited",
    "edited": "edited",
}
REJECTED_STATUSES = {"reject", "rejected"}

COMMON_REVIEW_ALIGNMENT_FIELDS = (
    "task",
    "source_candidate_id",
    "reserved_split",
    "split_reservation_plan",
    "split_reservation_id",
    "provenance_replacement_candidate",
    "provenance_replacement_date_label",
    "replacement_for_task",
    "replacement_for_split",
    "replacement_for_category",
    "replacement_match_level",
    "replacement_origin_phase",
    "replacement_rights_check",
    "replacement_source_unit",
    "replacement_evidence_fingerprint",
    "replacement_evidence_fingerprint_status",
)
MICROTEXT_REVIEW_ALIGNMENT_FIELDS = (
    "candidate_id",
    "doc_id",
    "version_id",
    "page_index",
    "image_path",
    "bbox",
)
VISUALDIFF_REVIEW_ALIGNMENT_FIELDS = (
    "pair_id",
    "project_id",
    "image_old",
    "image_new",
    "bbox_old",
    "bbox_new",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def active_identity(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("qid") or "").strip()


def candidate_identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def review_status(row: dict[str, Any]) -> str:
    return str(
        row.get("human_review_status")
        or row.get("human_status")
        or row.get("review_status")
        or row.get("status")
        or ""
    ).strip().lower()


def alignment_value(field: str, value: Any) -> Any:
    if field in {"image_path", "image_old", "image_new"}:
        return str(value or "").strip().replace("\\", "/")
    if field in {"page_index"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field in {"bbox", "bbox_old", "bbox_new"}:
        return list(value) if isinstance(value, (list, tuple)) else value
    if isinstance(value, str):
        return value.strip()
    return value


def reviewed_candidate_alignment_issues(
    candidate: dict[str, Any], reviewed: dict[str, Any]
) -> list[dict[str, Any]]:
    task = str(candidate.get("replacement_for_task") or candidate.get("task") or "").strip().lower()
    fields = list(COMMON_REVIEW_ALIGNMENT_FIELDS)
    fields.extend(
        MICROTEXT_REVIEW_ALIGNMENT_FIELDS
        if task == "microtext"
        else VISUALDIFF_REVIEW_ALIGNMENT_FIELDS
    )
    issues: list[dict[str, Any]] = []
    for field in fields:
        expected = alignment_value(field, candidate.get(field))
        actual = alignment_value(field, reviewed.get(field))
        if expected != actual:
            issues.append({"field": field, "expected": expected, "actual": actual})
    if reviewed.get("safe_to_merge_gold") is not False:
        issues.append(
            {
                "field": "safe_to_merge_gold",
                "expected": False,
                "actual": reviewed.get("safe_to_merge_gold"),
            }
        )
    return issues


def task_split_counts(rows: list[dict[str, Any]], *, affected: bool) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        task = str(row.get("task") if affected else row.get("replacement_for_task") or "").lower()
        split = str(row.get("split") if affected else row.get("replacement_for_split") or "").lower()
        counts[(task, split)] += 1
    return counts


def serialized_counts(counts: Counter[tuple[str, str]]) -> dict[str, int]:
    return {f"{task}:{split}": count for (task, split), count in sorted(counts.items())}


def add_issue(issues: list[dict[str, Any]], code: str, detail: Any) -> None:
    issues.append({"code": code, "detail": detail})


def build_report(
    *,
    root: Path,
    plan_path: Path,
    affected_path: Path,
    candidates_path: Path,
    reviewed_paths: list[Path] | None = None,
    date_label: str | None = None,
) -> dict[str, Any]:
    plan = read_json(plan_path)
    affected_rows = read_jsonl(affected_path)
    candidate_rows = read_jsonl(candidates_path)
    active_path = root / "eng_bench.jsonl"
    active_rows = read_jsonl(active_path)
    reviewed_paths = reviewed_paths or []
    reviewed_rows = [row for path in reviewed_paths for row in read_jsonl(path)]

    structural_issues: list[dict[str, Any]] = []
    review_issues: list[dict[str, Any]] = []

    active_by_id = {active_identity(row): row for row in active_rows if active_identity(row)}
    active_underlying_ids = planner.active_candidate_identities(
        active_rows,
        active_items=read_jsonl(
            root / "microtext" / "annotations" / "microtext_items.jsonl"
        ),
        active_pairs=read_jsonl(
            root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
        ),
    )
    affected_ids = [str(row.get("active_id") or "").strip() for row in affected_rows]
    candidate_ids = [candidate_identity(row) for row in candidate_rows]

    if len(active_by_id) != len(active_rows):
        add_issue(structural_issues, "active_ids_missing_or_duplicate", len(active_rows) - len(active_by_id))
    if any(not value for value in affected_ids):
        add_issue(structural_issues, "affected_active_id_missing", sum(not value for value in affected_ids))
    if len(set(affected_ids)) != len(affected_ids):
        add_issue(structural_issues, "affected_active_ids_duplicate", len(affected_ids) - len(set(affected_ids)))
    missing_active_ids = sorted(set(affected_ids) - set(active_by_id))
    if missing_active_ids:
        add_issue(structural_issues, "affected_ids_absent_from_active_gold", missing_active_ids[:20])

    active_metadata_mismatches = []
    for row in affected_rows:
        active = active_by_id.get(str(row.get("active_id") or ""))
        if not active:
            continue
        for field in ("task", "split"):
            expected = str(row.get(field) or "").strip().lower()
            actual = str(active.get(field) or "").strip().lower()
            if expected != actual:
                active_metadata_mismatches.append(
                    {"active_id": row.get("active_id"), "field": field, "expected": expected, "actual": actual}
                )
    if active_metadata_mismatches:
        add_issue(structural_issues, "affected_active_metadata_mismatch", active_metadata_mismatches[:20])

    if any(not value for value in candidate_ids):
        add_issue(structural_issues, "candidate_identity_missing", sum(not value for value in candidate_ids))
    if len(set(candidate_ids)) != len(candidate_ids):
        add_issue(structural_issues, "candidate_identities_duplicate", len(candidate_ids) - len(set(candidate_ids)))
    active_candidate_collisions = sorted(set(candidate_ids) & active_underlying_ids)
    if active_candidate_collisions:
        add_issue(
            structural_issues,
            "replacement_candidates_already_active_gold",
            active_candidate_collisions[:20],
        )

    fingerprints = [str(row.get("replacement_evidence_fingerprint") or "") for row in candidate_rows]
    if any(not value for value in fingerprints):
        add_issue(structural_issues, "evidence_fingerprint_missing", sum(not value for value in fingerprints))
    if len(set(fingerprints)) != len(fingerprints):
        add_issue(structural_issues, "evidence_fingerprints_duplicate", len(fingerprints) - len(set(fingerprints)))

    manifest = planner.read_jsonl(root / "manifest.jsonl")
    pair_docs = planner.manifest_pair_docs(manifest)
    inventory_status = {
        str(row.get("doc_id") or "").strip(): str(row.get("public_status") or row.get("rights_tier") or "").strip()
        for row in planner.read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    blocked_docs = set(plan.get("blocked_active_source_docs") or [])
    candidate_field_issues: list[dict[str, Any]] = []
    evidence_rehash_mismatches: list[str] = []
    for row in candidate_rows:
        identity = candidate_identity(row)
        task = str(row.get("replacement_for_task") or "").strip().lower()
        split = str(row.get("replacement_for_split") or "").strip().lower()
        reserved_split = str(row.get("reserved_split") or row.get("split") or "").strip().lower()
        rights_ok, rights_reason = planner.candidate_is_release_safe(
            row, inventory_status, blocked_docs, pair_docs
        )
        checks = {
            "replacement_candidate": row.get("provenance_replacement_candidate") is True,
            "task": task in {"microtext", "visualdiff"} and planner.task_for(row) == task,
            "split": split in {"train", "dev", "test"} and reserved_split == split,
            "rights": rights_ok and rights_reason == "release_safe_status" and row.get("replacement_rights_check") == "release_safe_status",
            "not_mergeable": planner.false_value(row.get("safe_to_merge_gold")),
            "human_review_pending": (
                str(row.get("review_status") or "").strip().lower() in {"needs_review", "pending"}
                and row.get("promotion_state") == "unreviewed_provenance_replacement_candidate"
            ),
            "evidence_status": row.get("replacement_evidence_fingerprint_status") == "pixel_crop_sha256",
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            candidate_field_issues.append({"candidate_id": identity, "failed": failed})
        computed_fingerprint, computed_status = planner.candidate_evidence_fingerprint(root, row)
        if computed_status != "pixel_crop_sha256" or computed_fingerprint != row.get("replacement_evidence_fingerprint"):
            evidence_rehash_mismatches.append(identity)
    if candidate_field_issues:
        add_issue(structural_issues, "candidate_release_contract_failed", candidate_field_issues[:20])
    if evidence_rehash_mismatches:
        add_issue(structural_issues, "candidate_evidence_rehash_mismatch", evidence_rehash_mismatches[:20])

    affected_counts = task_split_counts(affected_rows, affected=True)
    candidate_counts = task_split_counts(candidate_rows, affected=False)
    if affected_counts != candidate_counts:
        add_issue(
            structural_issues,
            "task_split_coverage_mismatch",
            {"affected": serialized_counts(affected_counts), "candidates": serialized_counts(candidate_counts)},
        )

    summary_count_checks = {
        "affected_gold_rows": (plan.get("affected_gold_rows"), len(affected_rows)),
        "selected_replacement_candidates": (plan.get("selected_replacement_candidates"), len(candidate_rows)),
        "selected_unique_evidence_fingerprints": (plan.get("selected_unique_evidence_fingerprints"), len(set(fingerprints))),
    }
    bad_summary_counts = {
        name: {"recorded": recorded, "computed": computed}
        for name, (recorded, computed) in summary_count_checks.items()
        if recorded != computed
    }
    if bad_summary_counts:
        add_issue(structural_issues, "plan_summary_count_mismatch", bad_summary_counts)

    preferred_requested = int(plan.get("preferred_issued_requested") or 0)
    preferred_available_value = plan.get("preferred_issued_available")
    preferred_selected = int(plan.get("preferred_issued_selected") or 0)
    preferred_retired_active = int(plan.get("preferred_issued_retired_active_gold") or 0)
    preferred_excluded = int(plan.get("preferred_issued_excluded") or 0)
    preferred_missing = int(plan.get("preferred_issued_missing") or 0)
    preferred_summary_issues: dict[str, Any] = {}
    if preferred_available_value is not None:
        preferred_available = int(preferred_available_value or 0)
        partition_total = (
            preferred_available
            + preferred_retired_active
            + preferred_excluded
            + preferred_missing
        )
        if partition_total != preferred_requested:
            preferred_summary_issues["requested_partition"] = {
                "requested": preferred_requested,
                "available_plus_retired_plus_excluded_plus_missing": partition_total,
            }
        if preferred_selected > preferred_available:
            preferred_summary_issues["selected_exceeds_available"] = {
                "selected": preferred_selected,
                "available": preferred_available,
            }
    if preferred_selected > len(candidate_rows):
        preferred_summary_issues["selected_exceeds_replacement_candidates"] = {
            "selected": preferred_selected,
            "replacement_candidates": len(candidate_rows),
        }
    if preferred_summary_issues:
        add_issue(
            structural_issues,
            "preferred_issued_summary_inconsistent",
            preferred_summary_issues,
        )
    if plan.get("remaining_replacement_gap") != 0:
        add_issue(structural_issues, "plan_has_replacement_gap", plan.get("remaining_replacement_gap"))
    if plan.get("preferred_issued_missing") != 0:
        add_issue(structural_issues, "preferred_issued_rows_missing", plan.get("preferred_issued_missing"))
    if plan.get("unresolved_active_visualdiff_source_mappings") != 0:
        add_issue(
            structural_issues,
            "active_visualdiff_source_mappings_unresolved",
            plan.get("unresolved_active_visualdiff_source_mappings"),
        )
    if plan.get("active_gold_rows_modified") != 0 or plan.get("safe_to_merge_gold_rows") != 0:
        add_issue(structural_issues, "plan_reports_premature_gold_modification", True)

    candidate_by_id = {candidate_identity(row): row for row in candidate_rows if candidate_identity(row)}
    reviewed_by_id: dict[str, dict[str, Any]] = {}
    duplicate_reviewed_ids: list[str] = []
    unknown_reviewed_ids: list[str] = []
    reviewed_alignment_issues: list[dict[str, Any]] = []
    accepted_reviewed_ids: set[str] = set()
    rejected_reviewed_ids: set[str] = set()
    for row in reviewed_rows:
        identity = candidate_identity(row)
        if not identity or identity not in candidate_by_id:
            unknown_reviewed_ids.append(identity or "<missing>")
            continue
        if identity in reviewed_by_id:
            duplicate_reviewed_ids.append(identity)
            continue
        reviewed_by_id[identity] = row
        alignment_issues = reviewed_candidate_alignment_issues(candidate_by_id[identity], row)
        if alignment_issues:
            reviewed_alignment_issues.append(
                {"candidate_id": identity, "issues": alignment_issues}
            )
            continue
        status = review_status(row)
        if status in PROMOTABLE_STATUSES:
            accepted_reviewed_ids.add(identity)
        elif status in REJECTED_STATUSES:
            rejected_reviewed_ids.add(identity)
    if duplicate_reviewed_ids:
        add_issue(review_issues, "reviewed_candidate_ids_duplicate", duplicate_reviewed_ids[:20])
    if unknown_reviewed_ids:
        add_issue(review_issues, "reviewed_rows_not_in_replacement_plan", unknown_reviewed_ids[:20])
    if reviewed_alignment_issues:
        add_issue(
            review_issues,
            "reviewed_candidate_metadata_mismatch",
            reviewed_alignment_issues[:20],
        )
    if rejected_reviewed_ids:
        add_issue(review_issues, "issued_replacements_rejected", sorted(rejected_reviewed_ids)[:20])

    outstanding_ids = sorted(set(candidate_ids) - accepted_reviewed_ids)
    plan_structurally_ready = not structural_issues
    human_review_complete = (
        len(accepted_reviewed_ids) == len(candidate_rows)
        and not duplicate_reviewed_ids
        and not unknown_reviewed_ids
        and not reviewed_alignment_issues
        and not rejected_reviewed_ids
    )
    ready_for_atomic_migration = plan_structurally_ready and human_review_complete and not review_issues

    return {
        "goal": "Gold v2.0 Global",
        "date_label": date_label or date.today().isoformat(),
        "plan_path": plan_path.relative_to(root).as_posix() if plan_path.is_relative_to(root) else plan_path.as_posix(),
        "affected_path": affected_path.relative_to(root).as_posix() if affected_path.is_relative_to(root) else affected_path.as_posix(),
        "candidates_path": candidates_path.relative_to(root).as_posix() if candidates_path.is_relative_to(root) else candidates_path.as_posix(),
        "reviewed_paths": [path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix() for path in reviewed_paths],
        "hashes": {
            "active_eng_bench_sha256": file_sha256(active_path),
            "plan_sha256": file_sha256(plan_path),
            "affected_sha256": file_sha256(affected_path),
            "candidates_sha256": file_sha256(candidates_path),
        },
        "counts": {
            "active_gold_rows": len(active_rows),
            "blocked_active_source_docs": len(blocked_docs),
            "affected_gold_rows": len(affected_rows),
            "replacement_candidates": len(candidate_rows),
            "unique_replacement_identities": len(set(candidate_ids)),
            "unique_evidence_fingerprints": len(set(fingerprints)),
            "reviewed_rows": len(reviewed_rows),
            "accepted_or_edited_reviewed_rows": len(accepted_reviewed_ids),
            "rejected_reviewed_rows": len(rejected_reviewed_ids),
            "reviewed_metadata_mismatch_rows": len(reviewed_alignment_issues),
            "outstanding_review_rows": len(outstanding_ids),
        },
        "affected_by_task_split": serialized_counts(affected_counts),
        "replacement_by_task_split": serialized_counts(candidate_counts),
        "plan_structurally_ready": plan_structurally_ready,
        "human_review_complete": human_review_complete,
        "ready_for_atomic_migration": ready_for_atomic_migration,
        "structural_issues": structural_issues,
        "review_issues": review_issues,
        "outstanding_review_examples": outstanding_ids[:20],
        "migration_contract": [
            "Re-run this audit against the exact returned review JSONL and current active Gold snapshot.",
            "Require ready_for_atomic_migration=true before writing any replacement preview.",
            "Build the removal and insertion preview in a snapshot directory; never edit active files in place.",
            "Run provenance, split, leakage, duplicate, annotation, unified strict-v2, and active-hash-delta checks on the preview.",
            "Replace all active artifacts atomically only after every preview gate passes.",
        ],
        "interpretation": (
            "Structural readiness proves the one-for-one replacement plan is internally consistent and evidence-bound. "
            "It does not authorize Gold migration. Human review must be complete and all migration preview gates must pass."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Eng_Bench Provenance Migration Readiness",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Date label: `{report['date_label']}`",
        f"- Plan structurally ready: `{str(report['plan_structurally_ready']).lower()}`",
        f"- Human review complete: `{str(report['human_review_complete']).lower()}`",
        f"- Ready for atomic migration: `{str(report['ready_for_atomic_migration']).lower()}`",
        f"- Affected active Gold rows: `{counts['affected_gold_rows']}`",
        f"- Replacement candidates: `{counts['replacement_candidates']}`",
        f"- Accepted/edited reviewed rows: `{counts['accepted_or_edited_reviewed_rows']}`",
        f"- Outstanding reviewed rows: `{counts['outstanding_review_rows']}`",
        "",
        "## Task/Split Coverage",
        "",
        "| Task/split | Affected | Replacement |",
        "| --- | ---: | ---: |",
    ]
    keys = sorted(set(report["affected_by_task_split"]) | set(report["replacement_by_task_split"]))
    for key in keys:
        lines.append(
            f"| `{key}` | {report['affected_by_task_split'].get(key, 0)} | {report['replacement_by_task_split'].get(key, 0)} |"
        )
    lines.extend(["", "## Issues", ""])
    if not report["structural_issues"] and not report["review_issues"]:
        lines.append("- None.")
    for issue in report["structural_issues"]:
        lines.append(f"- Structural `{issue['code']}`: `{json.dumps(issue['detail'], ensure_ascii=False)}`")
    for issue in report["review_issues"]:
        lines.append(f"- Review `{issue['code']}`: `{json.dumps(issue['detail'], ensure_ascii=False)}`")
    lines.extend(["", "## Migration Contract", ""])
    lines.extend(f"{index}. {step}" for index, step in enumerate(report["migration_contract"], 1))
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--affected", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, action="append", default=[])
    parser.add_argument("--date-label")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    report = build_report(
        root=root,
        plan_path=resolve(root, args.plan),
        affected_path=resolve(root, args.affected),
        candidates_path=resolve(root, args.candidates),
        reviewed_paths=[resolve(root, path) for path in args.reviewed],
        date_label=args.date_label,
    )
    write_json(resolve(root, args.output_json), report)
    output_md = resolve(root, args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "plan_structurally_ready": report["plan_structurally_ready"],
        "human_review_complete": report["human_review_complete"],
        "ready_for_atomic_migration": report["ready_for_atomic_migration"],
        **report["counts"],
    }, indent=2))
    if not report["plan_structurally_ready"]:
        return 1
    if args.require_ready and not report["ready_for_atomic_migration"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
