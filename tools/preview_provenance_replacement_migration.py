#!/usr/bin/env python3
"""Build a fail-closed, read-only preview of an atomic provenance migration.

The tool never edits active Gold. It first requires the frozen one-for-one
replacement plan to pass the migration-readiness audit, then removes every
rights-blocked active row and inserts every accepted replacement into combined
preview artifacts under ``derived/quality``.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_provenance_migration_readiness as readiness
import preview_reviewed_gold_promotion as promotion


def active_category(row: dict[str, Any], task: str) -> str:
    if task == "microtext":
        return str(row.get("category") or "").strip().lower()
    value = row.get("change_type")
    if isinstance(value, list):
        return "+".join(sorted(str(item).strip().lower() for item in value if item))
    return str(value or "").strip().lower()


def remove_affected_rows(
    *,
    items: list[dict[str, Any]],
    micro_questions: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    visual_questions: list[dict[str, Any]],
    affected_rows: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    item_by_id = {str(row.get("item_id") or ""): row for row in items}
    pair_by_id = {str(row.get("pair_id") or ""): row for row in pairs}
    micro_q_by_id = {
        str(row.get("question_id") or ""): row for row in micro_questions
    }
    visual_q_by_id = {
        str(row.get("question_id") or ""): row for row in visual_questions
    }
    remove_item_ids: set[str] = set()
    remove_pair_ids: set[str] = set()
    remove_micro_qids: set[str] = set()
    remove_visual_qids: set[str] = set()
    ledger: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for affected in affected_rows:
        active_id = str(affected.get("active_id") or "").strip()
        task = str(affected.get("task") or "").strip().lower()
        expected_split = str(affected.get("split") or "").strip().lower()
        expected_category = str(affected.get("category") or "").strip().lower()
        if task == "microtext":
            question = micro_q_by_id.get(active_id)
            item_ids = list((question or {}).get("item_ids") or [])
            if question is None:
                issues.append({"active_id": active_id, "reason": "microtext_question_missing"})
                continue
            if len(item_ids) != 1:
                issues.append(
                    {
                        "active_id": active_id,
                        "reason": "microtext_question_not_single_item",
                        "item_ids": item_ids,
                    }
                )
                continue
            item_id = str(item_ids[0])
            item = item_by_id.get(item_id)
            if item is None:
                issues.append({"active_id": active_id, "reason": "microtext_item_missing", "item_id": item_id})
                continue
            if item_id in remove_item_ids:
                issues.append({"active_id": active_id, "reason": "microtext_item_removed_twice", "item_id": item_id})
                continue
            if str(item.get("split") or "").strip().lower() != expected_split:
                issues.append({"active_id": active_id, "reason": "microtext_split_mismatch"})
                continue
            if expected_category and active_category(item, task) != expected_category:
                issues.append({"active_id": active_id, "reason": "microtext_category_mismatch"})
                continue
            remove_item_ids.add(item_id)
            remove_micro_qids.add(active_id)
            ledger.append({**affected, "underlying_id": item_id})
        elif task == "visualdiff":
            question = visual_q_by_id.get(active_id)
            pair_id = str((question or {}).get("pair_id") or "")
            if question is None:
                issues.append({"active_id": active_id, "reason": "visualdiff_question_missing"})
                continue
            pair = pair_by_id.get(pair_id)
            if pair is None:
                issues.append({"active_id": active_id, "reason": "visualdiff_pair_missing", "pair_id": pair_id})
                continue
            if pair_id in remove_pair_ids:
                issues.append({"active_id": active_id, "reason": "visualdiff_pair_removed_twice", "pair_id": pair_id})
                continue
            if str(pair.get("split") or "").strip().lower() != expected_split:
                issues.append({"active_id": active_id, "reason": "visualdiff_split_mismatch"})
                continue
            categories = set(active_category(pair, task).split("+"))
            if expected_category and expected_category not in categories:
                issues.append({"active_id": active_id, "reason": "visualdiff_category_mismatch"})
                continue
            remove_pair_ids.add(pair_id)
            remove_visual_qids.add(active_id)
            ledger.append({**affected, "underlying_id": pair_id})
        else:
            issues.append({"active_id": active_id, "reason": "unknown_affected_task", "task": task})

    remaining_items = [row for row in items if str(row.get("item_id") or "") not in remove_item_ids]
    remaining_micro_questions = [
        row for row in micro_questions if str(row.get("question_id") or "") not in remove_micro_qids
    ]
    remaining_pairs = [row for row in pairs if str(row.get("pair_id") or "") not in remove_pair_ids]
    remaining_visual_questions = [
        row for row in visual_questions if str(row.get("question_id") or "") not in remove_visual_qids
    ]

    for row in remaining_micro_questions:
        overlap = remove_item_ids & {str(value) for value in row.get("item_ids") or []}
        if overlap:
            issues.append(
                {
                    "active_id": row.get("question_id"),
                    "reason": "remaining_question_references_removed_item",
                    "item_ids": sorted(overlap),
                }
            )
    for row in remaining_visual_questions:
        pair_id = str(row.get("pair_id") or "")
        if pair_id in remove_pair_ids:
            issues.append(
                {
                    "active_id": row.get("question_id"),
                    "reason": "remaining_question_references_removed_pair",
                    "pair_id": pair_id,
                }
            )
    if len(ledger) != len(affected_rows):
        issues.append(
            {
                "reason": "affected_removal_count_mismatch",
                "expected": len(affected_rows),
                "actual": len(ledger),
            }
        )
    return (
        remaining_items,
        remaining_micro_questions,
        remaining_pairs,
        remaining_visual_questions,
        ledger,
        issues,
    )


def referenced_source_docs(
    items: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    manifest_pairs: dict[str, dict[str, Any]],
) -> tuple[set[str], list[str]]:
    doc_ids = {str(row.get("doc_id") or "").strip() for row in items}
    issues: list[str] = []
    for row in pairs:
        resolved, issue = promotion.resolve_visualdiff_docs(row, docs, manifest_pairs)
        if issue:
            issues.append(f"{row.get('pair_id')}:{issue}")
        doc_ids.update(resolved)
    return {value for value in doc_ids if value}, issues


def task_split_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        (str(row.get("task") or ""), str(row.get("split") or "")) for row in rows
    )
    return {f"{task}:{split}": count for (task, split), count in sorted(counts.items())}


def source_provenance_delta(
    *,
    source_atomic: bool,
    active_resolution_issues: list[str],
    preview_resolution_issues: list[str],
    active_source_issues: dict[str, list[str]],
    preview_source_issues: dict[str, list[str]],
) -> dict[str, Any]:
    """Evaluate provenance strictly or by no-regression source-atomic policy."""
    active_resolution = set(active_resolution_issues)
    preview_resolution = set(preview_resolution_issues)
    new_resolution = sorted(preview_resolution - active_resolution)
    new_source_issues = {
        doc_id: issues
        for doc_id, issues in sorted(preview_source_issues.items())
        if set(issues) - set(active_source_issues.get(doc_id, []))
    }
    retired_source_issues = {
        doc_id: issues
        for doc_id, issues in sorted(active_source_issues.items())
        if doc_id not in preview_source_issues
    }
    if source_atomic:
        passes = not new_resolution and not new_source_issues
        policy = "source_atomic_no_provenance_regression"
    else:
        passes = not preview_resolution_issues and not preview_source_issues
        policy = "complete_migration_all_sources_paper_ready"
    return {
        "policy": policy,
        "passes": passes,
        "new_resolution_issues": new_resolution,
        "new_source_issues": new_source_issues,
        "retired_source_issues": retired_source_issues,
        "remaining_source_issues": preview_source_issues,
    }


def merge_reservation_records(
    split_plan_paths: list[Path],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Merge split plans while rejecting conflicting authority for one unit."""
    reservations: dict[tuple[str, str], dict[str, Any]] = {}
    issues: list[str] = []
    summaries: list[dict[str, Any]] = []
    for path in split_plan_paths:
        plan_records, plan_issues = promotion.reservation_records(path)
        issues.extend(f"{path.as_posix()}:{issue}" for issue in plan_issues)
        added = 0
        duplicates = 0
        for unit, row in plan_records.items():
            existing = reservations.get(unit)
            if existing is None:
                reservations[unit] = row
                added += 1
                continue
            same_split = str(existing.get("split") or "").strip().lower() == str(
                row.get("split") or ""
            ).strip().lower()
            same_reservation = str(existing.get("reservation_id") or "").strip() == str(
                row.get("reservation_id") or ""
            ).strip()
            if not same_split or not same_reservation:
                issues.append(
                    f"split_reservation_plan_conflict:{unit[0]}:{unit[1]}:"
                    f"{existing.get('split')}:{existing.get('reservation_id')}:"
                    f"{row.get('split')}:{row.get('reservation_id')}"
                )
                continue
            duplicates += 1
        summaries.append(
            {
                "path": path.as_posix(),
                "sha256": readiness.file_sha256(path),
                "reservations_read": len(plan_records),
                "reservations_added": added,
                "identical_duplicates": duplicates,
                "issues": len(plan_issues),
            }
        )
    return reservations, issues, summaries


