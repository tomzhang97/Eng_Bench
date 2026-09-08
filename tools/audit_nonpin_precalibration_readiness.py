#!/usr/bin/env python3
"""Forecast current non-pin machine candidates against current Gold gates.

The audit is deliberately non-promoting. It starts from the hash-bound
Wave974 current-pending artifact, removes pin rows, checks every candidate's
policy/evidence/source/split contract, holds near-region and strict QA
duplicates, and builds a full combined-Gold validation preview. Passing rows
remain calibration-pending and unsafe to merge.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_question_leakage
import audit_staged_v2_capacity as staged
import audit_v2_0_gate
import microtext_merge
import unify_dataset
import validate_engbench_v2
from audit_active_gold_provenance import file_sha256, manifest_maps, read_csv
from audit_machine_certification_eligibility import (
    clean_text as machine_clean_text,
    evidence_record_sha256,
)
from audit_staged_promotion_contract import source_audit
from filter_machine_certified_strict_dedup import select_strict_unique_item_ids
from preview_reviewed_gold_promotion import (
    ACTIVE_PATHS,
    annotation_errors,
    evidence_issues,
    preview_split_leakage,
    reservation_records,
)


POLICY_VERSION = "1.0"
POLICY_TIER = "auto_gold_train"
EXPECTED_DISPOSITION = "auto_eligible_pending_calibration"
EXPECTED_PRIORITY = "balance_closing_nonpin"


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("record_id") or "").strip()


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def active_hashes(root: Path) -> dict[str, str]:
    return {
        relative: file_sha256(root / relative)
        for relative in ACTIVE_PATHS
        if (root / relative).is_file()
    }


def bound_active_hash_issues(
    root: Path, expected: dict[str, Any]
) -> tuple[dict[str, str], list[str]]:
    actual: dict[str, str] = {}
    issues: list[str] = []
    for relative, expected_hash in sorted(expected.items()):
        path = root / relative
        if not path.is_file():
            issues.append(f"active_file_missing:{relative}")
            continue
        actual_hash = file_sha256(path)
        actual[relative] = actual_hash
        if actual_hash != str(expected_hash).lower():
            issues.append(f"active_file_hash_mismatch:{relative}")
    return actual, issues


def canonical_evidence_issues(row: dict[str, Any]) -> list[str]:
    """Verify the immutable machine evidence carried by an eligible row."""
    issues: list[str] = []
    identity = candidate_id(row)
    evidence = row.get("machine_certification_evidence")
    ocr = row.get("machine_certification_ocr_evidence")
    if not isinstance(evidence, dict):
        return ["missing_machine_certification_evidence"]
    if not isinstance(ocr, dict):
        return ["missing_machine_certification_ocr_evidence"]
    if evidence_record_sha256(evidence) != clean_text(
        row.get("machine_certification_evidence_sha256")
    ).lower():
        issues.append("machine_certification_evidence_sha256_mismatch")
    if evidence_record_sha256(ocr) != clean_text(
        row.get("machine_certification_ocr_evidence_sha256")
    ).lower():
        issues.append("machine_certification_ocr_evidence_sha256_mismatch")
    for payload, label in ((evidence, "evidence"), (ocr, "ocr_evidence")):
        if clean_text(payload.get("candidate_id")) != identity:
            issues.append(f"{label}_candidate_id_mismatch")

    proposed = machine_clean_text(row.get("proposed_text") or row.get("target_text"))
    values = {
        "proposed_text": proposed,
        "target_text": machine_clean_text(row.get("target_text")),
        "evidence_answer": machine_clean_text(evidence.get("answer")),
        "ocr_text": machine_clean_text(row.get("machine_certification_ocr_text")),
        "ocr_evidence_text": machine_clean_text(ocr.get("ocr_text")),
    }
    if not proposed:
        issues.append("missing_candidate_text")
    for label, value in values.items():
        if not value:
            issues.append(f"missing_{label}")
        elif proposed and value != proposed:
            issues.append(f"{label}_mismatch")

    if clean_text(evidence.get("category")) != clean_text(row.get("category")):
        issues.append("evidence_category_mismatch")
    if clean_text(evidence.get("doc_id")) != clean_text(row.get("doc_id")):
        issues.append("evidence_doc_id_mismatch")
    if clean_text(evidence.get("version_id")) != clean_text(row.get("version_id")):
        issues.append("evidence_version_id_mismatch")
    if int(evidence.get("page_index", -1)) != int(row.get("page_index", -2)):
        issues.append("evidence_page_index_mismatch")
    if [int(value) for value in evidence.get("bbox") or []] != [
        int(value) for value in row.get("bbox") or []
    ]:
        issues.append("evidence_bbox_mismatch")
    if clean_text(evidence.get("reserved_split")).lower() != clean_text(
        row.get("reserved_split")
    ).lower():
        issues.append("evidence_split_mismatch")
    if clean_text(evidence.get("policy_version")) != POLICY_VERSION:
        issues.append("evidence_policy_version_mismatch")
    return sorted(set(issues))


def policy_issues(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if staged.task_for_row(row) != "microtext":
        issues.append("non_microtext_candidate")
    category = clean_text(row.get("category"))
    if not category or category in {"pin_label", "unknown", "unknown_microtext"}:
        issues.append(f"ineligible_nonpin_category:{category or 'missing'}")
    if clean_text(row.get("reserved_split")).lower() != "train":
        issues.append("non_train_reservation")
    if clean_text(row.get("machine_certification_policy_version")) != POLICY_VERSION:
        issues.append("policy_version_mismatch")
    if clean_text(row.get("machine_certification_tier")) != POLICY_TIER:
        issues.append("policy_tier_mismatch")
    if clean_text(row.get("machine_certification_disposition")) != EXPECTED_DISPOSITION:
        issues.append("eligibility_disposition_mismatch")
    if clean_text(row.get("machine_certification_release_priority")) != EXPECTED_PRIORITY:
        issues.append("release_priority_mismatch")
    if row.get("safe_to_merge_gold") is not False:
        issues.append("safety_flag_must_be_false")
    if not candidate_id(row):
        issues.append("missing_candidate_id")
    return issues


def reservation_issues(
    root: Path,
    row: dict[str, Any],
    cache: dict[Path, tuple[dict[tuple[str, str], dict[str, Any]], list[str]]],
) -> list[str]:
    plan_text = clean_text(row.get("split_reservation_plan"))
    if not plan_text:
        return ["missing_split_reservation_plan"]
    plan_path = resolve(root, plan_text)
    if not plan_path.is_file():
        return ["missing_split_reservation_plan_file"]
    if plan_path not in cache:
        cache[plan_path] = reservation_records(plan_path)
    records, plan_issues = cache[plan_path]
    issues = [f"split_plan:{issue}" for issue in plan_issues]
    unit = staged.staged_split_unit(row)
    record = records.get(unit)
    if record is None:
        issues.append(f"missing_split_reservation:{unit[0]}:{unit[1]}")
        return issues
    if clean_text(record.get("split")).lower() != "train":
        issues.append("split_reservation_not_train")
    if clean_text(record.get("reservation_id")) != clean_text(
        row.get("split_reservation_id")
    ):
        issues.append("split_reservation_id_mismatch")
    return issues


def evidence_file_issues(
    root: Path,
    row: dict[str, Any],
    image_hash_cache: dict[Path, str],
) -> list[str]:
    issues = list(evidence_issues(root, row, "microtext"))
    image_text = clean_text(row.get("image_path"))
    image_path = resolve(root, image_text) if image_text else None
    evidence = row.get("machine_certification_evidence")
    if image_path is None or not image_path.is_file() or not isinstance(evidence, dict):
        return issues
    if image_path not in image_hash_cache:
        image_hash_cache[image_path] = file_sha256(image_path)
    expected = clean_text(evidence.get("page_image_sha256")).lower()
    if image_hash_cache[image_path] != expected:
        issues.append("page_image_sha256_mismatch")
    return issues


def source_contract_issues(
    row: dict[str, Any], manifest: dict[str, Any] | None
) -> list[str]:
    if not manifest:
        return []
    issues: list[str] = []
    evidence = row.get("machine_certification_evidence")
    evidence_sha = clean_text(
        evidence.get("source_payload_sha256") if isinstance(evidence, dict) else ""
    ).lower()
    row_sha = clean_text(row.get("source_payload_sha256")).lower()
    manifest_sha = clean_text(manifest.get("sha256")).lower()
    supplied = {value for value in (evidence_sha, row_sha) if value}
    if not supplied:
        issues.append("missing_candidate_source_payload_sha256")
    if len(supplied) > 1:
        issues.append("candidate_source_payload_sha256_disagreement")
    if manifest_sha and supplied and manifest_sha not in supplied:
        issues.append("candidate_source_payload_sha256_mismatch")
    return issues


def partition_near_region_duplicates(
    active_items: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    for item in active_items:
        geometry = staged.microtext_region_geometry(item)
        if not geometry:
            continue
        doc_id, page_index, bbox = geometry
        grouped[(doc_id, page_index)].append(
            (bbox, f"active:{clean_text(item.get('item_id'))}")
        )

    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for row in candidates:
        geometry = staged.microtext_region_geometry(row)
        if not geometry:
            output = dict(row)
            output["precalibration_hold_reasons"] = ["invalid_region_geometry"]
            held.append(output)
            continue
        doc_id, page_index, bbox = geometry
        collision = next(
            (
                identity
                for other_bbox, identity in grouped[(doc_id, page_index)]
                if staged.bboxes_are_near_duplicates(bbox, other_bbox)
            ),
            "",
        )
        if collision:
            output = dict(row)
            output["precalibration_hold_reasons"] = ["near_duplicate_region"]
            output["precalibration_collides_with"] = collision
            held.append(output)
            continue
        kept.append(row)
        grouped[(doc_id, page_index)].append((bbox, f"candidate:{candidate_id(row)}"))
    return kept, held


def forecast_row(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["review_status"] = "accepted"
    output["review_source"] = "precalibration_readiness_forecast"
    output["safe_to_merge_gold"] = False
    return output


def readiness_row(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["precalibration_readiness_status"] = (
        "strict_ready_if_calibration_passes"
    )
    output["calibration_required"] = True
    output["safe_to_merge_gold"] = False
    output["promotion_state"] = "machine_calibration_pending"
    return output


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Non-Pin Pre-Calibration Readiness",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Forecast valid: `{str(report['precalibration_forecast_valid']).lower()}`",
        f"- Ready for promotion: `{str(report['ready_for_promotion']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Current-pending input: `{counts['current_pending_input_rows']}`",
        f"- Non-pin input: `{counts['nonpin_input_rows']}`",
        f"- Strict-ready if calibration passes: `{counts['strict_ready_if_calibrated']}`",
        f"- Held before or during forecast: `{counts['held_rows']}`",
        "",
        "## Candidate Funnel",
        "",
        "| Stage | Rows |",
        "|---|---:|",
        f"| Non-pin input | {counts['nonpin_input_rows']} |",
        f"| Structural ready | {counts['structural_ready_rows']} |",
        f"| Near-region unique | {counts['near_region_unique_rows']} |",
        f"| Strict QA unique | {counts['strict_ready_if_calibrated']} |",
        f"| All holds | {counts['held_rows']} |",
        "",
        "## Projected MicroText Balance",
        "",
        f"- {report['projected_microtext_balance']['current']}",
        "",
        "| Category | Current | Added | Projected | Remaining shortfall |",
        "|---|---:|---:|---:|---:|",
    ]
    current = report["current_category_counts"]
    added = report["strict_ready_categories"]
    projected = report["projected_microtext_balance"]["category_counts"]
    shortfalls = report["projected_microtext_balance"]["category_shortfalls"]
    for category in audit_v2_0_gate.MICROTEXT_CATEGORY_MINIMUMS:
        lines.append(
            f"| `{category}` | {current.get(category, 0)} | "
            f"{added.get(category, 0)} | {projected.get(category, 0)} | "
            f"{shortfalls.get(category, 0)} |"
        )
    lines.extend(["", "## Holds", "", "| Reason | Rows |", "|---|---:|"])
    if report["hold_reasons"]:
        lines.extend(
            f"| `{reason}` | {value} |"
            for reason, value in report["hold_reasons"].items()
        )
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            f"- Strict duplicate collisions with active Gold: "
            f"`{counts['strict_duplicate_collisions_with_active_gold']}`",
            f"- Strict duplicate collisions within this forecast: "
            f"`{counts['strict_duplicate_collisions_within_forecast']}`",
            f"- Distinct forecast representatives retained for held duplicates: "
            f"`{counts['strict_duplicate_collision_targets']}`",
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
        ]
    )
    return "\n".join(lines)


def build_audit(
    *,
    root: Path,
    reuse_report_path: Path,
    output_dir: Path,
    date_label: str,
    expected_reuse_report_sha256: str = "",
    expected_nonpin_rows: int = 0,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    allowed = (root / "derived" / "quality").resolve()
    if output_dir != allowed and allowed not in output_dir.parents:
        raise ValueError("output_dir must be under derived/quality")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True)

    reuse_hash = file_sha256(reuse_report_path)
    binding_issues: list[str] = []
    if expected_reuse_report_sha256 and reuse_hash != expected_reuse_report_sha256.lower():
        binding_issues.append("reuse_report_sha256_mismatch")
    reuse = json.loads(reuse_report_path.read_text(encoding="utf-8"))
    artifact = (reuse.get("artifacts") or {}).get("current_pending_auto_eligible") or {}
    pending_path = resolve(root, clean_text(artifact.get("path")))
    if not pending_path.is_file():
        raise FileNotFoundError(pending_path)
    pending_hash = file_sha256(pending_path)
    if pending_hash != clean_text(artifact.get("sha256")).lower():
        binding_issues.append("current_pending_artifact_sha256_mismatch")
    if reuse.get("active_gold_modified") is not False:
        binding_issues.append("reuse_report_modified_active_gold")
    if int((reuse.get("counts") or {}).get("conflict_rows") or 0):
        binding_issues.append("reuse_report_has_conflicts")

    active_before, active_binding_issues = bound_active_hash_issues(
        root, reuse.get("active_file_hashes_after") or {}
    )
    binding_issues.extend(active_binding_issues)
    all_pending = read_jsonl(pending_path)
    nonpin = [row for row in all_pending if clean_text(row.get("category")) != "pin_label"]
    if expected_nonpin_rows and len(nonpin) != expected_nonpin_rows:
        binding_issues.append(
            f"unexpected_nonpin_count:{len(nonpin)}:{expected_nonpin_rows}"
        )

    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        clean_text(row.get("doc_id")): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    active_items = read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_questions = read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    active_pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    active_visual_questions = read_jsonl(
        root / "visualdiff/annotations/visualdiff_questions.jsonl"
    )
    active_ids = {
        clean_text(row.get("source_candidate_id"))
        for row in active_items
        if row.get("source_candidate_id")
    }
    active_split_map = microtext_merge.load_split_policy(root)

    source_cache: dict[str, list[str]] = {}
    reservation_cache: dict[
        Path, tuple[dict[tuple[str, str], dict[str, Any]], list[str]]
    ] = {}
    image_hash_cache: dict[Path, str] = {}
    seen_ids: set[str] = set()
    unit_splits: dict[tuple[str, str], str] = {}
    structural_ready: list[dict[str, Any]] = []
    structural_holds: list[dict[str, Any]] = []
    hold_reason_counts: Counter[str] = Counter()

    for row in nonpin:
        identity = candidate_id(row)
        reasons = policy_issues(row)
        if identity in seen_ids:
            reasons.append("duplicate_candidate_id")
        seen_ids.add(identity)
        if identity in active_ids:
            reasons.append("candidate_id_already_active")
        reasons.extend(canonical_evidence_issues(row))
        reasons.extend(evidence_file_issues(root, row, image_hash_cache))

        doc_id = clean_text(row.get("doc_id"))
        if doc_id not in source_cache:
            source_cache[doc_id] = source_audit(root, doc_id, docs, inventory)
        reasons.extend(f"source:{reason}" for reason in source_cache[doc_id])
        reasons.extend(source_contract_issues(row, docs.get(doc_id)))
        reasons.extend(reservation_issues(root, row, reservation_cache))
        if doc_id in active_split_map and active_split_map[doc_id] != "train":
            reasons.append(
                f"active_split_conflict:{active_split_map[doc_id]}:train"
            )
        unit = staged.staged_split_unit(row)
        prior_split = unit_splits.setdefault(unit, "train")
        if prior_split != "train":
            reasons.append("cohort_split_conflict")

        reasons = sorted(set(reasons))
        if reasons:
            output = dict(row)
            output["precalibration_hold_reasons"] = reasons
            output["safe_to_merge_gold"] = False
            structural_holds.append(output)
            hold_reason_counts.update(reasons)
        else:
            structural_ready.append(row)

    near_ready, near_holds = partition_near_region_duplicates(
        active_items, structural_ready
    )
    for row in near_holds:
        hold_reason_counts.update(row["precalibration_hold_reasons"])

    split_by_doc = dict(active_split_map)
    for row in near_ready:
        split_by_doc[clean_text(row.get("doc_id"))] = "train"
    forecast_inputs = [forecast_row(row) for row in near_ready]
    merged_items, merged_questions, merge_stats = microtext_merge.merge_review_rows(
        active_items,
        active_questions,
        forecast_inputs,
        split="train",
        reviewed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        split_by_doc=split_by_doc,
        require_split_map=True,
    )
    merge_issues: list[str] = []
    prepared_items = merged_items[len(active_items) :]
    prepared_questions = merged_questions[len(active_questions) :]
    if len(prepared_items) != len(near_ready):
        merge_issues.append(
            f"merge_count_mismatch:{len(prepared_items)}:{len(near_ready)}"
        )
    if int(merge_stats.get("duplicate") or 0):
        merge_issues.append(f"merge_duplicate_rows:{merge_stats['duplicate']}")
    if int(merge_stats.get("missing_split_mapping") or 0):
        merge_issues.append(
            f"merge_missing_split_rows:{merge_stats['missing_split_mapping']}"
        )

    candidate_items_path = output_dir / "forecast_candidate_items.jsonl"
    candidate_questions_path = output_dir / "forecast_candidate_questions.jsonl"
    write_jsonl(candidate_items_path, prepared_items)
    write_jsonl(candidate_questions_path, prepared_questions)
    candidate_unified = unify_dataset.process_microtext(
        root, candidate_items_path, candidate_questions_path
    )
    active_unified = read_jsonl(root / "eng_bench.jsonl")
    kept_item_ids, strict_collisions = select_strict_unique_item_ids(
        active_unified, candidate_unified
    )
    item_to_candidate = {
        clean_text(item.get("item_id")): clean_text(item.get("source_candidate_id"))
        for item in prepared_items
    }
    kept_candidate_ids = {
        item_to_candidate[item_id] for item_id in kept_item_ids if item_id in item_to_candidate
    }
    strict_ready_raw = [
        row for row in near_ready if candidate_id(row) in kept_candidate_ids
    ]
    strict_duplicate_holds: list[dict[str, Any]] = []
    active_unified_ids = {
        clean_text(row.get("id") or row.get("qid"))
        for row in active_unified
        if row.get("id") or row.get("qid")
    }
    for item_id, collision in strict_collisions.items():
        identity = item_to_candidate.get(item_id, "")
        source_row = next(
            (row for row in near_ready if candidate_id(row) == identity), None
        )
        if source_row is None:
            merge_issues.append(f"strict_collision_mapping_missing:{item_id}")
            continue
        output = dict(source_row)
        output["precalibration_hold_reasons"] = ["strict_duplicate_qa_key"]
        output["precalibration_collides_with"] = collision
        output["safe_to_merge_gold"] = False
        strict_duplicate_holds.append(output)
        hold_reason_counts["strict_duplicate_qa_key"] += 1
    duplicate_active_collisions = sum(
        collision in active_unified_ids for collision in strict_collisions.values()
    )
    duplicate_forecast_collisions = (
        len(strict_collisions) - duplicate_active_collisions
    )

    final_items = [
        item for item in prepared_items if clean_text(item.get("item_id")) in kept_item_ids
    ]
    final_item_ids = {clean_text(item.get("item_id")) for item in final_items}
    final_questions = [
        row
        for row in prepared_questions
        if clean_text((row.get("item_ids") or [""])[0]) in final_item_ids
    ]
    combined_items = active_items + final_items
    combined_questions = active_questions + final_questions

    paths = {
        "structural_ready": output_dir / "precalibration_structural_ready.jsonl",
        "structural_holds": output_dir / "precalibration_structural_holds.jsonl",
        "near_region_holds": output_dir / "precalibration_near_region_holds.jsonl",
        "strict_duplicate_holds": output_dir / "precalibration_strict_duplicate_holds.jsonl",
        "strict_ready": output_dir / "precalibration_strict_ready_calibration_pending.jsonl",
        "combined_items": output_dir / "forecast_combined_microtext_items.jsonl",
        "combined_questions": output_dir / "forecast_combined_microtext_questions.jsonl",
        "unified": output_dir / "eng_bench_forecast.jsonl",
    }
    write_jsonl(paths["structural_ready"], structural_ready)
    write_jsonl(paths["structural_holds"], structural_holds)
    write_jsonl(paths["near_region_holds"], near_holds)
    write_jsonl(paths["strict_duplicate_holds"], strict_duplicate_holds)
    write_jsonl(paths["strict_ready"], [readiness_row(row) for row in strict_ready_raw])
    write_jsonl(paths["combined_items"], combined_items)
    write_jsonl(paths["combined_questions"], combined_questions)

    unified = unify_dataset.process_visualdiff(
        root,
        root / "visualdiff/annotations/visualdiff_pairs.jsonl",
        root / "visualdiff/annotations/visualdiff_questions.jsonl",
    ) + unify_dataset.process_microtext(
        root, paths["combined_items"], paths["combined_questions"]
    )
    write_jsonl(paths["unified"], unified)
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict_report, bad_indices = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    annotation_issue_list = annotation_errors(
        active_pairs, active_visual_questions, combined_items, combined_questions
    )
    split_leakage_issues = preview_split_leakage(
        combined_items, active_pairs, docs, manifest_pairs
    )
    question_leakage = audit_question_leakage.audit(unified)

    projected_balance = audit_v2_0_gate.microtext_category_balance(combined_items)
    current_categories = Counter(
        clean_text(row.get("category")) or "unknown" for row in active_items
    )
    strict_categories = Counter(
        clean_text(row.get("category")) or "unknown" for row in strict_ready_raw
    )
    active_after = active_hashes(root)
    active_unchanged = active_before == active_after
    all_holds = structural_holds + near_holds + strict_duplicate_holds
    partition_complete = len(strict_ready_raw) + len(all_holds) == len(nonpin)
    gates = {
        "reuse_and_pending_hash_binding": not binding_issues,
        "merge_forecast": not merge_issues,
        "forecast_partition_complete": partition_complete,
        "annotation_validation": not annotation_issue_list,
        "combined_split_leakage": not split_leakage_issues,
        "strict_unified_validation": not strict_report.errors and not bad_indices,
        "question_answer_leakage": question_leakage.get("critical_failures") == 0,
        "active_files_unchanged": active_unchanged,
    }
    forecast_valid = all(gates.values())
    report = {
        "schema": "eng_bench_nonpin_precalibration_readiness_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_current_gold_forecast",
        "precalibration_forecast_valid": forecast_valid,
        "calibration_required": True,
        "ready_for_promotion": False,
        "safe_to_merge_gold": False,
        "active_gold_modified": not active_unchanged,
        "counts": {
            "current_pending_input_rows": len(all_pending),
            "excluded_pin_rows": len(all_pending) - len(nonpin),
            "nonpin_input_rows": len(nonpin),
            "structural_ready_rows": len(structural_ready),
            "structural_hold_rows": len(structural_holds),
            "near_region_unique_rows": len(near_ready),
            "near_region_holds": len(near_holds),
            "strict_duplicate_holds": len(strict_duplicate_holds),
            "strict_duplicate_collisions_with_active_gold": duplicate_active_collisions,
            "strict_duplicate_collisions_within_forecast": duplicate_forecast_collisions,
            "strict_duplicate_collision_targets": len(set(strict_collisions.values())),
            "strict_ready_if_calibrated": len(strict_ready_raw),
            "held_rows": len(all_holds),
            "projected_gold_rows": len(unified),
            "active_gold_rows": len(active_unified),
            "source_docs_checked": len(source_cache),
        },
        "input_categories": dict(
            sorted(Counter(clean_text(row.get("category")) for row in nonpin).items())
        ),
        "strict_ready_categories": dict(sorted(strict_categories.items())),
        "current_category_counts": dict(sorted(current_categories.items())),
        "projected_microtext_balance": projected_balance,
        "hold_reasons": dict(sorted(hold_reason_counts.items())),
        "binding_issues": binding_issues,
        "merge_issues": merge_issues,
        "merge_stats": dict(sorted(merge_stats.items())),
        "annotation_errors": annotation_issue_list[:100],
        "split_leakage_issues": split_leakage_issues[:100],
        "strict_v2": {
            "errors": strict_report.errors[:100],
            "warnings": strict_report.warnings[:100],
            "bad_rows": len(bad_indices),
            "stats": strict_report.stats,
        },
        "question_leakage": question_leakage,
        "gates": gates,
        "inputs": {
            "reuse_report": display(root, reuse_report_path),
            "reuse_report_sha256": reuse_hash,
            "current_pending": display(root, pending_path),
            "current_pending_sha256": pending_hash,
        },
        "active_file_hashes_before": active_before,
        "active_file_hashes_after": active_after,
        "artifacts": {key: display(root, path) for key, path in paths.items()},
        "artifact_sha256": {key: file_sha256(path) for key, path in paths.items()},
        "interpretation": (
            "Strict-ready rows are only a forecast of rows that survive current Gold's "
            "source, evidence, split, near-region, dedup, annotation, leakage, and strict "
            "validation gates. They remain uncalibrated, unsafe to merge, and ineligible "
            "for promotion until the frozen calibration contract passes and a fresh "
            "promotion preview is rerun."
        ),
    }
    write_json(output_dir / "precalibration_readiness_report.json", report)
    (output_dir / "precalibration_readiness_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reuse-report", type=Path, required=True)
    parser.add_argument("--expected-reuse-report-sha256", default="")
    parser.add_argument("--expected-nonpin-rows", type=int, default=0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_audit(
        root=root,
        reuse_report_path=resolve(root, args.reuse_report),
        output_dir=resolve(root, args.output_dir),
        date_label=args.date_label,
        expected_reuse_report_sha256=args.expected_reuse_report_sha256,
        expected_nonpin_rows=args.expected_nonpin_rows,
    )
    print(
        json.dumps(
            {
                "precalibration_forecast_valid": report[
                    "precalibration_forecast_valid"
                ],
                "nonpin_input_rows": report["counts"]["nonpin_input_rows"],
                "strict_ready_if_calibrated": report["counts"][
                    "strict_ready_if_calibrated"
                ],
                "held_rows": report["counts"]["held_rows"],
                "active_gold_modified": report["active_gold_modified"],
                "gates": report["gates"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["precalibration_forecast_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
