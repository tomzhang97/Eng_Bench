#!/usr/bin/env python3
"""Apply recorded crop-level visual QA to selected NRCS Nebraska candidates."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


TEXT_CORRECTIONS = {
    "ocrcand__b7212216aaca0a91146e": "BYPASS VALVE BOX",
    "ocrcand__c6d51f81da29b46de1ff": "20 gpm,",
    "ocrcand__4fe937e3b2f1c63b928d": "10 gpm,",
    "ocrcand__68b6add62adb9a50f6f5": "15 gpm,",
    "ocrcand__c0930d631dcd5420fad4": "25 gpm,",
    "ocrcand__ddc39ced834e71917a8a": '12" Max',
}
VISUAL_HOLDS = {
    "ocrcand__7c58166a06db75b7221c": "crop_contains_multiple_pressure_values",
    "ocrcand__d6ca218f69b8e745bb67": "crop_contains_multiple_pressure_values",
    "ocrcand__f2635bfc73cea3880f74": "bbox_spans_multiple_table_rows",
}


def apply_visual_qa(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source in rows:
        row = dict(source)
        candidate_id = str(row.get("candidate_id") or "")
        seen_ids.add(candidate_id)
        if candidate_id in VISUAL_HOLDS:
            row.update(
                review_status="machine_held",
                machine_qa_status="held_by_nrcs_ne_visual_qa",
                machine_visual_qa_status="held",
                machine_visual_qa_notes=VISUAL_HOLDS[candidate_id],
                safe_to_merge_gold=False,
            )
            held.append(row)
            continue
        if candidate_id in TEXT_CORRECTIONS:
            original = str(row.get("proposed_text") or "")
            row.update(
                proposed_text=TEXT_CORRECTIONS[candidate_id],
                machine_visual_original_text=original,
                machine_visual_qa_status="corrected",
                machine_visual_qa_notes="machine_visual_exact_text_correction",
            )
        else:
            row.update(
                machine_visual_qa_status="confirmed",
                machine_visual_qa_notes="crop_and_context_visually_confirmed",
            )
        row.update(
            review_status="needs_review",
            promotion_state="unreviewed_candidate",
            safe_to_merge_gold=False,
        )
        selected.append(row)

    missing_decisions = sorted((set(TEXT_CORRECTIONS) | set(VISUAL_HOLDS)) - seen_ids)
    selected.sort(key=lambda row: (int(row.get("page_index") or 0), tuple(row.get("bbox") or ())))
    held.sort(key=lambda row: (int(row.get("page_index") or 0), tuple(row.get("bbox") or ())))
    report = {
        "goal": "Gold v2.0 Global",
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "corrected_rows": sum(row.get("machine_visual_qa_status") == "corrected" for row in selected),
        "selected_by_category": dict(sorted(Counter(row.get("category") for row in selected).items())),
        "held_reasons": dict(sorted(Counter(row.get("machine_visual_qa_notes") for row in held).items())),
        "missing_recorded_decisions": missing_decisions,
        "all_rows_visually_audited": not missing_decisions and len(selected) + len(held) == len(rows),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
    }
    return selected, held, report


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--expect-input", type=int, default=50)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    rows = read_jsonl(resolve(args.input))
    selected, held, report = apply_visual_qa(rows)
    report["valid"] = (
        len(rows) == args.expect_input
        and report["all_rows_visually_audited"]
        and len(selected) > 0
    )
    if len(rows) != args.expect_input:
        report["error"] = f"unexpected_input_count:{len(rows)}!={args.expect_input}"
    write_jsonl(resolve(args.selected_output), selected)
    write_jsonl(resolve(args.held_output), held)
    report_path = resolve(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
