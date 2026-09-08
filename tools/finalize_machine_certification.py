#!/usr/bin/env python3
"""Finalize calibrated machine-certified train MicroText rows without editing Gold."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POLICY_VERSION = "1.0"
CERTIFICATION_METHOD = "machine_verified"
CERTIFICATION_TIER = "auto_gold_train"
VALID_DECISIONS = {"correct", "incorrect", "unclear"}


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
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
    return str(row.get("candidate_id") or row.get("item_id") or row.get("id") or "").strip()


def calibration_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{str(key): str(value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def empty_artifact_hash() -> str:
    return hashlib.sha256(b"").hexdigest()


def build_report(
    root: Path,
    eligible_path: Path,
    eligibility_report_path: Path,
    checklist_path: Path,
    output_path: Path,
    *,
    expected_eligibility_report_sha256: str,
    date_label: str,
    minimum_sample_rows: int,
    confidence: float,
    minimum_precision_bound: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    eligible_path = resolve(root, eligible_path)
    eligibility_report_path = resolve(root, eligibility_report_path)
    checklist_path = resolve(root, checklist_path)
    output_path = resolve(root, output_path)
    actual_report_sha = file_sha256(eligibility_report_path)
    issues: list[str] = []
    if actual_report_sha.lower() != expected_eligibility_report_sha256.strip().lower():
        issues.append("eligibility_report_sha256_mismatch")
    eligibility = json.loads(eligibility_report_path.read_text(encoding="utf-8"))
    if str(eligibility.get("policy_version") or "") != POLICY_VERSION:
        issues.append("unsupported_policy_version")
    artifact = (eligibility.get("artifacts") or {}).get("auto_eligible") or {}
    if str(artifact.get("sha256") or "").lower() != file_sha256(eligible_path).lower():
        issues.append("eligible_jsonl_sha256_mismatch")
    eligible = read_jsonl(eligible_path)
    eligible_ids = [candidate_id(row) for row in eligible]
    if not eligible_ids or len(eligible_ids) != len(set(eligible_ids)) or any(not value for value in eligible_ids):
        issues.append("eligible_identity_set_invalid")
    expected_sample = eligibility.get("calibration_sample") or {}
    expected_ids = [str(value) for value in expected_sample.get("candidate_ids") or []]
    checklist = calibration_rows(checklist_path)
    checklist_ids = [str(row.get("candidate_id") or "") for row in checklist]
    if checklist_ids != expected_ids:
        issues.append("calibration_candidate_ids_mismatch")
    expected_id_hash = hashlib.sha256("\n".join(checklist_ids).encode()).hexdigest()
    if expected_id_hash != str(expected_sample.get("candidate_ids_sha256") or ""):
        issues.append("calibration_candidate_ids_sha256_mismatch")
    if len(checklist) < minimum_sample_rows:
        issues.append("calibration_sample_too_small")
    invalid_decisions = [
        f"{row.get('candidate_id')}:{str(row.get('reviewer_decision') or '').lower()}"
        for row in checklist
        if str(row.get("reviewer_decision") or "").lower() not in VALID_DECISIONS
    ]
    if invalid_decisions:
        issues.append("calibration_incomplete_or_invalid_decisions")
    incorrect = sum(str(row.get("reviewer_decision") or "").lower() == "incorrect" for row in checklist)
    unclear = sum(str(row.get("reviewer_decision") or "").lower() == "unclear" for row in checklist)
    correct = sum(str(row.get("reviewer_decision") or "").lower() == "correct" for row in checklist)
    if incorrect:
        issues.append("calibration_contains_incorrect_rows")
    if unclear:
        issues.append("calibration_contains_unclear_rows")
    precision = correct / len(checklist) if checklist else 0.0
    one_sided_lower_bound = (
        math.pow(1.0 - confidence, 1.0 / len(checklist))
        if checklist and not invalid_decisions and incorrect == 0 and unclear == 0
        else 0.0
    )
    if one_sided_lower_bound < minimum_precision_bound:
        issues.append("calibration_precision_bound_below_threshold")
    for row in eligible:
        if str(row.get("reserved_split") or "").strip().lower() != "train":
            issues.append(f"non_train_eligible_row:{candidate_id(row)}")
            break
        if str(row.get("machine_certification_policy_version") or "") != POLICY_VERSION:
            issues.append(f"row_policy_version_mismatch:{candidate_id(row)}")
            break
        if str(row.get("machine_certification_tier") or "") != CERTIFICATION_TIER:
            issues.append(f"row_certification_tier_mismatch:{candidate_id(row)}")
            break
        evidence_sha = str(row.get("machine_certification_evidence_sha256") or "")
        if len(evidence_sha) != 64:
            issues.append(f"row_evidence_sha256_missing:{candidate_id(row)}")
            break
    release_ready = not issues
    certified: list[dict[str, Any]] = []
    attestation_path = output_path.with_name(output_path.stem + "_calibration_attestation.json")
    checklist_sha = file_sha256(checklist_path)
    attestation = {
        "schema": "eng_bench_machine_certification_calibration_attestation_v1",
        "date_label": date_label,
        "policy_version": POLICY_VERSION,
        "eligible_jsonl_sha256": file_sha256(eligible_path),
        "eligibility_report_sha256": actual_report_sha,
        "calibration_checklist_sha256": checklist_sha,
        "calibration_candidate_ids_sha256": expected_id_hash,
        "calibration_rows": len(checklist),
        "correct": correct,
        "incorrect": incorrect,
        "unclear": unclear,
        "confidence": confidence,
        "one_sided_precision_lower_bound": round(one_sided_lower_bound, 8),
        "minimum_precision_lower_bound": minimum_precision_bound,
        "release_ready": release_ready,
        "issues": issues,
    }
    write_json(attestation_path, attestation)
    attestation_sha = file_sha256(attestation_path)
    if release_ready:
        certified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for row in eligible:
            output = dict(row)
            output.update(
                {
                    "review_status": "accepted",
                    "review_source": "machine_certification_policy",
                    "human_reviewed": False,
                    "certification_method": CERTIFICATION_METHOD,
                    "certification_tier": CERTIFICATION_TIER,
                    "certification_policy_version": POLICY_VERSION,
                    "certification_date": certified_at,
                    "certification_eligibility_report": display(root, eligibility_report_path),
                    "certification_eligibility_report_sha256": actual_report_sha,
                    "certification_calibration_checklist": display(root, checklist_path),
                    "certification_calibration_checklist_sha256": checklist_sha,
                    "certification_calibration_attestation": display(root, attestation_path),
                    "certification_calibration_attestation_sha256": attestation_sha,
                    "safe_to_merge_gold": False,
                }
            )
            certified.append(output)
    write_jsonl(output_path, certified)
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "policy_version": POLICY_VERSION,
        "certification_method": CERTIFICATION_METHOD,
        "certification_tier": CERTIFICATION_TIER,
        "active_gold_modified": False,
        "machine_certification_release_ready": release_ready,
        "issues": issues,
        "counts": {
            "eligible_rows": len(eligible),
            "calibration_rows": len(checklist),
            "calibration_correct": correct,
            "calibration_incorrect": incorrect,
            "calibration_unclear": unclear,
            "certified_output_rows": len(certified),
        },
        "statistics": {
            "observed_precision": round(precision, 8),
            "confidence": confidence,
            "one_sided_precision_lower_bound": round(one_sided_lower_bound, 8),
            "minimum_precision_lower_bound": minimum_precision_bound,
            "allowed_incorrect_or_unclear": 0,
        },
        "inputs": {
            "eligible_jsonl": display(root, eligible_path),
            "eligible_jsonl_sha256": file_sha256(eligible_path),
            "eligibility_report": display(root, eligibility_report_path),
            "eligibility_report_sha256": actual_report_sha,
            "calibration_checklist": display(root, checklist_path),
            "calibration_checklist_sha256": checklist_sha,
        },
        "output": {
            "certified_jsonl": display(root, output_path),
            "certified_jsonl_sha256": file_sha256(output_path) if output_path.is_file() else empty_artifact_hash(),
            "calibration_attestation": display(root, attestation_path),
            "calibration_attestation_sha256": attestation_sha,
        },
        "interpretation": (
            "A green report authorizes these rows to enter the normal read-only promotion preview as explicit "
            "machine-certified train MicroText. It does not directly modify Gold. Any calibration error emits an "
            "empty certified artifact and requires a policy revision plus a new frozen sample."
        ),
    }
    return report, certified


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    stats = report["statistics"]
    lines = [
        "# Machine Certification Finalization",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Release ready: `{str(report['machine_certification_release_ready']).lower()}`",
        f"- Eligible rows: `{counts['eligible_rows']}`",
        f"- Calibration rows: `{counts['calibration_rows']}`",
        f"- Incorrect or unclear: `{counts['calibration_incorrect'] + counts['calibration_unclear']}`",
        f"- One-sided precision lower bound: `{stats['one_sided_precision_lower_bound']}`",
        f"- Certified output rows: `{counts['certified_output_rows']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Issues",
        "",
    ]
    lines.extend(f"- `{issue}`" for issue in report["issues"])
    if not report["issues"]:
        lines.append("- none")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--eligible", type=Path, required=True)
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--expected-eligibility-report-sha256", required=True)
    parser.add_argument("--calibration-checklist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--minimum-sample-rows", type=int, default=300)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--minimum-precision-bound", type=float, default=0.99)
    parser.add_argument("--require-passing", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report, _ = build_report(
        root,
        args.eligible,
        args.eligibility_report,
        args.calibration_checklist,
        args.output,
        expected_eligibility_report_sha256=args.expected_eligibility_report_sha256,
        date_label=args.date_label,
        minimum_sample_rows=max(1, args.minimum_sample_rows),
        confidence=args.confidence,
        minimum_precision_bound=args.minimum_precision_bound,
    )
    report_path = resolve(root, args.report_json)
    write_json(report_path, report)
    write_markdown(resolve(root, args.report_md), report)
    print(json.dumps({
        "machine_certification_release_ready": report["machine_certification_release_ready"],
        "eligible_rows": report["counts"]["eligible_rows"],
        "calibration_rows": report["counts"]["calibration_rows"],
        "certified_output_rows": report["counts"]["certified_output_rows"],
        "issues": report["issues"],
        "active_gold_modified": False,
    }, indent=2, ensure_ascii=True))
    return 1 if args.require_passing and not report["machine_certification_release_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
