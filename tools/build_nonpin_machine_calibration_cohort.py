#!/usr/bin/env python3
"""Freeze the non-pin subset of a machine-certification eligibility run.

This tool does not certify or promote rows. It verifies the source report and
eligible artifact, preserves the original current/future cohort boundary, and
writes a hash-bound input cohort for a non-pin-only calibration rerun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def build_cohort(
    *,
    root: Path,
    eligibility_report_path: Path,
    output_dir: Path,
    expected_report_sha256: str = "",
) -> dict[str, Any]:
    issues: list[str] = []
    actual_report_sha = file_sha256(eligibility_report_path)
    if expected_report_sha256 and actual_report_sha != expected_report_sha256.lower():
        issues.append("eligibility_report_sha256_mismatch")
    report = json.loads(eligibility_report_path.read_text(encoding="utf-8"))
    artifact = (report.get("artifacts") or {}).get("auto_eligible") or {}
    eligible_path = resolve(root, str(artifact.get("path") or ""))
    if not eligible_path.is_file():
        raise FileNotFoundError(eligible_path)
    actual_eligible_sha = file_sha256(eligible_path)
    if actual_eligible_sha != str(artifact.get("sha256") or "").lower():
        issues.append("eligible_artifact_sha256_mismatch")

    eligible = read_jsonl(eligible_path)
    nonpin = [row for row in eligible if str(row.get("category") or "") != "pin_label"]
    pin_rows = len(eligible) - len(nonpin)
    by_origin: dict[str, list[dict[str, Any]]] = {"current": [], "future": []}
    unknown_origins: Counter[str] = Counter()
    identities: set[str] = set()
    for row in nonpin:
        identity = str(row.get("candidate_id") or row.get("record_id") or "").strip()
        if not identity or identity in identities:
            issues.append("nonpin_identity_set_invalid")
        identities.add(identity)
        origin = str(row.get("machine_certification_origin_cohort") or "").strip()
        if origin not in by_origin:
            unknown_origins[origin or "missing"] += 1
            continue
        clean = dict(row)
        clean["safe_to_merge_gold"] = False
        clean["promotion_state"] = "machine_calibration_pending"
        by_origin[origin].append(clean)
    if unknown_origins:
        issues.append("unknown_machine_certification_origin_cohort")

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, Any]] = {}
    for origin in ("current", "future"):
        path = output_dir / f"nonpin_{origin}.jsonl"
        write_jsonl(path, by_origin[origin])
        artifacts[origin] = {
            "path": display(root, path),
            "sha256": file_sha256(path),
            "rows": len(by_origin[origin]),
        }

    result = {
        "schema": "eng_bench_nonpin_machine_calibration_cohort_v1",
        "goal": "Gold v2.0 Global",
        "active_gold_modified": False,
        "source": {
            "eligibility_report": display(root, eligibility_report_path),
            "eligibility_report_sha256": actual_report_sha,
            "eligible_artifact": display(root, eligible_path),
            "eligible_artifact_sha256": actual_eligible_sha,
        },
        "counts": {
            "eligible_input_rows": len(eligible),
            "excluded_historically_calibrated_pin_rows": pin_rows,
            "nonpin_rows": len(nonpin),
            "categories": dict(sorted(Counter(str(row.get("category") or "") for row in nonpin).items())),
            "origins": {key: len(value) for key, value in by_origin.items()},
        },
        "artifacts": artifacts,
        "issues": sorted(set(issues)),
        "valid": not issues,
        "interpretation": (
            "This cohort removes the historically calibrated pin lane before sampling. "
            "It is calibration input only and is not safe to merge into Gold."
        ),
    }
    report_path = output_dir / "nonpin_cohort_report.json"
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--expected-report-sha256", default="")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    result = build_cohort(
        root=root,
        eligibility_report_path=resolve(root, args.eligibility_report),
        output_dir=resolve(root, args.output_dir),
        expected_report_sha256=args.expected_report_sha256,
    )
    print(json.dumps({"valid": result["valid"], **result["counts"]}, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
