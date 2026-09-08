#!/usr/bin/env python3
"""Apply high-precision machine QA to NRCS ND stock-water OCR candidates."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DOC_PREFIX = "nrcs_nd_stockwater_"
EXPLICIT_DIMENSION_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:'|FT)\s*(?:-\s*\d+(?:\s+\d+/\d+)?\s*\")?"
    r"\s*(?:MIN|MAX)?$|^\d+(?:\.\d+)?\s*\"\s*(?:MIN|MAX)?$",
    re.IGNORECASE,
)
EQUIPMENT_LABEL_RE = re.compile(
    r"^(?:"
    r"ENCLOSED STORAGE TANK|CONCRETE TANK|STORAGE TANK|STOCKWATER TANK|"
    r"SUBMERSIBLE PUMP|PRESSURE TANK|SURGE TANK(?: \(4 GALLON MIN\.\))?|"
    r"PRESSURE REDUCER VALVE|PRESSURE RELIEF VALVE|RELIEF VALVE|DRAIN VALVE|"
    r"FLOAT VALVE|GATE VALVE|HOSE BIB VALVE|BALL VALVE|GATE OR BALL VALVE|"
    r"OPTIONAL FROST FREE VALVE|MIN 1\" DIA BALL VALVE|VALVE,? IF REQ'D|"
    r"PUMP|TANK"
    r")$",
    re.IGNORECASE,
)
COMPONENT_LABEL_RE = re.compile(
    r"^(?:5\" DIA\.? TREATED WOOD POST|GUARD POST|TREATED WOOD POST|WOOD BRACE)$",
    re.IGNORECASE,
)


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def classify_row(row: dict[str, Any]) -> tuple[str, str]:
    if not str(row.get("doc_id") or "").startswith(DOC_PREFIX):
        return "", "unexpected_source"
    confidence = float(row.get("ocr_confidence") or 0.0)
    if confidence < 0.96:
        return "", "ocr_confidence_below_0_96"
    text = " ".join(str(row.get("proposed_text") or "").split()).strip()
    category = str(row.get("category") or "").strip()
    if category == "dimension_value":
        if EXPLICIT_DIMENSION_RE.fullmatch(text):
            return "dimension_value", "explicit_unit_dimension"
        return "", "unitless_or_table_quantity"
    if category == "equipment_tag":
        if EQUIPMENT_LABEL_RE.fullmatch(text):
            return "equipment_tag", "concise_equipment_label"
        return "", "equipment_heading_or_prose_fragment"
    if category == "pin_label":
        if COMPONENT_LABEL_RE.fullmatch(text):
            return "component_value", "recategorized_structural_component"
        return "", "non_pcb_pin_false_category"
    if category == "room_label":
        if text.casefold() == "pump house":
            return "room_label", "specific_facility_room_label"
        return "", "generic_room_fragment"
    return "", "unsupported_category"


def filter_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    provisional: list[tuple[dict[str, Any], str, str]] = []
    held: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for source in rows:
        row = dict(source)
        category, reason = classify_row(row)
        if not category:
            row.update(
                review_status="machine_held",
                machine_qa_status="held_by_nrcs_nd_high_precision_filter",
                machine_qa_notes=reason,
                safe_to_merge_gold=False,
            )
            held.append(row)
            reason_counts[reason] += 1
            continue
        provisional.append((row, category, reason))

    provisional.sort(
        key=lambda item: (
            -float(item[0].get("ocr_confidence") or 0.0),
            str(item[0].get("doc_id") or ""),
            str(item[0].get("candidate_id") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row, category, reason in provisional:
        key = (category, normalize_text(row.get("proposed_text")))
        if key in seen:
            row.update(
                category=category,
                review_status="machine_held",
                machine_qa_status="held_by_nrcs_nd_high_precision_filter",
                machine_qa_notes="duplicate_normalized_text",
                safe_to_merge_gold=False,
            )
            held.append(row)
            reason_counts["duplicate_normalized_text"] += 1
            continue
        seen.add(key)
        row.update(
            category=category,
            review_status="needs_review",
            promotion_state="unreviewed_candidate",
            machine_qa_status="selected_by_nrcs_nd_high_precision_filter",
            machine_qa_notes=reason,
            safe_to_merge_gold=False,
        )
        selected.append(row)
        reason_counts[reason] += 1

    selected.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row.get("bbox") or ()),
        )
    )
    held.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row.get("bbox") or ()),
        )
    )
    report = {
        "goal": "Gold v2.0 Global",
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_by_category": dict(sorted(Counter(row["category"] for row in selected).items())),
        "selected_by_doc": dict(sorted(Counter(row["doc_id"] for row in selected).items())),
        "decision_reasons": dict(sorted(reason_counts.items())),
        "safe_to_merge_gold": False,
    }
    return selected, held, report


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--expect-input", type=int, default=251)
    args = parser.parse_args(argv)

    root = args.root.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    rows = read_jsonl(resolve(args.input))
    selected, held, report = filter_rows(rows)
    if len(rows) != args.expect_input:
        report["error"] = f"unexpected_input_count:{len(rows)}!={args.expect_input}"
        report["valid"] = False
    else:
        report["valid"] = len(selected) > 0 and len(selected) + len(held) == len(rows)
    write_jsonl(resolve(args.selected_output), selected)
    write_jsonl(resolve(args.held_output), held)
    report_path = resolve(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
