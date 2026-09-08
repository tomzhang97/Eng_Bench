#!/usr/bin/env python3
"""Recover a bounded low-confidence NASA cartridge tolerance tranche."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import filter_nasa_cartridge_tolerance_candidates as base


def candidate_ids(rows: list[dict[str, Any]], *, label: str) -> set[str]:
    ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError(f"{label} row {index} is missing candidate_id")
        if candidate_id in ids:
            raise ValueError(f"{label} has duplicate candidate_id: {candidate_id}")
        ids.add(candidate_id)
    return ids


def recover_supplemental(
    raw_rows: list[dict[str, Any]],
    prior_selected_rows: list[dict[str, Any]],
    *,
    root: Path,
    min_confidence: float,
    upper_confidence_exclusive: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not 0.0 <= min_confidence < upper_confidence_exclusive <= 1.0:
        raise ValueError(
            "confidence window must satisfy "
            "0 <= min_confidence < upper_confidence_exclusive <= 1"
        )

    raw_ids = candidate_ids(raw_rows, label="raw input")
    prior_ids = candidate_ids(prior_selected_rows, label="prior selected input")
    missing_prior_ids = sorted(prior_ids - raw_ids)
    if missing_prior_ids:
        preview = ", ".join(missing_prior_ids[:5])
        raise ValueError(f"prior selected IDs missing from raw input: {preview}")

    provisional, base_held, base_report = base.shortlist(
        raw_rows,
        root=root,
        min_confidence=min_confidence,
    )
    recovered: list[dict[str, Any]] = []
    held = list(base_held)
    supplemental_hold_reasons: Counter[str] = Counter()

    for source_row in provisional:
        row = dict(source_row)
        candidate_id = str(row["candidate_id"])
        confidence = float(row["ocr_confidence"])
        if candidate_id in prior_ids:
            row["machine_qa_status"] = "machine_held_cartridge_supplemental_filter"
            row["machine_hold_reason"] = "previously_selected"
            held.append(row)
            supplemental_hold_reasons["previously_selected"] += 1
            continue
        if confidence >= upper_confidence_exclusive:
            row["machine_qa_status"] = "machine_held_cartridge_supplemental_filter"
            row["machine_hold_reason"] = "above_supplemental_confidence_window"
            held.append(row)
            supplemental_hold_reasons["above_supplemental_confidence_window"] += 1
            continue

        row["machine_qa_status"] = (
            "supplemental_low_confidence_pass_pending_visual_qa"
        )
        row["machine_qa_notes"] = (
            "Recovered from the original confidence hold after passing the strict "
            "tolerance-token, page-bounds, and tolerance-column geometry checks. "
            "Every crop still requires full visual QA and human review."
        )
        row["supplemental_confidence_floor"] = min_confidence
        row["supplemental_confidence_ceiling_exclusive"] = (
            upper_confidence_exclusive
        )
        row["supplemental_prior_selected_exclusion"] = True
        row["safe_to_merge_gold"] = False
        recovered.append(row)

    recovered.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
            str(row.get("candidate_id") or ""),
        )
    )
    held.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            float(row.get("bbox", [0, 0, 0, 0])[1]),
            float(row.get("bbox", [0, 0, 0, 0])[0]),
            str(row.get("candidate_id") or ""),
        )
    )

    report = {
        "goal": "Gold v2.0 Global",
        "source_doc_id": base.EXPECTED_DOC_ID,
        "raw_rows": len(raw_rows),
        "prior_selected_rows": len(prior_selected_rows),
        "base_geometry_pass_rows": base_report["selected_rows"],
        "recovered_rows": len(recovered),
        "held_rows": len(held),
        "recovered_by_page": dict(
            sorted(
                Counter(str(row.get("page_index")) for row in recovered).items(),
                key=lambda item: int(item[0]),
            )
        ),
        "base_hold_reasons": base_report["hold_reasons"],
        "supplemental_hold_reasons": dict(sorted(supplemental_hold_reasons.items())),
        "min_confidence": min_confidence,
        "upper_confidence_exclusive": upper_confidence_exclusive,
        "prior_overlap_rows": len(
            {str(row["candidate_id"]) for row in recovered} & prior_ids
        ),
        "safe_to_merge_gold": False,
    }
    return recovered, held, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prior-selected", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--min-confidence", type=float, default=0.90)
    parser.add_argument(
        "--upper-confidence-exclusive",
        type=float,
        default=0.98,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    prior_path = (root / args.prior_selected).resolve()
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()

    recovered, held, report = recover_supplemental(
        base.read_jsonl(input_path),
        base.read_jsonl(prior_path),
        root=root,
        min_confidence=args.min_confidence,
        upper_confidence_exclusive=args.upper_confidence_exclusive,
    )
    base.write_jsonl(selected_path, recovered)
    base.write_jsonl(held_path, held)
    report.update(
        {
            "input": input_path.as_posix(),
            "input_sha256": base.sha256(input_path),
            "prior_selected": prior_path.as_posix(),
            "prior_selected_sha256": base.sha256(prior_path),
            "selected_output": selected_path.as_posix(),
            "selected_sha256": base.sha256(selected_path),
            "held_output": held_path.as_posix(),
            "held_sha256": base.sha256(held_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
