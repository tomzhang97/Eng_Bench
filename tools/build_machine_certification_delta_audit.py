#!/usr/bin/env python3
"""Materialize a hash-bound visual audit pack for newly machine-eligible rows."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import file_sha256
from audit_machine_certification_eligibility import (
    identity_for,
    materialize_calibration_pack,
    read_jsonl,
    write_json,
    write_jsonl,
)


def unique_rows(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = identity_for(row)
        if not identifier:
            raise ValueError(f"{label} row is missing an identity")
        if identifier in result:
            raise ValueError(f"{label} contains duplicate identity: {identifier}")
        result[identifier] = row
    return result


def eligible_delta(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline = unique_rows(baseline_rows, "baseline")
    candidate = unique_rows(candidate_rows, "candidate")
    removed = sorted(set(baseline) - set(candidate))
    if removed:
        preview = ", ".join(removed[:5])
        raise ValueError(f"candidate cohort removed {len(removed)} baseline rows: {preview}")
    return [row for identifier, row in candidate.items() if identifier not in baseline]


def candidate_from_additions(
    baseline_rows: list[dict[str, Any]],
    addition_groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    candidate = list(baseline_rows)
    for rows in addition_groups:
        candidate.extend(rows)
    return candidate


def resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a visual audit pack for the additive delta between two machine cohorts."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--baseline-jsonl", type=Path, required=True)
    candidate_group = parser.add_mutually_exclusive_group(required=True)
    candidate_group.add_argument("--candidate-jsonl", type=Path)
    candidate_group.add_argument(
        "--candidate-addition-jsonl",
        type=Path,
        action="append",
        help=(
            "Additive eligible JSONL to append to the baseline. Repeat for multiple "
            "additions; the tool writes a hash-bound combined candidate snapshot."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    baseline_path = resolve(root, args.baseline_jsonl).resolve()
    output_dir = resolve(root, args.output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")

    baseline_rows = read_jsonl(baseline_path)
    addition_reports: list[dict[str, Any]] = []
    if args.candidate_jsonl:
        candidate_path = resolve(root, args.candidate_jsonl).resolve()
        candidate_rows = read_jsonl(candidate_path)
        candidate_mode = "complete_cohort"
    else:
        addition_paths = [
            resolve(root, path).resolve()
            for path in (args.candidate_addition_jsonl or [])
        ]
        addition_groups = [read_jsonl(path) for path in addition_paths]
        candidate_rows = candidate_from_additions(baseline_rows, addition_groups)
        candidate_path = output_dir / "candidate_snapshot.jsonl"
        candidate_mode = "baseline_plus_additions"
        addition_reports = [
            {
                "path": path.relative_to(root).as_posix(),
                "rows": len(rows),
                "sha256": file_sha256(path),
            }
            for path, rows in zip(addition_paths, addition_groups, strict=True)
        ]
    delta = eligible_delta(baseline_rows, candidate_rows)
    if any(bool(row.get("safe_to_merge_gold")) for row in delta):
        raise ValueError("delta contains a row marked safe_to_merge_gold")

    output_dir.mkdir(parents=True)
    if candidate_mode == "baseline_plus_additions":
        write_jsonl(candidate_path, candidate_rows)

    delta_path = output_dir / "auto_eligible_delta.jsonl"
    write_jsonl(delta_path, delta)
    pack_dir = output_dir / "audit_pack"
    checklist_path, index_path = materialize_calibration_pack(root, delta, pack_dir)

    report = {
        "goal": "Gold v2.0 Global",
        "date_label": args.date_label,
        "baseline": {
            "path": baseline_path.relative_to(root).as_posix(),
            "rows": len(baseline_rows),
            "sha256": file_sha256(baseline_path),
        },
        "candidate": {
            "path": candidate_path.relative_to(root).as_posix(),
            "rows": len(candidate_rows),
            "sha256": file_sha256(candidate_path),
            "mode": candidate_mode,
            "additions": addition_reports,
        },
        "delta": {
            "path": delta_path.relative_to(root).as_posix(),
            "rows": len(delta),
            "sha256": file_sha256(delta_path),
            "categories": dict(sorted(Counter(str(row.get("category") or "") for row in delta).items())),
            "source_text_contracts": dict(
                sorted(
                    Counter(
                        str(row.get("machine_certification_source_text_contract") or "")
                        for row in delta
                    ).items()
                )
            ),
        },
        "audit_pack": {
            "path": pack_dir.relative_to(root).as_posix(),
            "checklist": checklist_path.relative_to(root).as_posix(),
            "checklist_sha256": file_sha256(checklist_path),
            "index": index_path.relative_to(root).as_posix(),
        },
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
    }
    report_path = output_dir / "machine_certification_delta_audit_report.json"
    write_json(report_path, report)
    print(f"[OK] Wrote {report_path.relative_to(root).as_posix()}")
    print(f"[OK] Materialized {len(delta)} newly eligible rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
