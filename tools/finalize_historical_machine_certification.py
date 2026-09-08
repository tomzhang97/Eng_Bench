#!/usr/bin/env python3
"""Finalize historically calibrated rows into a balance-deferred staging lane.

This tool verifies a frozen eligibility cohort and a retrospective calibration
audit, emits a hash-bound attestation, and marks only qualified train rows as
machine-certified. It never edits active Gold or declares category balance.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_machine_certification_eligibility as eligibility
from audit_active_gold_provenance import file_sha256


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def active_hashes(root: Path) -> dict[str, str]:
    paths = (
        "visualdiff/annotations/visualdiff_pairs.jsonl",
        "visualdiff/annotations/visualdiff_questions.jsonl",
        "microtext/annotations/microtext_items.jsonl",
        "microtext/annotations/microtext_questions.jsonl",
        "eng_bench.jsonl",
    )
    return {path: file_sha256(root / path) for path in paths}


def artifact_path(root: Path, record: dict[str, Any]) -> Path:
    return resolve(root, str(record.get("path") or ""))


def verify_artifact(root: Path, record: dict[str, Any], issue: str, issues: list[str]) -> Path:
    path = artifact_path(root, record)
    expected = str(record.get("sha256") or "").lower()
    if not path.is_file():
        issues.append(f"missing_{issue}")
    elif not expected or file_sha256(path).lower() != expected:
        issues.append(f"{issue}_sha256_mismatch")
    return path


def build_finalization(
    root: Path,
    eligibility_dir: Path,
    calibration_dir: Path,
    output_dir: Path,
    *,
    date_label: str,
    category: str,
) -> dict[str, Any]:
    root = root.resolve()
    eligibility_dir = resolve(root, eligibility_dir)
    calibration_dir = resolve(root, calibration_dir)
    output_dir = resolve(root, output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    before = active_hashes(root)
    issues: list[str] = []

    eligibility_report_path = eligibility_dir / "eligibility_report.json"
    calibration_report_path = calibration_dir / "historical_machine_calibration_report.json"
    if not eligibility_report_path.is_file():
        issues.append("missing_eligibility_report")
        eligibility_report: dict[str, Any] = {}
    else:
        eligibility_report = json.loads(eligibility_report_path.read_text(encoding="utf-8"))
    if not calibration_report_path.is_file():
        issues.append("missing_historical_calibration_report")
        calibration_report: dict[str, Any] = {}
    else:
        calibration_report = json.loads(calibration_report_path.read_text(encoding="utf-8"))

    if str(eligibility_report.get("policy_version") or "") != eligibility.POLICY_VERSION:
        issues.append("eligibility_policy_version_mismatch")
    if str(calibration_report.get("policy_version") or "") != eligibility.POLICY_VERSION:
        issues.append("calibration_policy_version_mismatch")
    if eligibility_report.get("active_gold_modified") is not False:
        issues.append("eligibility_modified_active_gold")
    if calibration_report.get("active_gold_modified") is not False:
        issues.append("calibration_modified_active_gold")
    if calibration_report.get("selection_uses_human_outcome") is not False:
        issues.append("historical_selection_not_pre_human")
    if calibration_report.get("human_outcome_used_only_for_evaluation") is not True:
        issues.append("historical_outcome_boundary_missing")

    eligible_record = (eligibility_report.get("artifacts") or {}).get("auto_eligible") or {}
    eligible_path = verify_artifact(root, eligible_record, "eligible_jsonl", issues)
    calibration_artifacts = calibration_report.get("artifacts") or {}
    labeled_record = calibration_artifacts.get("historical_labeled") or {}
    qualified_record = calibration_artifacts.get("qualified_cohort_pending_policy") or {}
    labeled_path = verify_artifact(root, labeled_record, "historical_labeled", issues)
    qualified_path = verify_artifact(root, qualified_record, "qualified_cohort", issues)

    calibration_input_sha = str(
        ((calibration_report.get("inputs") or {}).get("cohort_sha256") or "")
    ).lower()
    eligible_sha = str(eligible_record.get("sha256") or "").lower()
    if calibration_input_sha != eligible_sha:
        issues.append("calibration_cohort_not_frozen_eligibility_artifact")

    metrics = (
        ((calibration_report.get("calibration") or {}).get("by_category") or {}).get(category)
        or {}
    )
    if metrics.get("qualifies") is not True:
        issues.append("category_not_historically_calibrated")
    if int(metrics.get("rows") or 0) < int(
        (calibration_report.get("calibration") or {}).get("minimum_sample_rows") or 300
    ):
        issues.append("historical_calibration_sample_too_small")
    if int(metrics.get("incorrect") or 0) or int(metrics.get("unclear") or 0):
        issues.append("historical_calibration_has_errors")
    if float(metrics.get("one_sided_precision_lower_bound") or 0.0) < float(
        (calibration_report.get("calibration") or {}).get("minimum_precision_lower_bound") or 0.99
    ):
        issues.append("historical_calibration_precision_too_low")

    eligible_rows = eligibility.read_jsonl(eligible_path) if eligible_path.is_file() else []
    qualified_rows = eligibility.read_jsonl(qualified_path) if qualified_path.is_file() else []
    eligible_by_id = {eligibility.identity_for(row): row for row in eligible_rows}
    qualified_ids: list[str] = []
    for row in qualified_rows:
        candidate_id = eligibility.identity_for(row)
        qualified_ids.append(candidate_id)
        source = eligible_by_id.get(candidate_id)
        if not candidate_id or source is None:
            issues.append(f"qualified_row_missing_from_eligibility:{candidate_id or 'blank'}")
            continue
        if str(row.get("category") or "") != category:
            issues.append(f"qualified_row_category_mismatch:{candidate_id}")
        if str(row.get("reserved_split") or "").lower() != "train":
            issues.append(f"qualified_row_not_train:{candidate_id}")
        if str(row.get("machine_certification_policy_version") or "") != eligibility.POLICY_VERSION:
            issues.append(f"qualified_row_policy_mismatch:{candidate_id}")
    if len(qualified_ids) != len(set(qualified_ids)):
        issues.append("duplicate_qualified_candidate_ids")
    expected_covered = int(
        (calibration_report.get("cohort_coverage") or {}).get("qualified_pending_policy_rows") or 0
    )
    if len(qualified_rows) != expected_covered:
        issues.append("qualified_cohort_count_mismatch")

    release_ready = not issues
    attestation_path = output_dir / "historical_calibration_attestation.json"
    attestation = {
        "schema": "eng_bench_machine_certification_historical_attestation_v1",
        "date_label": date_label,
        "policy_version": eligibility.POLICY_VERSION,
        "calibration_method": "retrospective_human_history",
        "category": category,
        "calibration_rows": int(metrics.get("rows") or 0),
        "correct": int(metrics.get("correct") or 0),
        "incorrect": int(metrics.get("incorrect") or 0),
        "unclear": int(metrics.get("unclear") or 0),
        "confidence": float((calibration_report.get("calibration") or {}).get("confidence") or 0.95),
        "one_sided_precision_lower_bound": float(metrics.get("one_sided_precision_lower_bound") or 0.0),
        "minimum_precision_lower_bound": float(
            (calibration_report.get("calibration") or {}).get("minimum_precision_lower_bound") or 0.99
        ),
        "eligibility_report": display(root, eligibility_report_path),
        "eligibility_report_sha256": file_sha256(eligibility_report_path)
        if eligibility_report_path.is_file()
        else "",
        "historical_calibration_report": display(root, calibration_report_path),
        "historical_calibration_report_sha256": file_sha256(calibration_report_path)
        if calibration_report_path.is_file()
        else "",
        "historical_labeled_evidence": display(root, labeled_path),
        "historical_labeled_evidence_sha256": file_sha256(labeled_path)
        if labeled_path.is_file()
        else "",
        "qualified_cohort_sha256": file_sha256(qualified_path) if qualified_path.is_file() else "",
        "qualified_rows": len(qualified_rows),
        "release_ready": release_ready,
        "balance_deferred": True,
        "active_gold_modified": False,
        "issues": sorted(set(issues)),
    }
    write_json(attestation_path, attestation)
    attestation_sha = file_sha256(attestation_path)

    certified_rows: list[dict[str, Any]] = []
    if release_ready:
        certified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        labeled_sha = file_sha256(labeled_path)
        eligibility_report_sha = file_sha256(eligibility_report_path)
        for row in qualified_rows:
            output = dict(row)
            output.update(
                {
                    "review_status": "accepted",
                    "review_source": "machine_certification_policy",
                    "human_reviewed": False,
                    "certification_method": "machine_verified",
                    "certification_tier": "auto_gold_train",
                    "certification_policy_version": eligibility.POLICY_VERSION,
                    "certification_calibration_method": "retrospective_human_history",
                    "certification_eligibility_report": display(root, eligibility_report_path),
                    "certification_eligibility_report_sha256": eligibility_report_sha,
                    "certification_calibration_checklist_sha256": labeled_sha,
                    "certification_calibration_evidence": display(root, labeled_path),
                    "certification_calibration_evidence_sha256": labeled_sha,
                    "certification_calibration_attestation": display(root, attestation_path),
                    "certification_calibration_attestation_sha256": attestation_sha,
                    "certified_at": certified_at,
                    "promotion_state": "machine_certified_balance_deferred",
                    "safe_to_merge_gold": False,
                }
            )
            certified_rows.append(output)

    certified_path = output_dir / "machine_certified_balance_deferred.jsonl"
    write_jsonl(certified_path, certified_rows)
    after = active_hashes(root)
    report = {
        "schema": "eng_bench_historical_machine_certification_finalization_v1",
        "date_label": date_label,
        "goal": "Gold v2.0 Global",
        "category": category,
        "release_ready": release_ready,
        "balance_deferred": True,
        "certified_rows": len(certified_rows),
        "active_gold_modified": before != after,
        "issues": sorted(set(issues)),
        "artifacts": {
            "attestation": {"path": display(root, attestation_path), "sha256": attestation_sha},
            "certified_balance_deferred": {
                "path": display(root, certified_path),
                "sha256": file_sha256(certified_path),
            },
        },
        "active_hashes_before": before,
        "active_hashes_after": after,
    }
    write_json(output_dir / "historical_machine_certification_finalization_report.json", report)
    (output_dir / "historical_machine_certification_finalization_report.md").write_text(
        "\n".join(
            [
                "# Historical Machine Certification Finalization",
                "",
                "**Goal:** Gold v2.0 Global",
                "",
                f"- Category: `{category}`",
                f"- Release-contract ready: `{str(release_ready).lower()}`",
                f"- Machine-certified balance-deferred rows: `{len(certified_rows)}`",
                f"- Active Gold modified: `{str(before != after).lower()}`",
                "",
                "These rows remain outside active Gold until category balance and the normal promotion transaction permit release.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finalize historically calibrated rows without editing active Gold."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--eligibility-dir", type=Path, required=True)
    parser.add_argument("--calibration-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--category", default="pin_label")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    report = build_finalization(
        args.root.resolve(),
        args.eligibility_dir,
        args.calibration_dir,
        args.output_dir,
        date_label=args.date_label,
        category=args.category,
    )
    print(
        json.dumps(
            {
                "release_ready": report["release_ready"],
                "balance_deferred": report["balance_deferred"],
                "certified_rows": report["certified_rows"],
                "active_gold_modified": report["active_gold_modified"],
                "issues": report["issues"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["release_ready"] or not args.require_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