def audit_candidate_reservation_coverage(
    *,
    root: Path,
    candidate_rows: list[dict[str, Any]],
    reservations: dict[tuple[str, str], dict[str, Any]],
    split_plan_paths: list[Path],
) -> dict[str, Any]:
    loaded_plans = {path.resolve() for path in split_plan_paths}
    issue_counts: Counter[str] = Counter()
    issues: list[str] = []
    covered = 0
    for row in candidate_rows:
        identity = readiness.candidate_identity(row)
        unit = promotion.staged.staged_split_unit(row)
        reservation = reservations.get(unit)
        row_issues: list[str] = []
        if reservation is None:
            row_issues.append("missing_split_reservation")
        else:
            expected_split = str(row.get("reserved_split") or "").strip().lower()
            planned_split = str(reservation.get("split") or "").strip().lower()
            if expected_split != planned_split:
                row_issues.append("reserved_split_conflict")
            expected_id = str(row.get("split_reservation_id") or "").strip()
            planned_id = str(reservation.get("reservation_id") or "").strip()
            if expected_id and expected_id != planned_id:
                row_issues.append("split_reservation_id_conflict")
        declared_plan = str(row.get("split_reservation_plan") or "").strip()
        if not declared_plan:
            row_issues.append("missing_declared_split_plan")
        elif promotion.resolve(root, Path(declared_plan)).resolve() not in loaded_plans:
            row_issues.append("declared_split_plan_not_loaded")
        if row_issues:
            for reason in sorted(set(row_issues)):
                issue_counts[reason] += 1
                if len(issues) < 100:
                    issues.append(f"{identity}:{reason}:{unit[0]}:{unit[1]}")
        else:
            covered += 1
    return {
        "candidate_rows": len(candidate_rows),
        "covered_rows": covered,
        "uncovered_rows": len(candidate_rows) - covered,
        "issue_counts": dict(sorted(issue_counts.items())),
        "issues": issues,
        "complete": covered == len(candidate_rows),
    }


