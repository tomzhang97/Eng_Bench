#!/usr/bin/env python3
"""Select rows that became OCR-only failures after a machine-policy change."""
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
from audit_machine_certification_eligibility import read_jsonl, write_json, write_jsonl
from audit_machine_ocr_rescue import OCR_ONLY_REASONS
from build_machine_certification_delta_audit import unique_rows


def newly_ocr_only_rows(
    baseline_human_rows: list[dict[str, Any]],
    candidate_human_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline = unique_rows(baseline_human_rows, "baseline human-required")
    candidate = unique_rows(candidate_human_rows, "candidate human-required")
    selected: list[dict[str, Any]] = []
    for identifier, row in candidate.items():
        current_reasons = set(row.get("machine_certification_reasons") or [])
        previous_reasons = set((baseline.get(identifier) or {}).get("machine_certification_reasons") or [])
        if (
            current_reasons
            and current_reasons.issubset(OCR_ONLY_REASONS)
            and not (previous_reasons and previous_reasons.issubset(OCR_ONLY_REASONS))
        ):
            selected.append(row)
    return selected


def resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--baseline-human-required", type=Path, required=True)
    parser.add_argument("--candidate-human-required", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    baseline_path = resolve(root, args.baseline_human_required).resolve()
    candidate_path = resolve(root, args.candidate_human_required).resolve()
    output_path = resolve(root, args.output_jsonl).resolve()
    report_path = resolve(root, args.report_json).resolve()
    for path in (output_path, report_path):
        if path.exists():
            raise FileExistsError(f"output already exists: {path}")

    baseline_rows = read_jsonl(baseline_path)
    candidate_rows = read_jsonl(candidate_path)
    selected = newly_ocr_only_rows(baseline_rows, candidate_rows)
    write_jsonl(output_path, selected)
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": args.date_label,
        "baseline_human_required": {
            "path": baseline_path.relative_to(root).as_posix(),
            "rows": len(baseline_rows),
            "sha256": file_sha256(baseline_path),
        },
        "candidate_human_required": {
            "path": candidate_path.relative_to(root).as_posix(),
            "rows": len(candidate_rows),
            "sha256": file_sha256(candidate_path),
        },
        "newly_ocr_only": {
            "path": output_path.relative_to(root).as_posix(),
            "rows": len(selected),
            "sha256": file_sha256(output_path),
            "categories": dict(sorted(Counter(str(row.get("category") or "") for row in selected).items())),
        },
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
    }
    write_json(report_path, report)
    print(f"[OK] Wrote {output_path.relative_to(root).as_posix()}")
    print(f"[OK] Selected {len(selected)} newly OCR-only rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

