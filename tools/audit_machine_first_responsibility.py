#!/usr/bin/env python3
"""Audit the machine-first responsibility boundary without editing Gold."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


# Source intake may append review-only documents to the manifest without
# changing active Gold rows. Historical promotion reports therefore pin every
# release file except this mutable source registry.
MUTABLE_NON_GOLD_HASH_PATHS = {"manifest.jsonl"}


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def row_id(row: dict[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or row.get("pair_id")
        or row.get("record_id")
        or row.get("id")
        or ""
    ).strip()


def origin(row: dict[str, Any]) -> str:
    value = str(row.get("machine_certification_origin_cohort") or "").strip().lower()
    return value or "unspecified"


def task(row: dict[str, Any]) -> str:
    value = str(row.get("task") or "").strip().lower()
    if value:
        return value
    return "visualdiff" if row.get("pair_id") else "microtext"


def split(row: dict[str, Any]) -> str:
    return str(row.get("reserved_split") or row.get("split") or "missing").strip().lower()


def artifact_path(root: Path, report: dict[str, Any], name: str) -> Path:
    artifact = (report.get("artifacts") or {}).get(name) or {}
    value = str(artifact.get("path") or "").strip()
    if not value:
        raise ValueError(f"eligibility report is missing artifact path: {name}")
    return resolve(root, value)


def verify_artifact(
    root: Path,
    report: dict[str, Any],
    name: str,
    issues: list[str],
) -> Path:
    artifact = (report.get("artifacts") or {}).get(name) or {}
    path = artifact_path(root, report, name)
    if not path.is_file():
        issues.append(f"missing_artifact:{name}")
        return path
    expected = str(artifact.get("sha256") or "").strip().lower()
    if not expected or file_sha256(path).lower() != expected:
        issues.append(f"artifact_sha256_mismatch:{name}")
    return path


def verify_readiness_artifact(
    root: Path,
    report: dict[str, Any],
    name: str,
    issues: list[str],
) -> Path:
    value = str((report.get("artifacts") or {}).get(name) or "").strip()
    path = resolve(root, value) if value else root / "__missing_readiness_artifact__"
    if not value or not path.is_file():
        issues.append(f"missing_readiness_artifact:{name}")
        return path
    expected = str((report.get("artifact_sha256") or {}).get(name) or "").lower()
    if not expected or file_sha256(path).lower() != expected:
        issues.append(f"readiness_artifact_sha256_mismatch:{name}")
    return path


def count_by(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        values = {
            "origin": origin(row),
            "task": task(row),
            "split": split(row),
        }
        counts["|".join(values[field] for field in fields)] += 1
    return dict(sorted(counts.items()))


def build_report(
    root: Path,
    eligibility_report_path: Path,
    agreement_completion_path: Path,
    *,
    date_label: str,
    historical_finalization_path: Path | None = None,
    strict_dedup_path: Path | None = None,
    nonpin_eligibility_report_path: Path | None = None,
    curation_funnel_paths: list[Path] | None = None,
    human_recovery_report_path: Path | None = None,
    human_recovery_promotion_report_path: Path | None = None,
    calibration_reuse_report_path: Path | None = None,
    nonpin_readiness_report_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    eligibility_report_path = resolve(root, eligibility_report_path)
    agreement_completion_path = resolve(root, agreement_completion_path)
    eligibility = json.loads(eligibility_report_path.read_text(encoding="utf-8"))
    agreement = json.loads(agreement_completion_path.read_text(encoding="utf-8"))
    issues: list[str] = []

    eligible_path = verify_artifact(root, eligibility, "auto_eligible", issues)
    human_path = verify_artifact(root, eligibility, "human_required", issues)
    calibration_path = verify_artifact(root, eligibility, "calibration_checklist", issues)
    eligible_rows = read_jsonl(eligible_path) if eligible_path.is_file() else []
    human_rows = read_jsonl(human_path) if human_path.is_file() else []

    counts = eligibility.get("counts") or {}
    expected_eligible = int(counts.get("auto_eligible_pending_calibration") or 0)
    expected_human = int(counts.get("human_required") or 0)
    expected_input = int(counts.get("input_rows") or 0)
    calibration_rows = int((eligibility.get("calibration_sample") or {}).get("rows") or 0)
    if len(eligible_rows) != expected_eligible:
        issues.append("eligible_row_count_mismatch")
    if len(human_rows) != expected_human:
        issues.append("human_required_row_count_mismatch")
    if expected_eligible + expected_human + int(counts.get("reject_or_hold") or 0) != expected_input:
        issues.append("eligibility_partition_count_mismatch")

    eligible_ids = [row_id(row) for row in eligible_rows]
    human_ids = [row_id(row) for row in human_rows]
    if any(not value for value in eligible_ids + human_ids):
        issues.append("missing_row_identity")
    if len(eligible_ids) != len(set(eligible_ids)):
        issues.append("duplicate_eligible_identity")
    if len(human_ids) != len(set(human_ids)):
        issues.append("duplicate_human_required_identity")
    if set(eligible_ids) & set(human_ids):
        issues.append("eligible_human_required_overlap")
    for row in eligible_rows:
        if task(row) != "microtext" or split(row) != "train":
            issues.append(f"machine_lane_scope_violation:{row_id(row)}")
            break

    current_machine = sum(origin(row) == "current" for row in eligible_rows)
    future_machine = sum(origin(row) == "future" for row in eligible_rows)
    current_human = sum(origin(row) == "current" for row in human_rows)
    future_human = sum(origin(row) == "future" for row in human_rows)
    formal_rows = int(agreement.get("formal_detailed_agreement_required_rows") or 0)
    formal_actions = int(agreement.get("formal_detailed_reviewer_actions_remaining") or 0)
    if formal_rows and formal_actions != formal_rows * 2:
        issues.append("formal_agreement_action_count_mismatch")

    contract_path_value = str(agreement.get("contract") or "").strip()
    contract_path = resolve(root, contract_path_value) if contract_path_value else None
    if contract_path and contract_path.is_file():
        expected_contract_sha = str(agreement.get("contract_sha256") or "").strip().lower()
        if file_sha256(contract_path).lower() != expected_contract_sha:
            issues.append("agreement_contract_sha256_mismatch")
    else:
        issues.append("missing_agreement_candidate_contract")

    pending_screen_counts = agreement.get("screen_pending_by_channel") or agreement.get(
        "channel_counts"
    ) or {}
    new_screen_actions = int(
        pending_screen_counts.get("new_independent_screen_required") or 0
    )
    issued_screen_pending = int(
        pending_screen_counts.get("pending_issued_independent_screen") or 0
    )
    primary_pending = int(agreement.get("primary_promotion_review_pending") or 0)
    net_saved = max(0, len(eligible_rows) - calibration_rows)
    historically_certified_rows: list[dict[str, Any]] = []
    strict_unique_rows: list[dict[str, Any]] = []
    strict_duplicate_rows: list[dict[str, Any]] = []
    nonpin_eligible_rows: list[dict[str, Any]] = []
    nonpin_calibration_rows = 0
    intake_raw_rows = 0
    intake_machine_removed = 0
    intake_human_rows = 0
    intake_corrected_rows = 0
    verified_funnels: list[dict[str, str]] = []
    repeat_review_actions_avoided = 0
    recovery_ready_rows = 0
    recovery_held_rows = 0
    verified_recovery: dict[str, str] = {}
    current_eligible_rows = eligible_rows
    eligible_already_active = 0
    calibration_completed_reused = 0
    calibration_rows_remaining = calibration_rows
    precalibration_strict_ready = 0
    precalibration_duplicate_holds = 0
    precalibration_other_holds = 0
    verified_calibration_reuse: dict[str, Any] = {}
    verified_nonpin_readiness: dict[str, Any] = {}
    calibration_reuse_scope = ""
    if historical_finalization_path is not None:
        historical_finalization_path = resolve(root, historical_finalization_path)
        if not historical_finalization_path.is_file():
            issues.append("missing_historical_finalization_report")
        else:
            historical = json.loads(historical_finalization_path.read_text(encoding="utf-8"))
            if historical.get("release_ready") is not True:
                issues.append("historical_finalization_not_ready")
            if historical.get("active_gold_modified") is not False:
                issues.append("historical_finalization_modified_active_gold")
            artifact = (historical.get("artifacts") or {}).get("certified_balance_deferred") or {}
            path = resolve(root, str(artifact.get("path") or ""))
            if not path.is_file():
                issues.append("missing_historically_certified_artifact")
            elif file_sha256(path) != str(artifact.get("sha256") or ""):
                issues.append("historically_certified_artifact_sha256_mismatch")
            else:
                historically_certified_rows = read_jsonl(path)
    if strict_dedup_path is not None:
        strict_dedup_path = resolve(root, strict_dedup_path)
        if not strict_dedup_path.is_file():
            issues.append("missing_strict_dedup_report")
        else:
            strict_report = json.loads(strict_dedup_path.read_text(encoding="utf-8"))
            if strict_report.get("issues"):
                issues.append("strict_dedup_report_has_issues")
            if strict_report.get("active_gold_modified") is not False:
                issues.append("strict_dedup_modified_active_gold")
            for name, destination in (
                ("strict_unique", strict_unique_rows),
                ("duplicate_hold", strict_duplicate_rows),
            ):
                artifact = (strict_report.get("artifacts") or {}).get(name) or {}
                path = resolve(root, str(artifact.get("path") or ""))
                if not path.is_file():
                    issues.append(f"missing_strict_dedup_artifact:{name}")
                elif file_sha256(path) != str(artifact.get("sha256") or ""):
                    issues.append(f"strict_dedup_artifact_sha256_mismatch:{name}")
                else:
                    destination.extend(read_jsonl(path))
    if historically_certified_rows:
        historical_ids = {row_id(row) for row in historically_certified_rows}
        if not historical_ids or "" in historical_ids:
            issues.append("historically_certified_identity_invalid")
        if not historical_ids <= set(eligible_ids):
            issues.append("historically_certified_not_subset_of_eligible")
        if strict_dedup_path is not None:
            partition_ids = {row_id(row) for row in strict_unique_rows + strict_duplicate_rows}
            if partition_ids != historical_ids:
                issues.append("strict_dedup_not_complete_historical_partition")
    if nonpin_eligibility_report_path is not None:
        nonpin_eligibility_report_path = resolve(root, nonpin_eligibility_report_path)
        if not nonpin_eligibility_report_path.is_file():
            issues.append("missing_nonpin_eligibility_report")
        else:
            nonpin_report = json.loads(nonpin_eligibility_report_path.read_text(encoding="utf-8"))
            nonpin_path = verify_artifact(root, nonpin_report, "auto_eligible", issues)
            nonpin_checklist_path = verify_artifact(
                root, nonpin_report, "calibration_checklist", issues
            )
            nonpin_eligible_rows = read_jsonl(nonpin_path) if nonpin_path.is_file() else []
            nonpin_counts = nonpin_report.get("counts") or {}
            nonpin_calibration_rows = int(
                (nonpin_report.get("calibration_sample") or {}).get("rows") or 0
            )
            if len(nonpin_eligible_rows) != int(
                nonpin_counts.get("auto_eligible_pending_calibration") or 0
            ):
                issues.append("nonpin_eligible_row_count_mismatch")
            if int(nonpin_counts.get("auto_eligible_deferred_pin") or 0) != 0:
                issues.append("nonpin_report_contains_deferred_pin_count")
            if any(str(row.get("category") or "") == "pin_label" for row in nonpin_eligible_rows):
                issues.append("nonpin_eligible_contains_pin_label")
            nonpin_ids = {row_id(row) for row in nonpin_eligible_rows}
            expected_nonpin_ids = {
                row_id(row)
                for row in eligible_rows
                if str(row.get("category") or "") != "pin_label"
            }
            if nonpin_ids != expected_nonpin_ids:
                issues.append("nonpin_eligible_not_complete_full_cohort_partition")
            if nonpin_checklist_path.is_file():
                with nonpin_checklist_path.open(
                    "r", encoding="utf-8-sig", newline=""
                ) as handle:
                    sample_rows = list(csv.DictReader(handle))
                if len(sample_rows) != nonpin_calibration_rows:
                    issues.append("nonpin_calibration_row_count_mismatch")
                if any(
                    str(row.get("category") or "") == "pin_label"
                    for row in sample_rows
                ):
                    issues.append("nonpin_calibration_contains_pin_label")
            calibration_rows = nonpin_calibration_rows
            net_saved = len(historically_certified_rows) + max(
                0, len(nonpin_eligible_rows) - nonpin_calibration_rows
            )
    for funnel_value in curation_funnel_paths or []:
        funnel_path = resolve(root, funnel_value)
        if not funnel_path.is_file():
            issues.append(f"missing_curation_funnel:{relative(root, funnel_path)}")
            continue
        funnel = json.loads(funnel_path.read_text(encoding="utf-8"))
        raw_rows = int(funnel.get("raw_candidate_rows") or 0)
        removed_rows = int(funnel.get("machine_row_decisions_removed") or 0)
        human_review_rows = int(funnel.get("human_review_rows") or 0)
        packet_rows = int(funnel.get("packet_checklist_rows") or 0)
        if funnel.get("valid") is not True or funnel.get("issues"):
            issues.append(f"invalid_curation_funnel:{relative(root, funnel_path)}")
        if funnel.get("active_gold_modified") is not False:
            issues.append(
                f"curation_funnel_modified_active_gold:{relative(root, funnel_path)}"
            )
        if raw_rows <= 0 or removed_rows + human_review_rows != raw_rows:
            issues.append(f"curation_funnel_partition_mismatch:{relative(root, funnel_path)}")
        if packet_rows != human_review_rows:
            issues.append(f"curation_funnel_packet_mismatch:{relative(root, funnel_path)}")
        intake_raw_rows += raw_rows
        intake_machine_removed += removed_rows
        intake_human_rows += human_review_rows
        intake_corrected_rows += int(funnel.get("machine_corrected_rows") or 0)
        verified_funnels.append(
            {
                "path": relative(root, funnel_path),
                "sha256": file_sha256(funnel_path),
            }
        )
    if (human_recovery_report_path is None) != (
        human_recovery_promotion_report_path is None
    ):
        issues.append("human_recovery_reports_must_be_supplied_together")
    elif human_recovery_report_path is not None:
        recovery_issue_start = len(issues)
        human_recovery_report_path = resolve(root, human_recovery_report_path)
        human_recovery_promotion_report_path = resolve(
            root, human_recovery_promotion_report_path
        )
        if not human_recovery_report_path.is_file():
            issues.append("missing_human_recovery_report")
        if not human_recovery_promotion_report_path.is_file():
            issues.append("missing_human_recovery_promotion_report")
        if len(issues) == recovery_issue_start:
            recovery = json.loads(
                human_recovery_report_path.read_text(encoding="utf-8")
            )
            promotion = json.loads(
                human_recovery_promotion_report_path.read_text(encoding="utf-8")
            )
            recovery_input = resolve(root, str(recovery.get("input") or ""))
            if not recovery_input.is_file():
                issues.append("missing_human_recovery_input")
                recovery_rows: list[dict[str, Any]] = []
            else:
                recovery_rows = read_jsonl(recovery_input)
                if file_sha256(recovery_input) != str(recovery.get("input_sha256") or ""):
                    issues.append("human_recovery_input_sha256_mismatch")
            recovery_ids = {row_id(row) for row in recovery_rows}
            recovery_ready_rows = int(recovery.get("ready_rows") or 0)
            recovery_held_rows = int(recovery.get("held_rows") or 0)
            if recovery.get("valid") is not True:
                issues.append("human_recovery_report_invalid")
            if recovery.get("active_gold_rows_modified") != 0:
                issues.append("human_recovery_report_modified_gold")
            if int(recovery.get("input_rows") or 0) != len(recovery_rows):
                issues.append("human_recovery_input_count_mismatch")
            if recovery_ready_rows + recovery_held_rows != len(recovery_rows):
                issues.append("human_recovery_partition_mismatch")
            if not recovery_ids or "" in recovery_ids or len(recovery_ids) != len(recovery_rows):
                issues.append("human_recovery_identity_invalid")
            if not recovery_ids <= set(human_ids):
                issues.append("human_recovery_not_in_frozen_human_partition")
            if promotion.get("applied") is not True or promotion.get("rolled_back") is not False:
                issues.append("human_recovery_promotion_not_applied")
            if int((promotion.get("validation") or {}).get("promoted_visualdiff_rows") or 0) != recovery_ready_rows:
                issues.append("human_recovery_promoted_count_mismatch")
            post = promotion.get("post_apply_validation") or {}
            for field in ("strict_errors", "split_leaks", "question_leaks", "provenance_regressions"):
                if int(post.get(field) or 0) != 0:
                    issues.append(f"human_recovery_post_apply_{field}")
            after_hashes = promotion.get("after_hashes") or {}
            if not after_hashes:
                issues.append("human_recovery_after_hashes_missing")
            for relative_path, expected_hash in after_hashes.items():
                if relative_path in MUTABLE_NON_GOLD_HASH_PATHS:
                    continue
                active_path = root / relative_path
                if not active_path.is_file() or file_sha256(active_path) != str(expected_hash):
                    issues.append(f"human_recovery_active_hash_mismatch:{relative_path}")
            active_visual_path = root / "visualdiff/annotations/visualdiff_pairs.jsonl"
            active_visual_ids = {
                row_id(row) for row in read_jsonl(active_visual_path)
            } if active_visual_path.is_file() else set()
            if len(recovery_ids & active_visual_ids) != recovery_ready_rows:
                issues.append("human_recovery_active_ready_count_mismatch")
            if len(issues) == recovery_issue_start:
                repeat_review_actions_avoided = len(recovery_rows)
                human_rows = [row for row in human_rows if row_id(row) not in recovery_ids]
                verified_recovery = {
                    "reconciliation_report": relative(root, human_recovery_report_path),
                    "reconciliation_report_sha256": file_sha256(human_recovery_report_path),
                    "promotion_report": relative(root, human_recovery_promotion_report_path),
                    "promotion_report_sha256": file_sha256(
                        human_recovery_promotion_report_path
                    ),
                }
    if nonpin_readiness_report_path is not None and calibration_reuse_report_path is None:
        issues.append("nonpin_readiness_requires_calibration_reuse_report")
    if calibration_reuse_report_path is not None:
        calibration_reuse_report_path = resolve(root, calibration_reuse_report_path)
        reuse_issue_start = len(issues)
        if not calibration_reuse_report_path.is_file():
            issues.append("missing_calibration_reuse_report")
        else:
            reuse = json.loads(calibration_reuse_report_path.read_text(encoding="utf-8"))
            reuse_counts = reuse.get("counts") or {}
            if reuse.get("schema") != "eng_bench_machine_calibration_review_reuse_v1":
                issues.append("calibration_reuse_schema_mismatch")
            if reuse.get("active_gold_modified") is not False:
                issues.append("calibration_reuse_modified_active_gold")
            if reuse.get("safe_to_merge_gold") is not False:
                issues.append("calibration_reuse_unsafe_merge_flag")
            reuse_eligibility_sha = str(
                (reuse.get("cohort") or {}).get("eligibility_report_sha256") or ""
            ).lower()
            full_eligibility_sha = file_sha256(eligibility_report_path).lower()
            nonpin_eligibility_sha = (
                file_sha256(nonpin_eligibility_report_path).lower()
                if nonpin_eligibility_report_path is not None
                and nonpin_eligibility_report_path.is_file()
                else ""
            )
            if reuse_eligibility_sha == full_eligibility_sha:
                reuse_original_rows = eligible_rows
                reuse_eligibility_scope = "full"
            elif (
                nonpin_eligibility_sha
                and reuse_eligibility_sha == nonpin_eligibility_sha
            ):
                reuse_original_rows = nonpin_eligible_rows
                reuse_eligibility_scope = "nonpin"
            else:
                reuse_original_rows = []
                reuse_eligibility_scope = "unmatched"
                issues.append("calibration_reuse_eligibility_sha256_mismatch")
            pending_path = verify_artifact(
                root, reuse, "current_pending_auto_eligible", issues
            )
            already_active_path = verify_artifact(
                root, reuse, "already_active_auto_eligible", issues
            )
            conflicts_path = verify_artifact(root, reuse, "conflicts", issues)
            verify_artifact(root, reuse, "remaining_checklist", issues)
            pending_rows = read_jsonl(pending_path) if pending_path.is_file() else []
            already_active_rows = (
                read_jsonl(already_active_path) if already_active_path.is_file() else []
            )
            conflict_rows = read_jsonl(conflicts_path) if conflicts_path.is_file() else []
            pending_ids = {row_id(row) for row in pending_rows}
            already_active_ids = {row_id(row) for row in already_active_rows}
            original_ids = {row_id(row) for row in reuse_original_rows}
            if not pending_ids or "" in pending_ids:
                issues.append("calibration_reuse_pending_identity_invalid")
            if "" in already_active_ids:
                issues.append("calibration_reuse_active_identity_invalid")
            if pending_ids & already_active_ids:
                issues.append("calibration_reuse_partition_overlap")
            if pending_ids | already_active_ids != original_ids:
                issues.append("calibration_reuse_partition_incomplete")
            expected_counts = {
                "eligible_rows_original": len(reuse_original_rows),
                "eligible_rows_current_pending": len(pending_rows),
                "eligible_rows_already_active": len(already_active_rows),
                "conflict_rows": len(conflict_rows),
            }
            for field, actual in expected_counts.items():
                if int(reuse_counts.get(field) or 0) != actual:
                    issues.append(f"calibration_reuse_count_mismatch:{field}")
            calibration_completed_reused = int(
                reuse_counts.get("reused_correct_rows") or 0
            )
            calibration_rows_remaining = int(reuse_counts.get("remaining_rows") or 0)
            if calibration_completed_reused + calibration_rows_remaining != calibration_rows:
                issues.append("calibration_reuse_sample_partition_mismatch")
            if conflict_rows:
                issues.append("calibration_reuse_has_conflicts")
            before_hashes = reuse.get("active_file_hashes_before") or {}
            after_hashes = reuse.get("active_file_hashes_after") or {}
            if not before_hashes or before_hashes != after_hashes:
                issues.append("calibration_reuse_active_hash_partition_mismatch")
            for relative_path, expected_hash in after_hashes.items():
                active_path = root / relative_path
                if not active_path.is_file() or file_sha256(active_path) != str(expected_hash):
                    issues.append(
                        f"calibration_reuse_current_active_hash_mismatch:{relative_path}"
                    )
            current_eligible_rows = pending_rows
            eligible_already_active = len(already_active_rows)
            if len(issues) == reuse_issue_start:
                calibration_reuse_scope = reuse_eligibility_scope
                verified_calibration_reuse = {
                    "path": relative(root, calibration_reuse_report_path),
                    "sha256": file_sha256(calibration_reuse_report_path),
                    "current_pending_rows": len(pending_rows),
                    "already_active_rows": len(already_active_rows),
                    "completed_calibration_rows": calibration_completed_reused,
                    "remaining_calibration_rows": calibration_rows_remaining,
                    "eligibility_scope": reuse_eligibility_scope,
                }
    if nonpin_readiness_report_path is not None:
        nonpin_readiness_report_path = resolve(root, nonpin_readiness_report_path)
        readiness_issue_start = len(issues)
        if not nonpin_readiness_report_path.is_file():
            issues.append("missing_nonpin_readiness_report")
        else:
            readiness = json.loads(
                nonpin_readiness_report_path.read_text(encoding="utf-8")
            )
            readiness_counts = readiness.get("counts") or {}
            if readiness.get("schema") != "eng_bench_nonpin_precalibration_readiness_v1":
                issues.append("nonpin_readiness_schema_mismatch")
            if readiness.get("precalibration_forecast_valid") is not True:
                issues.append("nonpin_readiness_forecast_invalid")
            if readiness.get("active_gold_modified") is not False:
                issues.append("nonpin_readiness_modified_active_gold")
            if readiness.get("calibration_required") is not True:
                issues.append("nonpin_readiness_missing_calibration_requirement")
            if readiness.get("ready_for_promotion") is not False:
                issues.append("nonpin_readiness_promotion_flag_unsafe")
            if readiness.get("safe_to_merge_gold") is not False:
                issues.append("nonpin_readiness_merge_flag_unsafe")
            if not (readiness.get("gates") or {}) or not all(
                bool(value) for value in (readiness.get("gates") or {}).values()
            ):
                issues.append("nonpin_readiness_gate_failure")
            if calibration_reuse_report_path is not None and str(
                (readiness.get("inputs") or {}).get("reuse_report_sha256") or ""
            ) != file_sha256(calibration_reuse_report_path):
                issues.append("nonpin_readiness_reuse_sha256_mismatch")
            readiness_paths = {
                name: verify_readiness_artifact(root, readiness, name, issues)
                for name in (
                    "strict_ready",
                    "structural_holds",
                    "near_region_holds",
                    "strict_duplicate_holds",
                )
            }
            strict_ready_rows = (
                read_jsonl(readiness_paths["strict_ready"])
                if readiness_paths["strict_ready"].is_file()
                else []
            )
            readiness_hold_rows: list[dict[str, Any]] = []
            strict_duplicate_hold_rows: list[dict[str, Any]] = []
            for name in (
                "structural_holds",
                "near_region_holds",
                "strict_duplicate_holds",
            ):
                if readiness_paths[name].is_file():
                    rows = read_jsonl(readiness_paths[name])
                    readiness_hold_rows.extend(rows)
                    if name == "strict_duplicate_holds":
                        strict_duplicate_hold_rows = rows
            current_nonpin_ids = {
                row_id(row)
                for row in current_eligible_rows
                if str(row.get("category") or "") != "pin_label"
            }
            strict_ready_ids = {row_id(row) for row in strict_ready_rows}
            readiness_hold_ids = {row_id(row) for row in readiness_hold_rows}
            if "" in strict_ready_ids | readiness_hold_ids:
                issues.append("nonpin_readiness_identity_invalid")
            if strict_ready_ids & readiness_hold_ids:
                issues.append("nonpin_readiness_partition_overlap")
            if strict_ready_ids | readiness_hold_ids != current_nonpin_ids:
                issues.append("nonpin_readiness_partition_incomplete")
            if int(readiness_counts.get("current_pending_input_rows") or 0) != len(
                current_eligible_rows
            ):
                issues.append("nonpin_readiness_current_pending_count_mismatch")
            if int(readiness_counts.get("nonpin_input_rows") or 0) != len(
                current_nonpin_ids
            ):
                issues.append("nonpin_readiness_nonpin_count_mismatch")
            if int(readiness_counts.get("strict_ready_if_calibrated") or 0) != len(
                strict_ready_rows
            ):
                issues.append("nonpin_readiness_strict_ready_count_mismatch")
            if int(readiness_counts.get("held_rows") or 0) != len(
                readiness_hold_rows
            ):
                issues.append("nonpin_readiness_hold_count_mismatch")
            reported_active_collisions = int(
                readiness_counts.get("strict_duplicate_collisions_with_active_gold")
                or 0
            )
            reported_forecast_collisions = int(
                readiness_counts.get("strict_duplicate_collisions_within_forecast")
                or 0
            )
            active_unified_path = root / "eng_bench.jsonl"
            active_unified_ids = {
                row_id(row) for row in read_jsonl(active_unified_path)
            } if active_unified_path.is_file() else set()
            held_active_collisions = sum(
                str(row.get("precalibration_collides_with") or "").strip()
                in active_unified_ids
                for row in strict_duplicate_hold_rows
            )
            if reported_active_collisions != held_active_collisions:
                issues.append("nonpin_readiness_active_collision_hold_mismatch")
            if (
                reported_active_collisions + reported_forecast_collisions
                != len(strict_duplicate_hold_rows)
            ):
                issues.append("nonpin_readiness_collision_partition_mismatch")
            precalibration_strict_ready = len(strict_ready_rows)
            precalibration_duplicate_holds = int(
                readiness_counts.get("strict_duplicate_holds") or 0
            )
            precalibration_other_holds = len(readiness_hold_rows) - (
                precalibration_duplicate_holds
            )
            if len(issues) == readiness_issue_start:
                verified_nonpin_readiness = {
                    "path": relative(root, nonpin_readiness_report_path),
                    "sha256": file_sha256(nonpin_readiness_report_path),
                    "strict_ready_if_calibrated": precalibration_strict_ready,
                    "strict_duplicate_holds": precalibration_duplicate_holds,
                    "other_holds": precalibration_other_holds,
                }
    accounted_machine_rows = list(current_eligible_rows)
    accounted_already_active = eligible_already_active
    if calibration_reuse_scope == "nonpin":
        # The non-pin reuse partition intentionally excludes the historically
        # certified pin lane. Add only its still-future rows so the machine
        # ownership ledger remains complete without double-counting active
        # human-reviewed pins or a full-cohort reuse partition.
        historical_pending_rows = [
            row for row in historically_certified_rows if origin(row) == "future"
        ]
        accounted_ids = {row_id(row) for row in accounted_machine_rows}
        historical_pending_ids = {row_id(row) for row in historical_pending_rows}
        if accounted_ids & historical_pending_ids:
            issues.append("nonpin_reuse_historical_pending_overlap")
        else:
            accounted_machine_rows.extend(historical_pending_rows)
        accounted_already_active += sum(
            origin(row) == "current" for row in historically_certified_rows
        )
    current_machine = sum(origin(row) == "current" for row in accounted_machine_rows)
    future_machine = sum(origin(row) == "future" for row in accounted_machine_rows)
    net_saved = max(0, len(accounted_machine_rows) - calibration_rows_remaining)
    current_human = sum(origin(row) == "current" for row in human_rows)
    future_human = sum(origin(row) == "future" for row in human_rows)
    historical_current = sum(origin(row) == "current" for row in historically_certified_rows)
    historical_future = sum(origin(row) == "future" for row in historically_certified_rows)
    nonpin_pending = sum(
        str(row.get("category") or "") != "pin_label"
        for row in current_eligible_rows
    )
    structurally_valid = not issues
    return {
        "schema": "eng_bench_machine_first_responsibility_v2",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "status": "PASS" if structurally_valid else "FAIL",
        "structurally_valid": structurally_valid,
        "active_gold_modified": False,
        "issues": issues,
        "machine_owned": {
            "eligible_after_one_calibration": len(accounted_machine_rows),
            "eligible_rows_already_active_through_human_review": accounted_already_active,
            "current_primary_rows_reclaimable_after_calibration": current_machine,
            "future_rows_machine_owned_after_calibration": future_machine,
            "human_row_by_row_decisions_avoided_net": net_saved,
            "calibrated_lane_decisions_avoided_net": net_saved,
            "source_intake_raw_candidates_processed": intake_raw_rows,
            "source_intake_row_decisions_avoided": intake_machine_removed,
            "source_intake_human_rows_remaining": intake_human_rows,
            "source_intake_machine_corrections": intake_corrected_rows,
            "total_machine_row_decisions_avoided": (
                net_saved + intake_machine_removed + repeat_review_actions_avoided
            ),
            "repeat_review_actions_avoided": repeat_review_actions_avoided,
            "recovered_human_semantic_rows_promoted": recovery_ready_rows,
            "recovered_human_semantic_rows_held": recovery_held_rows,
            "calibration_rows_required_once": calibration_rows,
            "calibration_rows_completed_reused": calibration_completed_reused,
            "calibration_rows_remaining": calibration_rows_remaining,
            "precalibration_strict_ready_nonpin_rows": precalibration_strict_ready,
            "precalibration_strict_duplicate_holds": precalibration_duplicate_holds,
            "precalibration_other_holds": precalibration_other_holds,
            "historically_calibrated_rows_no_new_human_work": len(historically_certified_rows),
            "historically_calibrated_current_rows": historical_current,
            "historically_calibrated_future_rows": historical_future,
            "strict_unique_balance_deferred_rows": len(strict_unique_rows),
            "strict_duplicate_holds": len(strict_duplicate_rows),
            "nonpin_rows_pending_calibration": nonpin_pending,
            "nonpin_calibration_sample_rows": nonpin_calibration_rows,
            "nonpin_calibration_sample_contains_pin_rows": 0,
            "calibration_reuse_scope": calibration_reuse_scope or "none",
        },
        "human_owned": {
            "human_required_rows": len(human_rows),
            "current_assignment_rows_remaining_human": current_human,
            "future_rows_remaining_human": future_human,
            "formal_agreement_rows": formal_rows,
            "formal_agreement_reviewer_actions_remaining": formal_actions,
            "new_preliminary_screen_actions": new_screen_actions,
            "already_issued_preliminary_screens_pending": issued_screen_pending,
            "primary_promotion_rows_pending_in_existing_assignment": primary_pending,
            "source_intake_rows_remaining_human": intake_human_rows,
            "machine_calibration_rows_remaining": calibration_rows_remaining,
        },
        "breakdowns": {
            "machine_by_origin_task_split": count_by(
                accounted_machine_rows, ("origin", "task", "split")
            ),
            "human_by_origin_task_split": count_by(human_rows, ("origin", "task", "split")),
        },
        "inputs": {
            "eligibility_report": relative(root, eligibility_report_path),
            "eligibility_report_sha256": file_sha256(eligibility_report_path),
            "eligible_rows": relative(root, eligible_path),
            "human_required_rows": relative(root, human_path),
            "calibration_checklist": relative(root, calibration_path),
            "agreement_completion": relative(root, agreement_completion_path),
            "agreement_completion_sha256": file_sha256(agreement_completion_path),
            "agreement_contract": relative(root, contract_path) if contract_path else "",
            "historical_finalization": (
                relative(root, historical_finalization_path)
                if historical_finalization_path is not None
                else ""
            ),
            "strict_dedup": (
                relative(root, strict_dedup_path) if strict_dedup_path is not None else ""
            ),
            "nonpin_eligibility_report": (
                relative(root, nonpin_eligibility_report_path)
                if nonpin_eligibility_report_path is not None
                else ""
            ),
            "curation_funnel_reports": verified_funnels,
            "human_semantics_recovery": verified_recovery,
            "calibration_reuse": verified_calibration_reuse,
            "nonpin_precalibration_readiness": verified_nonpin_readiness,
        },
        "policy": {
            "execution_goal": "maximize machine ownership under the release evidence contract and minimize human row actions without lowering Gold quality",
            "machine_scope": "objective train MicroText with frozen evidence-bound calibration, repeatable source-intake work, and recovery/localization/reconciliation of existing human semantics",
            "human_scope": "dev/test evaluation truth, unresolved VisualDiff semantics, ambiguous engineering labels, calibration, and independent agreement",
            "human_assignment_rule": "assign a row to a person only after machine preflight and prefill are exhausted, with an explicit irreducible-judgment reason",
            "gold_promotion": "never direct; machine-certified and human-reviewed outputs both pass normal promotion gates",
        },
    }


def write_report(output_json: Path, report: dict[str, Any]) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    machine = report["machine_owned"]
    human = report["human_owned"]
    lines = [
        "# Machine-First Responsibility Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Status: `{report['status']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Machine-owned rows after one calibration: `{machine['eligible_after_one_calibration']}`",
        f"- Eligible rows already active through human review: `{machine['eligible_rows_already_active_through_human_review']}`",
        f"- Net row-by-row human decisions avoided: `{machine['human_row_by_row_decisions_avoided_net']}`",
        f"- Calibration rows completed through exact review reuse: `{machine['calibration_rows_completed_reused']}`",
        f"- Calibration rows remaining: `{machine['calibration_rows_remaining']}`",
        f"- Current non-pin rows strict-ready if calibration passes: `{machine['precalibration_strict_ready_nonpin_rows']}`",
        f"- Current non-pin strict duplicate holds: `{machine['precalibration_strict_duplicate_holds']}`",
        f"- Current non-pin other holds: `{machine['precalibration_other_holds']}`",
        f"- Source-intake raw candidates processed: `{machine['source_intake_raw_candidates_processed']}`",
        f"- Source-intake machine decisions avoided: `{machine['source_intake_row_decisions_avoided']}`",
        f"- Source-intake human rows remaining: `{machine['source_intake_human_rows_remaining']}`",
        f"- Total machine row decisions avoided: `{machine['total_machine_row_decisions_avoided']}`",
        f"- Repeat human-review actions avoided by recovery: `{machine['repeat_review_actions_avoided']}`",
        f"- Historical calibration, no new human work: `{machine['historically_calibrated_rows_no_new_human_work']}`",
        f"- Strict-unique balance-deferred rows: `{machine['strict_unique_balance_deferred_rows']}`",
        f"- Strict duplicate holds: `{machine['strict_duplicate_holds']}`",
        f"- Non-pin rows pending calibration: `{machine['nonpin_rows_pending_calibration']}`",
        f"- Non-pin-only calibration rows: `{machine['nonpin_calibration_sample_rows']}`",
        f"- Current primary rows reclaimable after calibration: `{machine['current_primary_rows_reclaimable_after_calibration']}`",
        f"- Remaining current human rows: `{human['current_assignment_rows_remaining_human']}`",
        f"- Remaining future human rows: `{human['future_rows_remaining_human']}`",
        f"- Formal agreement actions remaining: `{human['formal_agreement_reviewer_actions_remaining']}`",
        "",
        "## Boundary",
        "",
        f"- Execution goal: {report['policy']['execution_goal']}.",
        f"- Machine: {report['policy']['machine_scope']}.",
        f"- Human: {report['policy']['human_scope']}.",
        f"- Human assignment rule: {report['policy']['human_assignment_rule']}.",
        f"- Promotion: {report['policy']['gold_promotion']}.",
        "",
        "## Issues",
        "",
    ]
    lines.extend(f"- `{issue}`" for issue in report["issues"])
    if not report["issues"]:
        lines.append("- None.")
    output_json.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--eligibility-report", required=True)
    parser.add_argument("--agreement-completion", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--historical-finalization")
    parser.add_argument("--strict-dedup")
    parser.add_argument("--nonpin-eligibility-report")
    parser.add_argument("--human-recovery-report")
    parser.add_argument("--human-recovery-promotion-report")
    parser.add_argument("--calibration-reuse-report")
    parser.add_argument("--nonpin-readiness-report")
    parser.add_argument(
        "--curation-funnel-report",
        action="append",
        default=[],
        help="Verified source-intake funnel report; repeat for multiple intakes.",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    report = build_report(
        root,
        Path(args.eligibility_report),
        Path(args.agreement_completion),
        date_label=args.date_label,
        historical_finalization_path=(
            Path(args.historical_finalization) if args.historical_finalization else None
        ),
        strict_dedup_path=Path(args.strict_dedup) if args.strict_dedup else None,
        nonpin_eligibility_report_path=(
            Path(args.nonpin_eligibility_report)
            if args.nonpin_eligibility_report
            else None
        ),
        curation_funnel_paths=[Path(value) for value in args.curation_funnel_report],
        human_recovery_report_path=(
            Path(args.human_recovery_report) if args.human_recovery_report else None
        ),
        human_recovery_promotion_report_path=(
            Path(args.human_recovery_promotion_report)
            if args.human_recovery_promotion_report
            else None
        ),
        calibration_reuse_report_path=(
            Path(args.calibration_reuse_report)
            if args.calibration_reuse_report
            else None
        ),
        nonpin_readiness_report_path=(
            Path(args.nonpin_readiness_report)
            if args.nonpin_readiness_report
            else None
        ),
    )
    output = resolve(root.resolve(), args.output_json)
    write_report(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["structurally_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
