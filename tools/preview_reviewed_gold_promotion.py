#!/usr/bin/env python3
"""Build a read-only, strict promotion preview for reviewed Eng_Bench rows.

The command never mutates active annotations, splits, the manifest, or the
unified Gold file. It produces combined preview artifacts and a release verdict
under derived/quality so maintainers can inspect exactly what would be added.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_question_leakage
import audit_staged_v2_capacity as staged
import microtext_merge
import unify_dataset
import validate_engbench
import validate_engbench_v2
import visualdiff_merge
from audit_active_gold_provenance import (
    file_sha256,
    manifest_maps,
    read_csv,
    resolve_visualdiff_docs,
)
from audit_staged_promotion_contract import image_dimensions, parse_bbox, source_audit
from visualdiff_description_finality import (
    machine_known_description_issue,
    tentative_description_details,
)
from candidate_evidence_holds import evidence_hold_ids, is_evidence_held


FINAL_MICROTEXT = {"accepted", "edited"}
FINAL_VISUALDIFF = {"accepted", "edited", "valid", "edit"}
ACTIVE_PATHS = (
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "eng_bench.jsonl",
    "manifest.jsonl",
    "splits/microtext_train.txt",
    "splits/microtext_dev.txt",
    "splits/microtext_test.txt",
    "splits/visualdiff_train.txt",
    "splits/visualdiff_dev.txt",
    "splits/visualdiff_test.txt",
)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ENGLISH_PROSE_WORDS = {
    "added", "appears", "changed", "deleted", "from", "has", "label",
    "moved", "new", "old", "removed", "replaced", "the", "to", "value",
    "was", "were", "with",
}
MACHINE_CERTIFICATION_METHOD = "machine_verified"
MACHINE_CERTIFICATION_TIER = "auto_gold_train"
MACHINE_CERTIFICATION_POLICY_VERSION = "1.0"


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def write_hold_csv(path: Path, holds: list[dict[str, Any]]) -> None:
    """Write a flat, Excel-friendly correction queue without hiding source data."""
    fields = [
        "task",
        "identity",
        "reasons",
        "source_path",
        "project_id",
        "doc_id",
        "reserved_split",
        "review_status",
        "image_path",
        "image_old",
        "image_new",
        "bbox",
        "bbox_old",
        "bbox_new",
        "current_text",
        "current_category",
        "current_description",
        "current_change_type",
        "corrected_text",
        "corrected_category",
        "corrected_english_description",
        "confirmed_change_type",
        "human_status",
        "reviewer_notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for hold in holds:
            row = hold["row"]
            task = str(hold["task"])
            writer.writerow(
                {
                    "task": task,
                    "identity": hold["identity"],
                    "reasons": ";".join(hold["reasons"]),
                    "source_path": hold["source_path"],
                    "project_id": row.get("project_id") or "",
                    "doc_id": row.get("doc_id") or row.get("source_doc_id") or "",
                    "reserved_split": row.get("reserved_split") or "",
                    "review_status": status_for(row, task),
                    "image_path": row.get("image_path") or "",
                    "image_old": row.get("image_old") or "",
                    "image_new": row.get("image_new") or "",
                    "bbox": json.dumps(row.get("bbox"), ensure_ascii=False),
                    "bbox_old": json.dumps(row.get("bbox_old"), ensure_ascii=False),
                    "bbox_new": json.dumps(row.get("bbox_new"), ensure_ascii=False),
                    "current_text": microtext_merge.answer_text(row) if task == "microtext" else "",
                    "current_category": row.get("corrected_category") or row.get("category") or "",
                    "current_description": visualdiff_merge.description(row) if task == "visualdiff" else "",
                    "current_change_type": json.dumps(
                        row.get("human_change_type") or row.get("change_type"),
                        ensure_ascii=False,
                    ),
                    "corrected_text": "",
                    "corrected_category": "",
                    "corrected_english_description": "",
                    "confirmed_change_type": "",
                    "human_status": "",
                    "reviewer_notes": "",
                }
            )


def visualdiff_description_requires_english_localization(description: str) -> bool:
    """Hold CJK text unless clear English prose surrounds the quoted labels."""
    if not CJK_RE.search(description):
        return False
    words = set(re.findall(r"[A-Za-z]+", description.lower()))
    return len(words & ENGLISH_PROSE_WORDS) < 2


def status_for(row: dict[str, Any], task: str) -> str:
    fields = (
        ("human_review_status", "human_status", "review_status", "status")
        if task == "visualdiff"
        else ("review_status", "human_review_status", "status")
    )
    return next(
        (
            str(row.get(field) or "").strip().lower()
            for field in fields
            if str(row.get(field) or "").strip()
        ),
        "",
    )


def identity_for(row: dict[str, Any], task: str) -> str:
    fields = ("pair_id", "id") if task == "visualdiff" else (
        "candidate_id",
        "item_id",
        "id",
    )
    return next((str(row.get(field) or "").strip() for field in fields if row.get(field)), "")


def machine_certification_reasons(
    root: Path,
    row: dict[str, Any],
    planned_split: str,
    artifact_cache: dict[Path, tuple[str, dict[str, Any] | None]],
) -> list[str]:
    method = str(row.get("certification_method") or "").strip().lower()
    if not method:
        return []
    reasons: list[str] = []
    if method != MACHINE_CERTIFICATION_METHOD:
        return [f"unsupported_certification_method:{method}"]
    if planned_split != "train":
        reasons.append("machine_certification_is_train_only")
    if str(row.get("certification_tier") or "") != MACHINE_CERTIFICATION_TIER:
        reasons.append("invalid_machine_certification_tier")
    if str(row.get("certification_policy_version") or "") != MACHINE_CERTIFICATION_POLICY_VERSION:
        reasons.append("invalid_machine_certification_policy_version")
    if row.get("human_reviewed") is not False:
        reasons.append("machine_certification_human_reviewed_flag_must_be_false")
    if str(row.get("review_source") or "") != "machine_certification_policy":
        reasons.append("invalid_machine_certification_review_source")
    for field in (
        "machine_certification_evidence_sha256",
        "certification_eligibility_report_sha256",
        "certification_calibration_checklist_sha256",
        "certification_calibration_attestation_sha256",
    ):
        value = str(row.get(field) or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            reasons.append(f"missing_or_invalid_{field}")
    for path_field, hash_field, kind in (
        ("certification_eligibility_report", "certification_eligibility_report_sha256", "eligibility"),
        ("certification_calibration_attestation", "certification_calibration_attestation_sha256", "attestation"),
    ):
        path_text = str(row.get(path_field) or "").strip()
        expected_hash = str(row.get(hash_field) or "").strip().lower()
        path = resolve(root, Path(path_text)) if path_text else None
        if path is None or not path.is_file():
            reasons.append(f"missing_machine_certification_{kind}_artifact")
            continue
        if path not in artifact_cache:
            actual_hash = file_sha256(path).lower()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            artifact_cache[path] = (actual_hash, payload)
        actual_hash, payload = artifact_cache[path]
        if actual_hash != expected_hash:
            reasons.append(f"machine_certification_{kind}_sha256_mismatch")
        if not isinstance(payload, dict):
            reasons.append(f"invalid_machine_certification_{kind}_artifact")
        elif kind == "attestation":
            if payload.get("release_ready") is not True:
                reasons.append("machine_certification_attestation_not_release_ready")
            if str(payload.get("policy_version") or "") != MACHINE_CERTIFICATION_POLICY_VERSION:
                reasons.append("machine_certification_attestation_policy_mismatch")
            if int(payload.get("incorrect") or 0) or int(payload.get("unclear") or 0):
                reasons.append("machine_certification_attestation_has_errors")
            if float(payload.get("one_sided_precision_lower_bound") or 0.0) < 0.99:
                reasons.append("machine_certification_attestation_precision_too_low")
    return reasons


def active_hashes(root: Path) -> dict[str, str]:
    return {
        value: file_sha256(root / value)
        for value in ACTIVE_PATHS
        if (root / value).is_file()
    }


def reservation_records(path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    issues: list[str] = []
    if payload.get("valid") is not True:
        issues.append("split_plan_not_valid")
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for row in payload.get("reservations") or []:
        key = (str(row.get("task") or "").strip(), str(row.get("unit_id") or "").strip())
        split = str(row.get("split") or "").strip().lower()
        if not all(key) or split not in {"train", "dev", "test"}:
            issues.append(f"invalid_split_reservation:{key[0]}:{key[1]}:{split}")
            continue
        if key in records and str(records[key].get("split")) != split:
            issues.append(f"conflicting_split_reservation:{key[0]}:{key[1]}")
            continue
        records[key] = row
    return records, issues


def valid_legacy_reservation(root: Path, row: dict[str, Any], unit: tuple[str, str], split: str) -> bool:
    plan_text = str(row.get("split_reservation_plan") or "").strip()
    reservation_id = str(row.get("split_reservation_id") or "").strip()
    if not plan_text or not reservation_id:
        return False
    plan_path = resolve(root, Path(plan_text))
    if not plan_path.is_file():
        return False
    records, issues = reservation_records(plan_path)
    record = records.get(unit)
    return bool(
        not issues
        and record
        and str(record.get("reservation_id") or "") == reservation_id
        and str(record.get("split") or "").strip().lower() == split
    )


def evidence_issues(root: Path, row: dict[str, Any], task: str) -> list[str]:
    fields = (
        (("image_old", "bbox_old"), ("image_new", "bbox_new"))
        if task == "visualdiff"
        else (("image_path", "bbox"),)
    )
    issues: list[str] = []
    for image_field, bbox_field in fields:
        image_text = str(row.get(image_field) or "").strip()
        image_path = resolve(root, Path(image_text)) if image_text else None
        bbox = parse_bbox(row.get(bbox_field))
        if image_path is None or not image_path.is_file():
            issues.append(f"missing_evidence_image:{image_field}")
            continue
        if bbox is None:
            issues.append(f"invalid_evidence_bbox:{bbox_field}")
            continue
        size = image_dimensions(image_path)
        if size is None:
            issues.append(f"unreadable_evidence_image:{image_field}")
        elif bbox[0] < 0 or bbox[1] < 0 or bbox[2] > size[0] or bbox[3] > size[1]:
            issues.append(f"bbox_out_of_frame:{bbox_field}:{bbox}:{size}")
    return issues


def split_maps(
    root: Path,
    reservations: dict[tuple[str, str], dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    micro = microtext_merge.load_split_policy(root)
    visual = visualdiff_merge.read_split_map(root)
    issues: list[str] = []
    for (task, unit_id), row in reservations.items():
        split = str(row.get("split") or "").strip().lower()
        target = visual if task == "visualdiff" else micro if task == "microtext" else None
        if target is None:
            issues.append(f"unknown_reservation_task:{task}:{unit_id}")
            continue
        if unit_id in target and target[unit_id] != split:
            issues.append(
                f"active_split_conflict:{task}:{unit_id}:{target[unit_id]}:{split}"
            )
        target[unit_id] = split
    return micro, visual, issues


def annotation_errors(
    pairs: list[dict[str, Any]],
    visual_questions: list[dict[str, Any]],
    items: list[dict[str, Any]],
    micro_questions: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_engbench.validate_visualdiff_pairs(pairs))
    errors.extend(
        validate_engbench.validate_visualdiff_questions(
            visual_questions, {str(row.get("pair_id") or "") for row in pairs}
        )
    )
    errors.extend(validate_engbench.validate_microtext_items(items))
    errors.extend(
        validate_engbench.validate_microtext_questions(
            micro_questions, {str(row.get("item_id") or "") for row in items}
        )
    )
    return errors


def preview_split_leakage(
    items: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    manifest_pairs: dict[str, dict[str, Any]],
) -> list[str]:
    assignments: list[tuple[str, str, dict[str, Any]]] = []
    for row in items:
        doc_id = str(row.get("doc_id") or "").strip()
        if doc_id in docs:
            assignments.append((str(row.get("split") or ""), doc_id, docs[doc_id]))
    for row in pairs:
        doc_ids, resolution_issue = resolve_visualdiff_docs(row, docs, manifest_pairs)
        if resolution_issue:
            continue
        for doc_id in doc_ids:
            if doc_id in docs:
                assignments.append((str(row.get("split") or ""), doc_id, docs[doc_id]))

    issues: list[str] = []
    for field in ("doc_id", "sha256", "source_candidate_id", "same_model_id", "source_url"):
        grouped: dict[str, set[str]] = {}
        evidence: dict[str, set[str]] = {}
        for split, doc_id, doc in assignments:
            value = doc_id if field == "doc_id" else str(doc.get(field) or "").strip()
            if not value:
                continue
            grouped.setdefault(value, set()).add(split)
            evidence.setdefault(value, set()).add(doc_id)
        for value, splits in grouped.items():
            if len(splits) > 1:
                issues.append(
                    f"{field}_leakage:{value}:{','.join(sorted(splits))}:"
                    f"{','.join(sorted(evidence[value]))}"
                )
    return sorted(issues)


def build_preview(
    root: Path,
    micro_paths: list[Path],
    visual_paths: list[Path],
    split_plan_path: Path,
    output_dir: Path,
    date_label: str,
    *,
    final_rows_only: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = resolve(root, output_dir).resolve()
    allowed_root = (root / "derived" / "quality").resolve()
    if output_dir != allowed_root and allowed_root not in output_dir.parents:
        raise ValueError("output_dir must be under derived/quality")
    output_dir.mkdir(parents=True, exist_ok=True)
    before_hashes = active_hashes(root)
    machine_evidence_holds = evidence_hold_ids(root)

    split_plan_path = resolve(root, split_plan_path).resolve()
    reservations, reservation_issues = reservation_records(split_plan_path)
    micro_splits, visual_splits, split_issues = split_maps(root, reservations)
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or ""): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }

    input_rows: list[tuple[str, str, dict[str, Any]]] = []
    scanned_input_rows = 0
    ignored_nonfinal_rows = 0
    for task, paths in (("microtext", micro_paths), ("visualdiff", visual_paths)):
        for raw_path in paths:
            path = resolve(root, raw_path)
            for row in read_jsonl(path):
                scanned_input_rows += 1
                if final_rows_only:
                    final_statuses = FINAL_VISUALDIFF if task == "visualdiff" else FINAL_MICROTEXT
                    if status_for(row, task) not in final_statuses:
                        ignored_nonfinal_rows += 1
                        continue
                input_rows.append((task, path.as_posix(), row))

    holds: list[dict[str, Any]] = []
    eligible_micro: list[dict[str, Any]] = []
    eligible_visual: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    seen_identities: set[str] = set()
    source_cache: dict[str, list[str]] = {}
    machine_certification_artifact_cache: dict[Path, tuple[str, dict[str, Any] | None]] = {}

    for task, source_path, row in input_rows:
        status = status_for(row, task)
        identity = identity_for(row, task)
        status_counts[f"{task}:{status or 'blank'}"] += 1
        reasons: list[str] = []
        if is_evidence_held(row, machine_evidence_holds):
            reasons.append("unresolved_machine_evidence_hold")
        final_statuses = FINAL_VISUALDIFF if task == "visualdiff" else FINAL_MICROTEXT
        if status not in final_statuses:
            reasons.append(f"nonfinal_review_status:{status or 'blank'}")
        if not identity:
            reasons.append("missing_identity")
        elif f"{task}:{identity}" in seen_identities:
            reasons.append("duplicate_return_identity")
        else:
            seen_identities.add(f"{task}:{identity}")
        if row.get("safe_to_merge_gold") is not False:
            reasons.append("pre_review_safety_flag_must_be_false")

        unit = staged.staged_split_unit(row)
        reservation = reservations.get(unit)
        planned_split = str((reservation or {}).get("split") or "").strip().lower()
        if reservation is None:
            reasons.append(f"missing_split_reservation:{unit[0]}:{unit[1]}")
        else:
            row_split = str(row.get("reserved_split") or "").strip().lower()
            if row_split and row_split != planned_split:
                reasons.append(f"reserved_split_conflict:{row_split}:{planned_split}")
            row_reservation = str(row.get("split_reservation_id") or "").strip()
            planned_reservation = str(reservation.get("reservation_id") or "").strip()
            if row_reservation and row_reservation != planned_reservation and not valid_legacy_reservation(
                root, row, unit, planned_split
            ):
                reasons.append("unverifiable_split_reservation_id")
        reasons.extend(evidence_issues(root, row, task))

        if task == "microtext":
            category = str(row.get("corrected_category") or row.get("category") or "").strip()
            text = microtext_merge.answer_text(row)
            if category in {"", "unknown", "unknown_microtext"}:
                reasons.append("unresolved_microtext_category")
            if not text:
                reasons.append("missing_microtext_answer")
            reasons.extend(
                machine_certification_reasons(
                    root,
                    row,
                    planned_split,
                    machine_certification_artifact_cache,
                )
            )
            doc_ids = [str(row.get("doc_id") or "").strip()]
        else:
            if visualdiff_merge.normalized_change_type(row) == ["unknown"]:
                reasons.append("unresolved_visualdiff_change_type")
            description = visualdiff_merge.description(row)
            if not description or description == visualdiff_merge.TODO_DESCRIPTION:
                reasons.append("missing_visualdiff_description")
            elif tentative_description_details(description):
                reasons.append("tentative_visualdiff_description")
            elif visualdiff_description_requires_english_localization(description):
                reasons.append("visualdiff_description_requires_english_localization")
            else:
                description_issue = machine_known_description_issue(
                    description,
                    desc_source=str(row.get("desc_source") or ""),
                )
                if description_issue in {
                    "unvalidated_machine_visual",
                    "generic_machine_description",
                }:
                    reasons.append("generic_visualdiff_description")
            doc_ids, resolution_issue = resolve_visualdiff_docs(row, docs, manifest_pairs)
            if resolution_issue:
                reasons.append(f"visualdiff_manifest_resolution:{resolution_issue}")

        for doc_id in filter(None, doc_ids):
            if doc_id not in source_cache:
                source_cache[doc_id] = source_audit(root, doc_id, docs, inventory)
            reasons.extend(f"source:{doc_id}:{issue}" for issue in source_cache[doc_id])
        if not any(doc_ids):
            reasons.append("missing_source_doc_id")

        if reasons:
            holds.append(
                {
                    "task": task,
                    "identity": identity,
                    "source_path": source_path,
                    "reasons": sorted(dict.fromkeys(reasons)),
                    "row": row,
                }
            )
            continue
        prepared = dict(row)
        prepared["reserved_split"] = planned_split
        if task == "microtext":
            eligible_micro.append(prepared)
        else:
            eligible_visual.append(prepared)

    active_items = read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_micro_questions = read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    active_pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    active_visual_questions = read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")

    merged_items, merged_micro_questions, micro_stats = microtext_merge.merge_review_rows(
        active_items,
        active_micro_questions,
        eligible_micro,
        split="dev",
        reviewed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        split_by_doc=micro_splits,
        require_split_map=True,
    )
    merged_pairs, merged_visual_questions, accepted_visual, visual_report = (
        visualdiff_merge.merge_reviewed_rows(
            active_pairs,
            active_visual_questions,
            eligible_visual,
            visual_splits,
            manifest_rows,
        )
    )
    accepted_micro_count = int(micro_stats.get("accepted", 0))
    merge_issues: list[str] = []
    if accepted_micro_count != len(eligible_micro):
        merge_issues.append(
            f"microtext_merge_count_mismatch:{accepted_micro_count}:{len(eligible_micro)}"
        )
    if len(accepted_visual) != len(eligible_visual):
        merge_issues.append(
            f"visualdiff_merge_count_mismatch:{len(accepted_visual)}:{len(eligible_visual)}"
        )
    merge_issues.extend(
        f"visualdiff_hold:{row.get('pair_id')}:{','.join(row.get('reasons') or [])}"
        for row in visual_report.get("held_rows") or []
    )

    paths = {
        "combined_microtext_items": output_dir / "combined_microtext_items.jsonl",
        "combined_microtext_questions": output_dir / "combined_microtext_questions.jsonl",
        "combined_visualdiff_pairs": output_dir / "combined_visualdiff_pairs.jsonl",
        "combined_visualdiff_questions": output_dir / "combined_visualdiff_questions.jsonl",
        "prepared_microtext_items": output_dir / "prepared_microtext_items.jsonl",
        "prepared_visualdiff_pairs": output_dir / "prepared_visualdiff_pairs.jsonl",
        "holds": output_dir / "promotion_holds.jsonl",
        "holds_csv": output_dir / "promotion_holds_for_human.csv",
        "unified": output_dir / "eng_bench_preview.jsonl",
    }
    prepared_items = merged_items[len(active_items) :]
    write_jsonl(paths["combined_microtext_items"], merged_items)
    write_jsonl(paths["combined_microtext_questions"], merged_micro_questions)
    write_jsonl(paths["combined_visualdiff_pairs"], merged_pairs)
    write_jsonl(paths["combined_visualdiff_questions"], merged_visual_questions)
    write_jsonl(paths["prepared_microtext_items"], prepared_items)
    write_jsonl(paths["prepared_visualdiff_pairs"], accepted_visual)
    write_jsonl(paths["holds"], holds)
    write_hold_csv(paths["holds_csv"], holds)

    annotation_issue_list = annotation_errors(
        merged_pairs, merged_visual_questions, merged_items, merged_micro_questions
    )
    split_leakage_issues = preview_split_leakage(
        merged_items, merged_pairs, docs, manifest_pairs
    )
    unified = unify_dataset.process_visualdiff(
        root, paths["combined_visualdiff_pairs"], paths["combined_visualdiff_questions"]
    ) + unify_dataset.process_microtext(
        root, paths["combined_microtext_items"], paths["combined_microtext_questions"]
    )
    write_jsonl(paths["unified"], unified)
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict_report, bad_indices = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    leakage_report = audit_question_leakage.audit(unified)
    after_hashes = active_hashes(root)
    active_unchanged = before_hashes == after_hashes

    issue_counts = Counter(reason for hold in holds for reason in hold["reasons"])
    gates = {
        "split_plan": not reservation_issues and not split_issues,
        "all_input_rows_final_and_eligible": not holds,
        "all_eligible_rows_merged": not merge_issues,
        "annotation_validation": not annotation_issue_list,
        "combined_split_leakage": not split_leakage_issues,
        "strict_unified_validation": not strict_report.errors and not bad_indices,
        "question_answer_leakage": leakage_report["critical_failures"] == 0,
        "active_files_unchanged": active_unchanged,
    }
    ready = bool(input_rows) and all(gates.values())
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_preview",
        "selection_mode": "final_rows_only" if final_rows_only else "all_input_rows",
        "ready_for_apply": ready,
        "active_gold_modified": not active_unchanged,
        "split_plan": split_plan_path.relative_to(root).as_posix(),
        "split_plan_sha256": file_sha256(split_plan_path),
        "inputs": {
            "microtext": [resolve(root, path).as_posix() for path in micro_paths],
            "visualdiff": [resolve(root, path).as_posix() for path in visual_paths],
        },
        "counts": {
            "scanned_input_rows": scanned_input_rows,
            "ignored_nonfinal_rows": ignored_nonfinal_rows,
            "input_rows": len(input_rows),
            "eligible_microtext_rows": len(eligible_micro),
            "eligible_visualdiff_rows": len(eligible_visual),
            "prepared_microtext_rows": len(prepared_items),
            "prepared_visualdiff_rows": len(accepted_visual),
            "held_rows": len(holds),
            "combined_gold_rows": len(unified),
            "active_gold_rows": len(active_items) + len(active_pairs),
        },
        "review_statuses": dict(sorted(status_counts.items())),
        "hold_reasons": dict(sorted(issue_counts.items())),
        "split_plan_issues": [*reservation_issues, *split_issues],
        "merge_issues": merge_issues,
        "microtext_merge_stats": dict(sorted(micro_stats.items())),
        "visualdiff_merge_counts": visual_report.get("counts") or {},
        "annotation_errors": annotation_issue_list[:100],
        "split_leakage_issues": split_leakage_issues[:100],
        "strict_v2": {
            "errors": strict_report.errors[:100],
            "warnings": strict_report.warnings[:100],
            "stats": strict_report.stats,
            "bad_rows": len(bad_indices),
        },
        "question_leakage": leakage_report,
        "gates": gates,
        "active_file_hashes_before": before_hashes,
        "active_file_hashes_after": after_hashes,
        "artifacts": {
            key: path.relative_to(root).as_posix() for key, path in paths.items()
        },
        "artifact_sha256": {key: file_sha256(path) for key, path in paths.items()},
        "interpretation": (
            "ready_for_apply means this exact reviewed batch survives a read-only combined-Gold "
            "preview. It does not apply rows. A later explicit apply still requires a fresh snapshot "
            "and rerunning all release gates against the resulting active files."
        ),
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Reviewed Gold Promotion Preview",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Ready for apply: `{str(report['ready_for_apply']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Input rows: `{counts['input_rows']}`",
        f"- Prepared rows: `{counts['prepared_microtext_rows'] + counts['prepared_visualdiff_rows']}`",
        f"- Held rows: `{counts['held_rows']}`",
        f"- Combined preview rows: `{counts['combined_gold_rows']}`",
        "",
        "## Gates",
        "",
        "| Gate | Pass |",
        "|---|---|",
    ]
    lines.extend(f"| `{key}` | `{str(value).lower()}` |" for key, value in report["gates"].items())
    lines.extend(["", "## Hold Reasons", "", "| Reason | Rows |", "|---|---:|"])
    if report["hold_reasons"]:
        lines.extend(f"| `{key}` | {value} |" for key, value in report["hold_reasons"].items())
    else:
        lines.append("| none | 0 |")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    if report["counts"]["held_rows"]:
        lines.extend(
            [
                "## Human Correction Queue",
                "",
                f"Open `{report['artifacts']['holds_csv']}` in Excel.",
                "Do not edit task, identity, reason, evidence, source, or split columns.",
                "For each row, set `human_status` to `edited`, `rejected`, or `needs_full_page`.",
                "For `visualdiff_description_requires_english_localization`, fill a concise, visually verified `corrected_english_description`.",
                "For `unresolved_visualdiff_change_type`, fill `confirmed_change_type` with one or more of: addition, deletion, layout, symbol, text, value.",
                "Use `reviewer_notes` for the evidence or rejection reason. Return the completed CSV for non-destructive import and another strict preview.",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--microtext-reviewed", action="append", type=Path, default=[])
    parser.add_argument("--visualdiff-reviewed", action="append", type=Path, default=[])
    parser.add_argument("--split-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument(
        "--final-rows-only",
        action="store_true",
        help="Select only final reviewed rows from a mixed queue and report all ignored rows.",
    )
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args(argv)
    if not args.microtext_reviewed and not args.visualdiff_reviewed:
        parser.error("at least one reviewed input is required")
    root = args.root.resolve()
    report = build_preview(
        root,
        args.microtext_reviewed,
        args.visualdiff_reviewed,
        args.split_plan,
        args.output_dir,
        args.date_label,
        final_rows_only=args.final_rows_only,
    )
    output_dir = resolve(root, args.output_dir)
    write_json(output_dir / "promotion_preview_report.json", report)
    (output_dir / "promotion_preview_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ready_for_apply": report["ready_for_apply"],
                "active_gold_modified": report["active_gold_modified"],
                "counts": report["counts"],
                "hold_reasons": report["hold_reasons"],
                "gates": report["gates"],
            },
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 1 if args.require_ready and not report["ready_for_apply"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
