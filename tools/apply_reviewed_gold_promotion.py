#!/usr/bin/env python3
"""Atomically apply a cryptographically pinned, green reviewed-Gold preview.

The command is a dry run unless ``--apply`` is supplied. It never accepts raw
human review files: the only promotable input is a
``preview_reviewed_gold_promotion.py`` report whose hash is provided by the
maintainer and whose ``ready_for_apply`` verdict is true. Apply mode snapshots
all active release files, stages every replacement, validates the staged
release, and restores the snapshot if any post-write check fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
SPLITS_DIR = ROOT_DIR / "splits"
for module_dir in (TOOLS_DIR, SPLITS_DIR):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

import audit_active_gold_provenance
import audit_question_leakage
import leakage_check
import unify_dataset
import validate_engbench_v2
import visualdiff_merge
from visualdiff_description_finality import (
    definitive_description,
    machine_known_description_issue,
    tentative_description_details,
)
from candidate_evidence_holds import evidence_hold_ids, is_evidence_held
from preview_reviewed_gold_promotion import (
    ACTIVE_PATHS,
    FINAL_MICROTEXT,
    FINAL_VISUALDIFF,
    MACHINE_CERTIFICATION_METHOD,
    MACHINE_CERTIFICATION_POLICY_VERSION,
    MACHINE_CERTIFICATION_TIER,
    annotation_errors,
    file_sha256,
    manifest_maps,
    preview_split_leakage,
    read_jsonl,
)


ANNOTATION_ARTIFACTS = {
    "combined_microtext_items": "microtext/annotations/microtext_items.jsonl",
    "combined_microtext_questions": "microtext/annotations/microtext_questions.jsonl",
    "combined_visualdiff_pairs": "visualdiff/annotations/visualdiff_pairs.jsonl",
    "combined_visualdiff_questions": "visualdiff/annotations/visualdiff_questions.jsonl",
    "unified": "eng_bench.jsonl",
}
MACHINE_EPISTEMIC_DESC_SOURCE = "human_semantics_machine_epistemic_normalized"
MACHINE_EPISTEMIC_METHOD = "machine_epistemic_normalization"
MACHINE_RECONCILED_DESC_SOURCE = "human_semantics_machine_visual_reconciled"
MACHINE_RECONCILIATION_METHOD = "machine_visual_reconciliation"
MUTABLE_PATHS = (
    *ANNOTATION_ARTIFACTS.values(),
    "splits/microtext_train.txt",
    "splits/microtext_dev.txt",
    "splits/microtext_test.txt",
    "splits/visualdiff_train.txt",
    "splits/visualdiff_dev.txt",
    "splits/visualdiff_test.txt",
)
SPLITS = ("train", "dev", "test")
LOCALIZED_HUMAN_DESC_SOURCE = "human_semantics_machine_localized"
LOCALIZED_HUMAN_METHOD = "machine_translation_and_visual_reconciliation"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def require_under(root: Path, path: Path, relative_parent: str, label: str) -> Path:
    resolved = resolve(root, path).resolve()
    allowed = (root / relative_parent).resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"{label} must be under {relative_parent}")
    return resolved


def load_preview_report(
    root: Path,
    report_path: Path,
    expected_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    path = require_under(root, report_path, "derived/quality", "preview report")
    if not path.is_file():
        raise ValueError(f"preview report does not exist: {path}")
    expected = expected_sha256.strip().lower()
    actual = file_sha256(path).lower()
    if not expected or actual != expected:
        raise ValueError(f"preview_report_sha256_mismatch:{actual}:{expected or 'blank'}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("mode") != "read_only_preview":
        raise ValueError("preview_report_mode_is_not_read_only_preview")
    if report.get("ready_for_apply") is not True:
        raise ValueError("preview_report_not_ready_for_apply")
    if report.get("active_gold_modified") is not False:
        raise ValueError("preview_report_says_active_gold_was_modified")
    gates = report.get("gates") or {}
    if not gates or not all(value is True for value in gates.values()):
        raise ValueError("preview_report_has_nonpassing_gate")
    if int((report.get("counts") or {}).get("held_rows") or 0):
        raise ValueError("preview_report_contains_held_rows")
    if report.get("active_file_hashes_after") != report.get("active_file_hashes_before"):
        raise ValueError("preview_active_before_after_hashes_differ")
    prepared = sum(
        int((report.get("counts") or {}).get(key) or 0)
        for key in ("prepared_microtext_rows", "prepared_visualdiff_rows")
    )
    if prepared <= 0:
        raise ValueError("preview_report_has_no_prepared_rows")
    return path, report


def verify_active_prestate(root: Path, report: dict[str, Any]) -> dict[str, str]:
    expected = report.get("active_file_hashes_before") or {}
    missing = sorted(set(ACTIVE_PATHS) - set(expected))
    if missing:
        raise ValueError("preview_missing_active_hashes:" + ",".join(missing))
    actual: dict[str, str] = {}
    for relative in ACTIVE_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"active_file_missing:{relative}")
        actual[relative] = file_sha256(path).lower()
        if actual[relative] != str(expected[relative]).lower():
            raise ValueError(
                f"active_file_changed_since_preview:{relative}:"
                f"{actual[relative]}:{str(expected[relative]).lower()}"
            )
    return actual


def resolve_artifacts(root: Path, report: dict[str, Any]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    artifacts = report.get("artifacts") or {}
    hashes = report.get("artifact_sha256") or {}
    required = set(ANNOTATION_ARTIFACTS) | {
        "prepared_microtext_items",
        "prepared_visualdiff_pairs",
        "holds",
    }
    missing = sorted(required - set(artifacts))
    if missing:
        raise ValueError("preview_missing_artifacts:" + ",".join(missing))
    for key in required:
        path = require_under(root, Path(str(artifacts[key])), "derived/quality", f"artifact {key}")
        if not path.is_file():
            raise ValueError(f"preview_artifact_missing:{key}:{path}")
        actual = file_sha256(path).lower()
        expected = str(hashes.get(key) or "").lower()
        if not expected or actual != expected:
            raise ValueError(f"preview_artifact_sha256_mismatch:{key}:{actual}:{expected or 'blank'}")
        paths[key] = path
    if read_jsonl(paths["holds"]):
        raise ValueError("preview_holds_artifact_is_not_empty")
    return paths


def require_additive_rows(
    active: list[dict[str, Any]],
    combined: list[dict[str, Any]],
    prepared: list[dict[str, Any]],
    *,
    label: str,
) -> None:
    if len(combined) != len(active) + len(prepared):
        raise ValueError(f"{label}_count_is_not_additive")
    if combined[: len(active)] != active:
        raise ValueError(f"{label}_rewrites_or_reorders_active_rows")
    if combined[len(active) :] != prepared:
        raise ValueError(f"{label}_prepared_suffix_mismatch")


def validate_prepared_rows(
    prepared_micro: list[dict[str, Any]],
    prepared_visual: list[dict[str, Any]],
    *,
    machine_evidence_holds: set[str] | None = None,
) -> None:
    issues: list[str] = []
    for row in prepared_micro + prepared_visual:
        if is_evidence_held(row, machine_evidence_holds or set()):
            issues.append("unresolved_machine_evidence_hold:" + str(row.get("item_id") or row.get("pair_id")))
    for row in prepared_micro:
        identity = str(row.get("item_id") or "").strip()
        status = str(row.get("review_status") or "").strip().lower()
        category = str(row.get("category") or "").strip().lower()
        if not identity:
            issues.append("microtext:missing_item_id")
        if status not in FINAL_MICROTEXT:
            issues.append(f"microtext:{identity}:nonfinal_status:{status or 'blank'}")
        if not str(row.get("text_gt") or "").strip():
            issues.append(f"microtext:{identity}:missing_text_gt")
        if category in {"", "unknown", "unknown_microtext"}:
            issues.append(f"microtext:{identity}:unresolved_category")
        if not str(row.get("doc_id") or "").strip():
            issues.append(f"microtext:{identity}:missing_doc_id")
        method = str(row.get("certification_method") or "").strip().lower()
        if method:
            if method != MACHINE_CERTIFICATION_METHOD:
                issues.append(f"microtext:{identity}:unsupported_certification_method:{method}")
            if str(row.get("split") or "").strip().lower() != "train":
                issues.append(f"microtext:{identity}:machine_certification_not_train")
            if str(row.get("certification_tier") or "") != MACHINE_CERTIFICATION_TIER:
                issues.append(f"microtext:{identity}:machine_certification_tier_invalid")
            if str(row.get("certification_policy_version") or "") != MACHINE_CERTIFICATION_POLICY_VERSION:
                issues.append(f"microtext:{identity}:machine_certification_policy_invalid")
            if row.get("human_reviewed") is not False:
                issues.append(f"microtext:{identity}:machine_human_reviewed_flag_invalid")
            if str(row.get("review_source") or "") != "machine_certification_policy":
                issues.append(f"microtext:{identity}:machine_review_source_invalid")
            for field in (
                "machine_certification_evidence_sha256",
                "certification_eligibility_report_sha256",
                "certification_calibration_attestation_sha256",
            ):
                value = str(row.get(field) or "").strip().lower()
                if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                    issues.append(f"microtext:{identity}:invalid_{field}")
    for row in prepared_visual:
        identity = str(row.get("pair_id") or "").strip()
        status = str(
            row.get("human_review_status") or row.get("review_status") or ""
        ).strip().lower()
        change_types = row.get("change_type")
        normalized = (
            [str(value).strip().lower() for value in change_types]
            if isinstance(change_types, list)
            else visualdiff_merge.normalized_change_type(row)
        )
        if not identity:
            issues.append("visualdiff:missing_pair_id")
        if status not in FINAL_VISUALDIFF:
            issues.append(f"visualdiff:{identity}:nonfinal_status:{status or 'blank'}")
        if not str(row.get("change_desc_gt") or "").strip():
            issues.append(f"visualdiff:{identity}:missing_description")
        elif tentative_description_details(str(row["change_desc_gt"])):
            issues.append(f"visualdiff:{identity}:tentative_visualdiff_description")
        elif machine_known_description_issue(
            str(row["change_desc_gt"]),
            desc_source=str(row.get("desc_source") or ""),
        ) in {"unvalidated_machine_visual", "generic_machine_description"}:
            issues.append(f"visualdiff:{identity}:generic_visualdiff_description")
        if not normalized or "unknown" in normalized:
            issues.append(f"visualdiff:{identity}:unresolved_change_type")
        desc_source = str(row.get("desc_source") or "").strip().lower()
        if desc_source == LOCALIZED_HUMAN_DESC_SOURCE:
            evidence = row.get("review_evidence")
            if not isinstance(evidence, dict):
                issues.append(f"visualdiff:{identity}:localized_human_evidence_missing")
            else:
                original = str(evidence.get("original_human_description") or "").strip()
                localized = str(evidence.get("localized_human_description") or "").strip()
                method = str(evidence.get("localization_method") or "").strip()
                sheet = str(evidence.get("reconciliation_evidence_sheet") or "").strip()
                if not original or not contains_cjk(original):
                    issues.append(f"visualdiff:{identity}:localized_original_human_description_invalid")
                if (
                    not localized
                    or contains_cjk(localized)
                    or localized != str(row.get("change_desc_gt") or "").strip()
                ):
                    issues.append(f"visualdiff:{identity}:localized_description_invalid")
                if method != LOCALIZED_HUMAN_METHOD:
                    issues.append(f"visualdiff:{identity}:localized_method_invalid")
                if not sheet:
                    issues.append(f"visualdiff:{identity}:localized_evidence_sheet_missing")
                provenance = evidence.get("localization_provenance")
                if provenance is not None:
                    if not isinstance(provenance, dict):
                        issues.append(f"visualdiff:{identity}:localized_provenance_invalid")
                    else:
                        source_hash = str(provenance.get("source_input_sha256") or "").lower()
                        artifact = provenance.get("evidence_artifact")
                        support = provenance.get("independent_audit_support")
                        if provenance.get("method") != LOCALIZED_HUMAN_METHOD:
                            issues.append(f"visualdiff:{identity}:localized_provenance_method_invalid")
                        if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
                            issues.append(f"visualdiff:{identity}:localized_provenance_source_hash_invalid")
                        if not isinstance(artifact, dict):
                            issues.append(f"visualdiff:{identity}:localized_provenance_artifact_missing")
                        else:
                            path = str(artifact.get("path") or "")
                            digest = str(artifact.get("sha256") or "").lower()
                            if not path.startswith("derived/quality/") or len(digest) != 64 or any(
                                char not in "0123456789abcdef" for char in digest
                            ):
                                issues.append(f"visualdiff:{identity}:localized_provenance_artifact_invalid")
                        if not isinstance(support, dict) or support.get("decision_code") != "1":
                            issues.append(f"visualdiff:{identity}:localized_provenance_audit_missing")
                        else:
                            for field in (
                                "assignment_payload_sha256",
                                "evidence_sha256",
                                "source_workbook_sha256",
                            ):
                                value = str(support.get(field) or "").lower()
                                if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                                    issues.append(f"visualdiff:{identity}:localized_provenance_{field}_invalid")
        elif desc_source == MACHINE_RECONCILED_DESC_SOURCE:
            evidence = row.get("review_evidence")
            reconciliation = evidence.get("semantic_reconciliation") if isinstance(evidence, dict) else None
            original = str((reconciliation or {}).get("original_human_description") or "").strip()
            final = str((evidence or {}).get("machine_reconciled_description") or "").strip()
            support = (reconciliation or {}).get("independent_audit_support")
            artifact = (reconciliation or {}).get("evidence_artifact")
            source_hash = str((reconciliation or {}).get("source_input_sha256") or "").lower()
            if not isinstance(evidence, dict) or not isinstance(reconciliation, dict):
                issues.append(f"visualdiff:{identity}:machine_reconciliation_evidence_missing")
            else:
                details = tentative_description_details(original)
                if not details or details.get("kind") != "graphic_uncertain":
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_original_invalid")
                if not final or tentative_description_details(final) or final != str(row.get("change_desc_gt") or "").strip():
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_final_invalid")
                if reconciliation.get("method") != MACHINE_RECONCILIATION_METHOD:
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_method_invalid")
                if not str(reconciliation.get("machine_visual_reconciliation") or "").strip():
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_basis_missing")
                if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_source_hash_invalid")
                if not isinstance(artifact, dict):
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_artifact_missing")
                else:
                    path = str(artifact.get("path") or "")
                    digest = str(artifact.get("sha256") or "").lower()
                    if not path.startswith("derived/quality/") or len(digest) != 64 or any(
                        char not in "0123456789abcdef" for char in digest
                    ):
                        issues.append(f"visualdiff:{identity}:machine_reconciliation_artifact_invalid")
                if not isinstance(support, dict) or support.get("decision_code") != "1":
                    issues.append(f"visualdiff:{identity}:machine_reconciliation_audit_missing")
                else:
                    for field in (
                        "assignment_payload_sha256",
                        "evidence_sha256",
                        "source_workbook_sha256",
                    ):
                        value = str(support.get(field) or "").lower()
                        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                            issues.append(f"visualdiff:{identity}:machine_reconciliation_{field}_invalid")
        elif desc_source == MACHINE_EPISTEMIC_DESC_SOURCE:
            evidence = row.get("review_evidence")
            if not isinstance(evidence, dict):
                issues.append(f"visualdiff:{identity}:machine_epistemic_evidence_missing")
            else:
                original = str(evidence.get("original_human_description") or "").strip()
                final = str(evidence.get("machine_final_description") or "").strip()
                details = tentative_description_details(original)
                finalization = evidence.get("description_finalization")
                support = evidence.get("independent_audit_support")
                if not details or details.get("kind") == "graphic_uncertain":
                    issues.append(f"visualdiff:{identity}:machine_epistemic_original_invalid")
                elif final != definitive_description(details) or final != str(row.get("change_desc_gt") or "").strip():
                    issues.append(f"visualdiff:{identity}:machine_epistemic_final_invalid")
                if not isinstance(finalization, dict):
                    issues.append(f"visualdiff:{identity}:machine_epistemic_record_missing")
                else:
                    if str(finalization.get("method") or "") != MACHINE_EPISTEMIC_METHOD:
                        issues.append(f"visualdiff:{identity}:machine_epistemic_method_invalid")
                    if not str(finalization.get("basis") or "").strip():
                        issues.append(f"visualdiff:{identity}:machine_epistemic_basis_missing")
                    source_hash = str(finalization.get("source_input_sha256") or "").lower()
                    if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
                        issues.append(f"visualdiff:{identity}:machine_epistemic_source_hash_invalid")
                    artifacts = finalization.get("evidence_artifacts")
                    if not isinstance(artifacts, list) or not artifacts:
                        issues.append(f"visualdiff:{identity}:machine_epistemic_artifacts_missing")
                    else:
                        for artifact in artifacts:
                            path = str(artifact.get("path") or "") if isinstance(artifact, dict) else ""
                            digest = str(artifact.get("sha256") or "").lower() if isinstance(artifact, dict) else ""
                            if not path.startswith("derived/quality/") or len(digest) != 64 or any(
                                char not in "0123456789abcdef" for char in digest
                            ):
                                issues.append(f"visualdiff:{identity}:machine_epistemic_artifact_invalid")
                                break
                if not isinstance(support, dict) or support.get("decision_code") != "1":
                    issues.append(f"visualdiff:{identity}:machine_epistemic_audit_support_missing")
                else:
                    for field in (
                        "assignment_payload_sha256",
                        "evidence_sha256",
                        "source_workbook_sha256",
                    ):
                        value = str(support.get(field) or "").lower()
                        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                            issues.append(f"visualdiff:{identity}:machine_epistemic_{field}_invalid")
        elif desc_source != "human":
            issues.append(f"visualdiff:{identity}:description_not_human")
        if str(row.get("review_confidence") or "").strip().lower() != "high":
            issues.append(f"visualdiff:{identity}:review_confidence_not_high")
        if not str(row.get("project_id") or "").strip():
            issues.append(f"visualdiff:{identity}:missing_project_id")
    if issues:
        raise ValueError("prepared_row_contract_failed:" + ";".join(issues[:25]))


def split_file_entries(path: Path) -> tuple[bytes, list[str]]:
    raw_bytes = path.read_bytes()
    raw_lines = raw_bytes.decode("utf-8").splitlines()
    entries = [line.strip() for line in raw_lines if line.strip() and not line.lstrip().startswith("#")]
    if len(entries) != len(set(entries)):
        raise ValueError(f"duplicate_entries_in_active_split:{path.as_posix()}")
    return raw_bytes, entries


def staged_split_bytes(
    root: Path,
    prepared_micro: list[dict[str, Any]],
    prepared_visual: list[dict[str, Any]],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    additions: dict[tuple[str, str], set[str]] = {
        (task, split): set() for task in ("microtext", "visualdiff") for split in SPLITS
    }
    for row in prepared_micro:
        unit = str(row.get("doc_id") or "").strip()
        split = str(row.get("split") or "").strip().lower()
        if not unit or split not in SPLITS:
            raise ValueError(f"invalid_prepared_microtext_split:{unit}:{split}")
        additions[("microtext", split)].add(unit)
    for row in prepared_visual:
        unit = str(row.get("project_id") or "").strip()
        split = str(row.get("split") or "").strip().lower()
        if not unit or split not in SPLITS:
            raise ValueError(f"invalid_prepared_visualdiff_split:{unit}:{split}")
        additions[("visualdiff", split)].add(unit)

    assignments: dict[tuple[str, str], str] = {}
    bytes_by_key: dict[tuple[str, str], bytes] = {}
    for task in ("microtext", "visualdiff"):
        for split in SPLITS:
            key = (task, split)
            relative = f"splits/{task}_{split}.txt"
            raw_bytes, entries = split_file_entries(root / relative)
            bytes_by_key[key] = raw_bytes
            for unit in entries:
                old = assignments.get((task, unit))
                if old and old != split:
                    raise ValueError(f"active_split_conflict:{task}:{unit}:{old}:{split}")
                assignments[(task, unit)] = split

    staged: dict[str, bytes] = {}
    added_summary: dict[str, list[str]] = {}
    for task in ("microtext", "visualdiff"):
        for split in SPLITS:
            key = (task, split)
            relative = f"splits/{task}_{split}.txt"
            new_units: list[str] = []
            for unit in sorted(additions[key]):
                old = assignments.get((task, unit))
                if old and old != split:
                    raise ValueError(f"prepared_split_conflict:{task}:{unit}:{old}:{split}")
                if not old:
                    assignments[(task, unit)] = split
                    new_units.append(unit)
            data = bytes_by_key[key]
            if new_units:
                newline = b"\r\n" if b"\r\n" in data else b"\n"
                if data and not data.endswith(b"\n"):
                    data += b"\n" if data.endswith(b"\r") else newline
                data += newline.join(unit.encode("utf-8") for unit in new_units) + newline
            staged[relative] = data
            added_summary[f"{task}:{split}"] = new_units
    return staged, added_summary


def row_map(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row.get("id") or "").strip()
        if not identity or identity in mapped:
            raise ValueError(f"{label}_missing_or_duplicate_unified_id:{identity or 'blank'}")
        mapped[identity] = row
    return mapped


def write_validation_root(
    validation_root: Path,
    root: Path,
    split_bytes: dict[str, bytes],
) -> None:
    (validation_root / "splits").mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "manifest.jsonl", validation_root / "manifest.jsonl")
    for relative, data in split_bytes.items():
        path = validation_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def validate_staged_release(
    root: Path,
    artifacts: dict[str, Path],
    split_bytes: dict[str, bytes],
) -> dict[str, Any]:
    active_items = read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_micro_questions = read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    active_pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    active_visual_questions = read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    active_unified = read_jsonl(root / "eng_bench.jsonl")
    items = read_jsonl(artifacts["combined_microtext_items"])
    micro_questions = read_jsonl(artifacts["combined_microtext_questions"])
    pairs = read_jsonl(artifacts["combined_visualdiff_pairs"])
    visual_questions = read_jsonl(artifacts["combined_visualdiff_questions"])
    prepared_items = read_jsonl(artifacts["prepared_microtext_items"])
    prepared_pairs = read_jsonl(artifacts["prepared_visualdiff_pairs"])
    unified = read_jsonl(artifacts["unified"])
    validate_prepared_rows(prepared_items, prepared_pairs, machine_evidence_holds=evidence_hold_ids(root))

    require_additive_rows(active_items, items, prepared_items, label="microtext_items")
    require_additive_rows(active_pairs, pairs, prepared_pairs, label="visualdiff_pairs")
    if len(micro_questions) - len(active_micro_questions) != len(prepared_items):
        raise ValueError("microtext_question_delta_mismatch")
    if micro_questions[: len(active_micro_questions)] != active_micro_questions:
        raise ValueError("microtext_questions_rewrite_or_reorder_active_rows")
    if len(visual_questions) - len(active_visual_questions) != len(prepared_pairs):
        raise ValueError("visualdiff_question_delta_mismatch")
    if visual_questions[: len(active_visual_questions)] != active_visual_questions:
        raise ValueError("visualdiff_questions_rewrite_or_reorder_active_rows")

    rebuilt = unify_dataset.process_visualdiff(
        root, artifacts["combined_visualdiff_pairs"], artifacts["combined_visualdiff_questions"]
    ) + unify_dataset.process_microtext(
        root, artifacts["combined_microtext_items"], artifacts["combined_microtext_questions"]
    )
    if rebuilt != unified:
        raise ValueError("unified_artifact_does_not_match_combined_annotations")
    active_map = row_map(active_unified, "active")
    combined_map = row_map(unified, "combined")
    if not set(active_map).issubset(combined_map):
        raise ValueError("unified_preview_deletes_active_ids")
    if any(combined_map[key] != value for key, value in active_map.items()):
        raise ValueError("unified_preview_changes_active_rows")

    annotation_issue_list = annotation_errors(pairs, visual_questions, items, micro_questions)
    if annotation_issue_list:
        raise ValueError("annotation_validation_failed:" + ";".join(annotation_issue_list[:10]))
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict, bad = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    if strict.errors or bad:
        raise ValueError(
            "strict_unified_validation_failed:" + ";".join(strict.errors[:10])
        )
    leakage = audit_question_leakage.audit(unified)
    if leakage["critical_failures"]:
        raise ValueError(f"question_answer_leakage:{leakage['critical_failures']}")
    docs, manifest_pairs = manifest_maps(root)
    combined_leakage = preview_split_leakage(items, pairs, docs, manifest_pairs)
    if combined_leakage:
        raise ValueError("combined_split_leakage:" + ";".join(combined_leakage[:10]))

    with tempfile.TemporaryDirectory(dir=root / "derived" / "quality") as temp:
        validation_root = Path(temp)
        write_validation_root(validation_root, root, split_bytes)
        split_issues = leakage_check.check_leakage(validation_root)
    if split_issues:
        raise ValueError("staged_split_files_leak:" + ";".join(split_issues[:10]))

    split_maps: dict[str, dict[str, str]] = {"microtext": {}, "visualdiff": {}}
    for task in split_maps:
        for split in SPLITS:
            text = split_bytes[f"splits/{task}_{split}.txt"].decode("utf-8")
            for line in text.splitlines():
                unit = line.strip()
                if unit and not unit.startswith("#"):
                    split_maps[task][unit] = split
    membership_issues: list[str] = []
    for row in items:
        unit = str(row.get("doc_id") or "")
        if split_maps["microtext"].get(unit) != str(row.get("split") or ""):
            membership_issues.append(f"microtext:{row.get('item_id')}:{unit}:{row.get('split')}")
    for row in pairs:
        unit = str(row.get("project_id") or "")
        if split_maps["visualdiff"].get(unit) != str(row.get("split") or ""):
            membership_issues.append(f"visualdiff:{row.get('pair_id')}:{unit}:{row.get('split')}")
    if membership_issues:
        raise ValueError("annotation_split_membership_failed:" + ";".join(membership_issues[:10]))

    return {
        "active_rows": len(active_unified),
        "combined_rows": len(unified),
        "promoted_microtext_rows": len(prepared_items),
        "promoted_visualdiff_rows": len(prepared_pairs),
        "annotation_errors": 0,
        "strict_errors": 0,
        "question_leaks": 0,
        "split_leaks": 0,
        "missing_images": int(strict.stats.get("missing_images", 0)),
        "text_encoding_issues": int(strict.stats.get("text_encoding_issues", 0)),
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.promotion-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        # Windows may briefly deny replacement while a scanner holds the file.
        # Persistent locks still fail, retaining the transaction snapshot.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.05 * (2 ** attempt))
    finally:
        if temporary.exists():
            temporary.unlink()


def create_snapshot(root: Path, snapshot_dir: Path, before_hashes: dict[str, str]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if any(snapshot_dir.iterdir()):
        raise ValueError(f"snapshot_directory_not_empty:{snapshot_dir}")
    for relative in ACTIVE_PATHS:
        source = root / relative
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {
        "mode": "reviewed_gold_promotion_snapshot",
        "root": root.as_posix(),
        "files": before_hashes,
    }
    (snapshot_dir / "snapshot_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def restore_snapshot(root: Path, snapshot_dir: Path, before_hashes: dict[str, str]) -> None:
    for relative in ACTIVE_PATHS:
        source = snapshot_dir / relative
        if not source.is_file():
            raise RuntimeError(f"rollback_snapshot_file_missing:{relative}")
        atomic_write(root / relative, source.read_bytes())
    restored = {relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS}
    if restored != before_hashes:
        raise RuntimeError("rollback_hash_verification_failed")


def provenance_regressions(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    before_docs = {row["doc_id"]: row for row in before["documents"]}
    after_docs = {row["doc_id"]: row for row in after["documents"]}
    new_docs = sorted(set(after_docs) - set(before_docs))
    for doc_id in new_docs:
        if not after_docs[doc_id]["paper_ready"]:
            issues.append(f"new_active_doc_not_paper_ready:{doc_id}")
    for key in (
        "unresolved_active_rows",
        "missing_inventory_docs",
        "missing_manifest_docs",
        "missing_local_paths",
        "missing_source_urls",
        "rights_status_disagreement_docs",
        "rights_blocked_docs",
        "missing_recorded_sha256_docs",
        "sha256_mismatch_docs",
    ):
        if int(after["totals"][key]) > int(before["totals"][key]):
            issues.append(f"provenance_regression:{key}:{before['totals'][key]}:{after['totals'][key]}")
    return issues


def post_apply_validation(
    root: Path,
    expected_hashes: dict[str, str],
    before_provenance: dict[str, Any],
) -> dict[str, Any]:
    actual = {relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS}
    hash_issues = [
        f"post_apply_hash_mismatch:{relative}:{actual[relative]}:{expected}"
        for relative, expected in expected_hashes.items()
        if actual.get(relative) != expected
    ]
    if hash_issues:
        raise ValueError(";".join(hash_issues))
    pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    visual_questions = read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    items = read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    micro_questions = read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    errors = annotation_errors(pairs, visual_questions, items, micro_questions)
    if errors:
        raise ValueError("post_apply_annotation_validation_failed:" + ";".join(errors[:10]))
    unified = read_jsonl(root / "eng_bench.jsonl")
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict, bad = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    if strict.errors or bad:
        raise ValueError("post_apply_strict_validation_failed:" + ";".join(strict.errors[:10]))
    leaks = leakage_check.check_leakage(root)
    if leaks:
        raise ValueError("post_apply_split_leakage:" + ";".join(leaks[:10]))
    question_leakage = audit_question_leakage.audit(unified)
    if question_leakage["critical_failures"]:
        raise ValueError(f"post_apply_question_leakage:{question_leakage['critical_failures']}")
    after_provenance = audit_active_gold_provenance.build_report(root)
    provenance_issues = provenance_regressions(before_provenance, after_provenance)
    if provenance_issues:
        raise ValueError(";".join(provenance_issues))
    return {
        "active_rows": len(items) + len(pairs),
        "strict_errors": 0,
        "split_leaks": 0,
        "question_leaks": 0,
        "provenance_regressions": 0,
        "paper_ready_docs": after_provenance["totals"]["paper_ready_docs"],
        "active_source_docs": after_provenance["totals"]["active_source_docs"],
    }


def build_transaction(
    root: Path,
    report_path: Path,
    expected_report_sha256: str,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, str], dict[str, Any]]:
    root = root.resolve()
    resolved_report, preview = load_preview_report(root, report_path, expected_report_sha256)
    before_hashes = verify_active_prestate(root, preview)
    artifacts = resolve_artifacts(root, preview)
    prepared_micro = read_jsonl(artifacts["prepared_microtext_items"])
    prepared_visual = read_jsonl(artifacts["prepared_visualdiff_pairs"])
    counts = preview.get("counts") or {}
    if len(prepared_micro) != int(counts.get("prepared_microtext_rows") or 0):
        raise ValueError("preview_microtext_prepared_count_mismatch")
    if len(prepared_visual) != int(counts.get("prepared_visualdiff_rows") or 0):
        raise ValueError("preview_visualdiff_prepared_count_mismatch")
    split_bytes, split_additions = staged_split_bytes(root, prepared_micro, prepared_visual)
    validation = validate_staged_release(root, artifacts, split_bytes)
    staged: dict[str, bytes] = {
        target: artifacts[key].read_bytes() for key, target in ANNOTATION_ARTIFACTS.items()
    }
    staged.update(split_bytes)
    expected_after = dict(before_hashes)
    for relative, data in staged.items():
        expected_after[relative] = sha256_bytes(data)
    transaction = {
        "goal": "Gold v2.0 Global",
        "mode": "reviewed_gold_promotion_transaction",
        "preview_report": resolved_report.relative_to(root).as_posix(),
        "preview_report_sha256": file_sha256(resolved_report),
        "ready_to_apply": True,
        "applied": False,
        "rolled_back": False,
        "before_hashes": before_hashes,
        "expected_after_hashes": expected_after,
        "split_additions": split_additions,
        "validation": validation,
    }
    return transaction, staged, before_hashes, audit_active_gold_provenance.build_report(root)


def apply_transaction(
    root: Path,
    report_path: Path,
    expected_report_sha256: str,
    *,
    snapshot_dir: Path | None = None,
    apply: bool = False,
    fail_after_replacements: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    transaction, staged, before_hashes, before_provenance = build_transaction(
        root, report_path, expected_report_sha256
    )
    if not apply:
        return transaction
    if snapshot_dir is None:
        raise ValueError("snapshot_dir_is_required_for_apply")
    snapshot = require_under(root, snapshot_dir, "derived/snapshots", "snapshot directory")
    create_snapshot(root, snapshot, before_hashes)
    transaction["snapshot_dir"] = snapshot.relative_to(root).as_posix()
    replacements = 0
    try:
        for relative in MUTABLE_PATHS:
            atomic_write(root / relative, staged[relative])
            replacements += 1
            if fail_after_replacements is not None and replacements >= fail_after_replacements:
                raise RuntimeError("injected_post_replace_failure")
        post = post_apply_validation(
            root, transaction["expected_after_hashes"], before_provenance
        )
        transaction.update(
            {
                "applied": True,
                "after_hashes": {
                    relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS
                },
                "post_apply_validation": post,
            }
        )
        return transaction
    except Exception as exc:
        try:
            restore_snapshot(root, snapshot, before_hashes)
            transaction.update(
                {
                    "applied": False,
                    "rolled_back": True,
                    "failure": f"{type(exc).__name__}:{exc}",
                    "restored_hashes": {
                        relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS
                    },
                }
            )
        except Exception as rollback_exc:
            transaction.update(
                {
                    "applied": False,
                    "rolled_back": False,
                    "failure": f"{type(exc).__name__}:{exc}",
                    "rollback_failure": f"{type(rollback_exc).__name__}:{rollback_exc}",
                }
            )
        return transaction


def write_report(root: Path, path: Path, report: dict[str, Any]) -> Path:
    output = require_under(root, path, "derived/quality", "transaction report")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preview-report", type=Path, required=True)
    parser.add_argument("--expected-preview-sha256", required=True)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        output = require_under(root, args.report, "derived/quality", "transaction report")
        output.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    try:
        report = apply_transaction(
            root,
            args.preview_report,
            args.expected_preview_sha256,
            snapshot_dir=args.snapshot_dir,
            apply=args.apply,
        )
        output = write_report(root, output, report)
        print(f"[OK] Wrote {output}")
        print(
            json.dumps(
                {
                    "ready_to_apply": report.get("ready_to_apply"),
                    "applied": report.get("applied"),
                    "rolled_back": report.get("rolled_back"),
                    "validation": report.get("post_apply_validation") or report.get("validation"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failure_report = {
            "goal": "Gold v2.0 Global",
            "mode": "reviewed_gold_promotion_transaction_refusal",
            "preview_report": str(args.preview_report).replace("\\", "/"),
            "expected_preview_sha256": args.expected_preview_sha256,
            "ready_to_apply": False,
            "applied": False,
            "rolled_back": False,
            "error": f"{type(exc).__name__}:{exc}",
        }
        try:
            write_report(root, output, failure_report)
            print(f"[OK] Wrote refusal report {output}")
        except OSError as report_exc:
            print(f"[ERROR] Could not write refusal report: {report_exc}")
        print(f"[ERROR] {exc}")
        return 1
    return 0 if not args.apply or report.get("applied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
