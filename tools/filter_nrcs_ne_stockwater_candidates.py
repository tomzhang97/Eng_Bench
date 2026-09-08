#!/usr/bin/env python3
"""Apply high-precision machine QA to NRCS Nebraska stock-water OCR candidates."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DOC_ID = "nrcs_ne_stockwater_pipeline_handbook_2008"
EXPLICIT_DIMENSION_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?\s*(?:'|FT)|\d+(?:\.\d+)?\s*\"\s*(?:MIN|MAX)?)$",
    re.IGNORECASE,
)
PROCESS_VALUE_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:GPM|PSI|PSIG)$",
    re.IGNORECASE,
)
EQUIPMENT_LABEL_RE = re.compile(
    r"^(?:"
    r"SHUT\s*OFF VALVE|DRAIN VALVE|CHECK VALVE|GATE VALVE|BYPASS VALVE|"
    r"PRESSURE RELIEF VALVE|PRESSURE REDUCING VALVE|FLOAT VALVE|"
    r"FLOAT VALVE BOX|FLOW RATE CONTROLLER VALVES|GRAVEL FILTER|"
    r"SAND\s*/\s*GRAVEL FILTER\s*/\s*COLLECTOR|STOCK TANK|"
    r"SUBMERSIBLE PUMP|THREE PISTON PUMP|PUMP CYLINDER|"
    r"WIND GENERATOR POWERED PUMP|DEEP WELL PUMP CYLINDER|RAM PUMP|"
    r"PUMP UP RELAY|GOVERNOR|CONCRETE TANK|FROST PROOF CONCRETE TANK|"
    r"FROST FREE FIBERGLASS TANK|MANUFACTURED STEEL TANK|"
    r"FABRICATED STEEL STORAGE TANK|FIBERGLASS STORAGE TANK|"
    r"REMOTE TANK FLOAT OPERATED SWITCHING EQUIPMENT"
    r")$",
    re.IGNORECASE,
)
COMPONENT_LABEL_RE = re.compile(
    r"^(?:MARKER POST|GUARD POST|VALVE SLEEVE|VALVE TUBE|VALVE PORT)$",
    re.IGNORECASE,
)
PROCESS_LABEL_RE = re.compile(
    r"^(?:STORAGE TANK OUTLET|PIPELINE TANK INLET)$",
    re.IGNORECASE,
)


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def classify_row(row: dict[str, Any]) -> tuple[str, str, str]:
    if str(row.get("doc_id") or "") != DOC_ID:
        return "", "", "unexpected_source"
    confidence = float(row.get("ocr_confidence") or 0.0)
    text = " ".join(str(row.get("proposed_text") or "").split()).strip()
    category = str(row.get("category") or "").strip()
    if category == "dimension_value":
        if confidence < 0.96:
            return "", "", "ocr_confidence_below_category_floor"
        if EXPLICIT_DIMENSION_RE.fullmatch(text):
            return "dimension_value", "ocr_objective_human_verification", "explicit_unit_dimension"
        return "", "", "unitless_table_or_chart_value"
    if category == "process_value":
        if confidence < 0.96:
            return "", "", "ocr_confidence_below_category_floor"
        if PROCESS_VALUE_RE.fullmatch(text):
            return "process_value", "ocr_objective_human_verification", "explicit_unit_process_value"
        return "", "", "unsupported_process_value"
    if confidence < 0.98:
        return "", "", "ocr_confidence_below_0_98"
    if category == "equipment_tag":
        if EQUIPMENT_LABEL_RE.fullmatch(text):
            return "equipment_tag", "human_semantic_review", "concise_equipment_label"
        if COMPONENT_LABEL_RE.fullmatch(text):
            return "component_value", "human_semantic_review", "recategorized_component_label"
        if PROCESS_LABEL_RE.fullmatch(text):
            return "process_label", "human_semantic_review", "recategorized_process_label"
        return "", "", "equipment_heading_prose_or_generic_repeat"
    if category == "pin_label":
        if COMPONENT_LABEL_RE.fullmatch(text):
            return "component_value", "human_semantic_review", "recategorized_component_label"
        return "", "", "non_pcb_pin_false_category"
    if category == "room_label":
        return "", "", "facility_component_misclassified_as_room"
    return "", "", "unsupported_category"


def filter_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    provisional: list[tuple[dict[str, Any], str, str, str]] = []
    held: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for source in rows:
        row = dict(source)
        category, route, reason = classify_row(row)
        if not category:
            row.update(
                review_status="machine_held",
                machine_qa_status="held_by_nrcs_ne_high_precision_filter",
                machine_qa_notes=reason,
                safe_to_merge_gold=False,
            )
            held.append(row)
            reason_counts[reason] += 1
            continue
        provisional.append((row, category, route, reason))

    provisional.sort(
        key=lambda item: (
            -float(item[0].get("ocr_confidence") or 0.0),
            str(item[0].get("candidate_id") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row, category, route, reason in provisional:
        key = (category, normalize_text(row.get("proposed_text")))
        if key in seen:
            row.update(
                category=category,
                review_status="machine_held",
                machine_qa_status="held_by_nrcs_ne_high_precision_filter",
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
            machine_review_route=route,
            machine_qa_status="selected_by_nrcs_ne_high_precision_filter",
            machine_qa_notes=reason,
            safe_to_merge_gold=False,
        )
        selected.append(row)
        reason_counts[reason] += 1

    sort_key = lambda row: (
        int(row.get("page_index") or 0),
        tuple(row.get("bbox") or ()),
        str(row.get("candidate_id") or ""),
    )
    selected.sort(key=sort_key)
    held.sort(key=sort_key)
    report = {
        "goal": "Gold v2.0 Global",
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "human_rows_avoided": len(held),
        "human_labor_reduction_percent": round(100.0 * len(held) / len(rows), 4) if rows else 0.0,
        "selected_by_category": dict(sorted(Counter(row["category"] for row in selected).items())),
        "selected_by_route": dict(sorted(Counter(row["machine_review_route"] for row in selected).items())),
        "decision_reasons": dict(sorted(reason_counts.items())),
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
    parser.add_argument("--expect-input", type=int, default=473)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    rows = read_jsonl(resolve(args.input))
    selected, held, report = filter_rows(rows)
    report["valid"] = (
        len(rows) == args.expect_input
        and len(selected) > 0
        and len(selected) + len(held) == len(rows)
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
