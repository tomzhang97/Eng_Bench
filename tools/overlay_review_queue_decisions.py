#!/usr/bin/env python3
"""Overlay machine-curated fields onto pending rows without touching human decisions."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


OVERLAY_FIELDS = {
    "category",
    "change_type",
    "description",
    "description_source",
    "machine_audit",
    "machine_change_type_corrected_from",
    "machine_description_corrected_from",
    "machine_qa_status",
    "machine_visual_qa_notes",
    "machine_visual_qa_status",
    "new_text",
    "notes",
    "old_text",
    "proposed_text",
    "question_text",
    "review_confidence",
    "safe_to_merge_gold",
    "target_text",
    "text_context",
}
HUMAN_DECISION_FIELDS = {
    "human_description",
    "human_review_status",
    "human_status",
    "reviewed_by",
}
NON_DECISION_STATUSES = {
    "",
    "awaiting_review",
    "needs_review",
    "not_reviewed",
    "pending",
    "unassigned",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_id(row: dict[str, Any]) -> str:
    return str(
        row.get("pair_id")
        or row.get("candidate_id")
        or row.get("id")
        or row.get("item_id")
        or ""
    ).strip()


def index_rows(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    identifiers = [row_id(row) for row in rows]
    missing = sum(not value for value in identifiers)
    duplicates = sorted(
        value for value, count in Counter(identifiers).items() if value and count > 1
    )
    if missing or duplicates:
        raise ValueError(f"invalid {label} identities: missing={missing} duplicates={duplicates}")
    return dict(zip(identifiers, rows))


def has_human_decision(row: dict[str, Any]) -> bool:
    for field in HUMAN_DECISION_FIELDS:
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        normalized = value.lower().replace(" ", "_")
        if field in {"human_review_status", "human_status"} and normalized in NON_DECISION_STATUSES:
            continue
        return True
    return False


def overlay_pending_rows(
    input_rows: list[dict[str, Any]],
    overlay_rows: list[dict[str, Any]],
    drop_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    input_index = index_rows(input_rows, "input")
    overlay_index = index_rows(overlay_rows, "overlay")
    drop_index = index_rows(drop_rows, "drop")
    input_ids = set(input_index)
    matched_overlay_ids = input_ids & set(overlay_index)
    matched_drop_ids = input_ids & set(drop_index)
    conflict_ids = sorted(matched_overlay_ids & matched_drop_ids)
    if conflict_ids:
        raise ValueError(f"rows are both overlaid and dropped: {conflict_ids}")
    protected_ids = sorted(
        identifier
        for identifier in matched_overlay_ids | matched_drop_ids
        if has_human_decision(input_index[identifier])
    )
    if protected_ids:
        raise ValueError(f"refusing to modify rows with human decisions: {protected_ids}")

    output: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    changed_fields: Counter[str] = Counter()
    for original in input_rows:
        identifier = row_id(original)
        if identifier in matched_drop_ids:
            row = dict(original)
            source = drop_index[identifier]
            reason = str(source.get("machine_hold_reason") or "machine_visual_hold")
            row["review_status"] = "machine_held"
            row["machine_qa_status"] = "machine_held"
            row["machine_hold_reason"] = reason
            row["safe_to_merge_gold"] = False
            held.append(row)
            continue
        row = dict(original)
        if identifier in matched_overlay_ids:
            source = overlay_index[identifier]
            for field in OVERLAY_FIELDS:
                if field not in source:
                    continue
                if row.get(field) != source[field]:
                    changed_fields[field] += 1
                row[field] = source[field]
            row["review_status"] = "needs_review"
            row["safe_to_merge_gold"] = False
            row["cohort_overlay_status"] = "machine_visual_curation_applied"
        output.append(row)

    output_ids = [row_id(row) for row in output]
    issues: list[str] = []
    if len(output) + len(held) != len(input_rows):
        issues.append("input_not_conserved")
    if len(output_ids) != len(set(output_ids)):
        issues.append("duplicate_output_identities")
    report = {
        "totals": {
            "input_rows": len(input_rows),
            "output_rows": len(output),
            "held_rows": len(held),
            "overlay_input_rows": len(overlay_rows),
            "overlay_rows_applied": len(matched_overlay_ids),
            "drop_input_rows": len(drop_rows),
            "drop_rows_applied": len(matched_drop_ids),
            "unmatched_overlay_rows": len(set(overlay_index) - input_ids),
            "unmatched_drop_rows": len(set(drop_index) - input_ids),
        },
        "changed_fields": dict(sorted(changed_fields.items())),
        "overlaid_ids": sorted(matched_overlay_ids),
        "held_ids": sorted(matched_drop_ids),
        "issues": issues,
        "valid": not issues,
        "interpretation": (
            "Pending-review overlay only. Existing human decisions are protected and no row "
            "becomes gold."
        ),
    }
    return output, held, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Pending Review Queue Overlay",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Output review rows: `{totals['output_rows']}`",
        f"- Machine-held rows: `{totals['held_rows']}`",
        f"- Curated overlays applied: `{totals['overlay_rows_applied']}`",
        f"- Unmatched overlay rows: `{totals['unmatched_overlay_rows']}`",
        f"- Unmatched hold rows: `{totals['unmatched_drop_rows']}`",
        "",
        "## Changed Fields",
        "",
    ]
    for field, count in report["changed_fields"].items():
        lines.append(f"- `{field}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--drop-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    output, held, report = overlay_pending_rows(
        read_jsonl(args.input), read_jsonl(args.overlay), read_jsonl(args.drop_input)
    )
    write_jsonl(args.output, output)
    write_jsonl(args.hold_output, held)
    write_report(args.report_json, report)
    write_markdown(args.report_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