def build_preview(
    *,
    root: Path,
    plan_path: Path,
    affected_path: Path,
    candidates_path: Path,
    reviewed_paths: list[Path],
    output_dir: Path,
    date_label: str,
    split_plan_path: Path | None = None,
    split_plan_paths: list[Path] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = promotion.resolve(root, output_dir).resolve()
    allowed_root = (root / "derived" / "quality").resolve()
    if output_dir != allowed_root and allowed_root not in output_dir.parents:
        raise ValueError("output_dir must be under derived/quality")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = promotion.resolve(root, plan_path).resolve()
    affected_path = promotion.resolve(root, affected_path).resolve()
    candidates_path = promotion.resolve(root, candidates_path).resolve()
    requested_split_plans = list(split_plan_paths or [])
    if split_plan_path is not None:
        requested_split_plans.insert(0, split_plan_path)
    if not requested_split_plans:
        raise ValueError("at least one split plan is required")
    resolved_split_plans: list[Path] = []
    seen_split_plans: set[Path] = set()
    for path in requested_split_plans:
        resolved = promotion.resolve(root, path).resolve()
        if resolved not in seen_split_plans:
            resolved_split_plans.append(resolved)
            seen_split_plans.add(resolved)
    reviewed_paths = [promotion.resolve(root, path).resolve() for path in reviewed_paths]
    before_hashes = promotion.active_hashes(root)
    candidate_rows = readiness.read_jsonl(candidates_path)
    reservations, reservation_issues, raw_reservation_plan_summaries = merge_reservation_records(
        resolved_split_plans
    )
    reservation_plan_summaries = [
        {
            **summary,
            "path": (
                path.relative_to(root).as_posix()
                if path.is_relative_to(root)
                else path.as_posix()
            ),
        }
        for summary, path in zip(raw_reservation_plan_summaries, resolved_split_plans)
    ]
    reservation_coverage = audit_candidate_reservation_coverage(
        root=root,
        candidate_rows=candidate_rows,
        reservations=reservations,
        split_plan_paths=resolved_split_plans,
    )
    split_plan_paths_relative = [
        path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        for path in resolved_split_plans
    ]

    migration_readiness = readiness.build_report(
        root=root,
        plan_path=plan_path,
        affected_path=affected_path,
        candidates_path=candidates_path,
        reviewed_paths=reviewed_paths,
        date_label=date_label,
    )
    if not migration_readiness["ready_for_atomic_migration"]:
        after_hashes = promotion.active_hashes(root)
        return {
            "goal": "Gold v2.0 Global",
            "date_label": date_label,
            "mode": "read_only_atomic_migration_preview",
            "ready_for_atomic_apply": False,
            "preview_built": False,
            "active_gold_modified": before_hashes != after_hashes,
            "migration_readiness": migration_readiness,
            "counts": {
                "affected_rows": migration_readiness["counts"]["affected_gold_rows"],
                "reviewed_rows": migration_readiness["counts"]["reviewed_rows"],
                "outstanding_review_rows": migration_readiness["counts"]["outstanding_review_rows"],
                "split_reservation_plans": len(resolved_split_plans),
                "candidate_reservations_covered": reservation_coverage["covered_rows"],
                "candidate_reservations_uncovered": reservation_coverage["uncovered_rows"],
            },
            "gates": {
                "migration_readiness": False,
                "split_reservation_plans_valid": not reservation_issues,
                "candidate_split_reservations_complete": reservation_coverage["complete"],
                "active_files_unchanged": before_hashes == after_hashes,
            },
            "split_reservation_plans": split_plan_paths_relative,
            "split_reservation_plan_summaries": reservation_plan_summaries,
            "split_reservation_issues": reservation_issues,
            "candidate_split_reservation_coverage": reservation_coverage,
            "active_file_hashes_before": before_hashes,
            "active_file_hashes_after": after_hashes,
            "artifacts": {},
            "artifact_sha256": {},
            "interpretation": (
                "Preview construction is blocked until every frozen replacement has an accepted or "
                "edited, metadata-aligned human review. Active Gold remains unchanged."
            ),
        }

    affected_rows = readiness.read_jsonl(affected_path)
    reviewed_rows = [row for path in reviewed_paths for row in readiness.read_jsonl(path)]
    reviewed_micro = [row for row in reviewed_rows if str(row.get("replacement_for_task") or row.get("task") or "").lower() == "microtext"]
    reviewed_visual = [row for row in reviewed_rows if str(row.get("replacement_for_task") or row.get("task") or "").lower() == "visualdiff"]

    active_items = promotion.read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_micro_questions = promotion.read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    active_pairs = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    active_visual_questions = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    active_unified = promotion.read_jsonl(root / "eng_bench.jsonl")
    (
        remaining_items,
        remaining_micro_questions,
        remaining_pairs,
        remaining_visual_questions,
        removal_ledger,
        removal_issues,
    ) = remove_affected_rows(
        items=active_items,
        micro_questions=active_micro_questions,
        pairs=active_pairs,
        visual_questions=active_visual_questions,
        affected_rows=affected_rows,
    )

    micro_splits, visual_splits, split_issues = promotion.split_maps(root, reservations)
    manifest_rows = promotion.read_jsonl(root / "manifest.jsonl")
    docs, manifest_pairs = promotion.manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or ""): row
        for row in promotion.read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    eligibility_holds: list[dict[str, Any]] = []
    for row in reviewed_rows:
        task = str(row.get("replacement_for_task") or row.get("task") or "").strip().lower()
        identity = readiness.candidate_identity(row)
        reasons: list[str] = []
        unit = promotion.staged.staged_split_unit(row)
        reservation = reservations.get(unit)
        planned_split = str((reservation or {}).get("split") or "").strip().lower()
        if reservation is None:
            reasons.append(f"missing_split_reservation:{unit[0]}:{unit[1]}")
        elif str(row.get("reserved_split") or "").strip().lower() != planned_split:
            reasons.append("reserved_split_conflict")
        reasons.extend(promotion.evidence_issues(root, row, task))
        if task == "microtext":
            doc_ids = [str(row.get("doc_id") or "").strip()]
        elif task == "visualdiff":
            description = promotion.visualdiff_merge.description(row)
            if promotion.visualdiff_description_requires_english_localization(description):
                reasons.append("visualdiff_description_requires_english_localization")
            doc_ids, issue = promotion.resolve_visualdiff_docs(row, docs, manifest_pairs)
            if issue:
                reasons.append(f"visualdiff_manifest_resolution:{issue}")
        else:
            doc_ids = []
            reasons.append(f"unknown_task:{task}")
        for doc_id in filter(None, doc_ids):
            reasons.extend(
                f"source:{doc_id}:{issue}"
                for issue in promotion.source_audit(root, doc_id, docs, inventory)
            )
        if not any(doc_ids):
            reasons.append("missing_source_doc_id")
        if reasons:
            eligibility_holds.append(
                {"task": task, "identity": identity, "reasons": sorted(set(reasons)), "row": row}
            )

    reviewed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    merged_items, merged_micro_questions, micro_stats = promotion.microtext_merge.merge_review_rows(
        remaining_items,
        remaining_micro_questions,
        [] if eligibility_holds else reviewed_micro,
        split="dev",
        reviewed_at=reviewed_at,
        split_by_doc=micro_splits,
        require_split_map=True,
    )
    merged_pairs, merged_visual_questions, accepted_visual, visual_report = promotion.visualdiff_merge.merge_reviewed_rows(
        remaining_pairs,
        remaining_visual_questions,
        [] if eligibility_holds else reviewed_visual,
        visual_splits,
        manifest_rows,
    )
    prepared_micro = merged_items[len(remaining_items) :]
    inserted_count = len(prepared_micro) + len(accepted_visual)
    merge_issues: list[str] = []
    if len(prepared_micro) != len(reviewed_micro):
        merge_issues.append(f"microtext_merge_count_mismatch:{len(prepared_micro)}:{len(reviewed_micro)}")
    if len(accepted_visual) != len(reviewed_visual):
        merge_issues.append(f"visualdiff_merge_count_mismatch:{len(accepted_visual)}:{len(reviewed_visual)}")
    merge_issues.extend(
        f"visualdiff_hold:{row.get('pair_id')}:{','.join(row.get('reasons') or [])}"
        for row in visual_report.get("held_rows") or []
    )

    paths = {
        "combined_microtext_items": output_dir / "combined_microtext_items.jsonl",
        "combined_microtext_questions": output_dir / "combined_microtext_questions.jsonl",
        "combined_visualdiff_pairs": output_dir / "combined_visualdiff_pairs.jsonl",
        "combined_visualdiff_questions": output_dir / "combined_visualdiff_questions.jsonl",
        "inserted_microtext_items": output_dir / "inserted_microtext_items.jsonl",
        "inserted_visualdiff_pairs": output_dir / "inserted_visualdiff_pairs.jsonl",
        "removed_active_rows": output_dir / "removed_active_rows.jsonl",
        "eligibility_holds": output_dir / "migration_eligibility_holds.jsonl",
        "unified": output_dir / "eng_bench_migration_preview.jsonl",
    }
    promotion.write_jsonl(paths["combined_microtext_items"], merged_items)
    promotion.write_jsonl(paths["combined_microtext_questions"], merged_micro_questions)
    promotion.write_jsonl(paths["combined_visualdiff_pairs"], merged_pairs)
    promotion.write_jsonl(paths["combined_visualdiff_questions"], merged_visual_questions)
    promotion.write_jsonl(paths["inserted_microtext_items"], prepared_micro)
    promotion.write_jsonl(paths["inserted_visualdiff_pairs"], accepted_visual)
    promotion.write_jsonl(paths["removed_active_rows"], removal_ledger)
    promotion.write_jsonl(paths["eligibility_holds"], eligibility_holds)

    unified = promotion.unify_dataset.process_visualdiff(
        root, paths["combined_visualdiff_pairs"], paths["combined_visualdiff_questions"]
    ) + promotion.unify_dataset.process_microtext(
        root, paths["combined_microtext_items"], paths["combined_microtext_questions"]
    )
    promotion.write_jsonl(paths["unified"], unified)
    annotation_issues = promotion.annotation_errors(
        merged_pairs, merged_visual_questions, merged_items, merged_micro_questions
    )
    leakage_issues = promotion.preview_split_leakage(
        merged_items, merged_pairs, docs, manifest_pairs
    )
    manifest = promotion.validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict_report, bad_indices = promotion.validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    question_leakage = promotion.audit_question_leakage.audit(unified)
    active_doc_ids, active_source_resolution_issues = referenced_source_docs(
        active_items, active_pairs, docs, manifest_pairs
    )
    active_source_issues = {
        doc_id: promotion.source_audit(root, doc_id, docs, inventory)
        for doc_id in sorted(active_doc_ids)
    }
    active_source_issues = {key: value for key, value in active_source_issues.items() if value}
    preview_doc_ids, source_resolution_issues = referenced_source_docs(
        merged_items, merged_pairs, docs, manifest_pairs
    )
    source_issues = {
        doc_id: promotion.source_audit(root, doc_id, docs, inventory)
        for doc_id in sorted(preview_doc_ids)
    }
    source_issues = {key: value for key, value in source_issues.items() if value}
    plan = readiness.read_json(plan_path)
    blocked_docs = set(plan.get("blocked_active_source_docs") or [])
    blocked_remaining = sorted(preview_doc_ids & blocked_docs)
    source_atomic = plan.get("source_atomic") is True
    provenance_delta = source_provenance_delta(
        source_atomic=source_atomic,
        active_resolution_issues=active_source_resolution_issues,
        preview_resolution_issues=source_resolution_issues,
        active_source_issues=active_source_issues,
        preview_source_issues=source_issues,
    )
    active_counts = task_split_counts(active_unified)
    preview_counts = task_split_counts(unified)
    after_hashes = promotion.active_hashes(root)
    gates = {
        "migration_readiness": True,
        "split_plan": not reservation_issues and reservation_coverage["complete"] and not split_issues,
        "exact_affected_removal": not removal_issues and len(removal_ledger) == len(affected_rows),
        "all_reviewed_rows_eligible": not eligibility_holds,
        "all_replacements_inserted": not merge_issues and inserted_count == len(affected_rows),
        "row_count_preserved": len(unified) == len(active_unified),
        "task_split_counts_preserved": active_counts == preview_counts,
        "blocked_sources_removed": not blocked_remaining,
        "preview_source_provenance": provenance_delta["passes"],
        "annotation_validation": not annotation_issues,
        "combined_split_leakage": not leakage_issues,
        "strict_unified_validation": not strict_report.errors and not bad_indices,
        "question_answer_leakage": question_leakage["critical_failures"] == 0,
        "active_files_unchanged": before_hashes == after_hashes,
    }
    ready = all(gates.values())
    return {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_atomic_migration_preview",
        "ready_for_atomic_apply": ready,
        "preview_built": True,
        "active_gold_modified": before_hashes != after_hashes,
        "migration_readiness": migration_readiness,
        "counts": {
            "active_gold_rows": len(active_unified),
            "affected_rows": len(affected_rows),
            "removed_rows": len(removal_ledger),
            "reviewed_rows": len(reviewed_rows),
            "inserted_microtext_rows": len(prepared_micro),
            "inserted_visualdiff_rows": len(accepted_visual),
            "inserted_rows": inserted_count,
            "preview_gold_rows": len(unified),
            "preview_source_docs": len(preview_doc_ids),
            "split_reservation_plans": len(resolved_split_plans),
            "candidate_reservations_covered": reservation_coverage["covered_rows"],
            "candidate_reservations_uncovered": reservation_coverage["uncovered_rows"],
        },
        "active_task_split_counts": active_counts,
        "preview_task_split_counts": preview_counts,
        "gates": gates,
        "split_reservation_plans": split_plan_paths_relative,
        "split_reservation_plan_summaries": reservation_plan_summaries,
        "split_reservation_issues": reservation_issues,
        "candidate_split_reservation_coverage": reservation_coverage,
        "removal_issues": removal_issues,
        "eligibility_holds": [
            {"task": row["task"], "identity": row["identity"], "reasons": row["reasons"]}
            for row in eligibility_holds[:100]
        ],
        "merge_issues": merge_issues,
        "blocked_source_docs_remaining": blocked_remaining,
        "source_resolution_issues": source_resolution_issues[:100],
        "source_provenance_issues": dict(list(source_issues.items())[:100]),
        "source_atomic": source_atomic,
        "source_provenance_delta": provenance_delta,
        "annotation_errors": annotation_issues[:100],
        "split_leakage_issues": leakage_issues[:100],
        "strict_v2": {
            "errors": strict_report.errors[:100],
            "warnings": strict_report.warnings[:100],
            "stats": strict_report.stats,
            "bad_rows": len(bad_indices),
        },
        "question_leakage": question_leakage,
        "active_file_hashes_before": before_hashes,
        "active_file_hashes_after": after_hashes,
        "artifacts": {key: path.relative_to(root).as_posix() for key, path in paths.items()},
        "artifact_sha256": {key: readiness.file_sha256(path) for key, path in paths.items()},
        "interpretation": (
            "ready_for_atomic_apply means the exact complete human-reviewed replacement set survives "
            "a removal-plus-insertion preview with all release gates shown here. This command still does "
            "not edit Gold; a later explicit apply requires a fresh snapshot and hash lock."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Provenance Replacement Migration Preview",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Preview built: `{str(report['preview_built']).lower()}`",
        f"- Ready for atomic apply: `{str(report['ready_for_atomic_apply']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Affected rows: `{counts.get('affected_rows', 0)}`",
        f"- Reviewed rows: `{counts.get('reviewed_rows', 0)}`",
        f"- Outstanding review rows: `{counts.get('outstanding_review_rows', 0)}`",
        f"- Split reservation plans: `{counts.get('split_reservation_plans', 0)}`",
        f"- Candidate reservations covered: `{counts.get('candidate_reservations_covered', 0)}`",
        f"- Candidate reservations uncovered: `{counts.get('candidate_reservations_uncovered', 0)}`",
        "",
        "## Gates",
        "",
        "| Gate | Pass |",
        "|---|---|",
    ]
    lines.extend(f"| `{key}` | `{str(value).lower()}` |" for key, value in report["gates"].items())
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--affected", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, action="append", default=[])
    parser.add_argument(
        "--split-plan",
        dest="split_plans",
        type=Path,
        action="append",
        required=True,
        help="Split reservation plan; repeat for candidates covered by multiple plans.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_preview(
        root=args.root,
        plan_path=args.plan,
        affected_path=args.affected,
        candidates_path=args.candidates,
        reviewed_paths=args.reviewed,
        split_plan_paths=args.split_plans,
        output_dir=args.output_dir,
        date_label=args.date_label,
    )
    root = args.root.resolve()
    output_dir = promotion.resolve(root, args.output_dir)
    promotion.write_json(output_dir / "migration_preview_report.json", report)
    (output_dir / "migration_preview_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ready_for_atomic_apply": report["ready_for_atomic_apply"],
                "preview_built": report["preview_built"],
                "active_gold_modified": report["active_gold_modified"],
                "counts": report["counts"],
                "gates": report["gates"],
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if args.require_ready and not report["ready_for_atomic_apply"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
