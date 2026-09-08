#!/usr/bin/env python3
"""Apply an auditable contact-sheet decision file to a microtext review queue."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


QUESTION_BY_CATEGORY = {
    "component_value": "What component value is shown in this region?",
    "dimension_value": "What dimension value is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "instrument_tag": "What instrument tag is shown in this region?",
    "pin_label": "What pin or component label is shown in this marked region?",
    "pipe_line_tag": "What pipe or process line tag is shown in this region?",
    "process_label": "What process step or stream label is shown in this region?",
    "process_value": "What process value is shown in this region?",
    "room_label": "What room label is shown in this region?",
    "tolerance_value": "What tolerance is specified in this small text region?",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_row_spec(value: str) -> list[int]:
    rows: list[int] = []
    for raw_token in value.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" not in token:
            number = int(token)
            if number < 1:
                raise ValueError(f"row numbers must be positive: {token}")
            rows.append(number)
            continue
        start_text, end_text = (part.strip() for part in token.split("-", 1))
        start = int(start_text)
        end = int(end_text)
        if start < 1 or end < start:
            raise ValueError(f"invalid row range: {token}")
        rows.extend(range(start, end + 1))
    return rows


def normalize_correction(value: dict[str, Any], label: str) -> dict[str, Any]:
    category = str(value.get("category") or "").strip()
    proposed_text = str(value.get("proposed_text") or "").strip()
    bbox = value.get("bbox")
    if not category and not proposed_text and bbox is None:
        raise ValueError(f"{label} lacks category, proposed_text, or bbox")

    correction: dict[str, Any] = {
        "reason": str(value.get("reason") or "visual_correction").strip(),
    }
    if category:
        correction["category"] = category
    if proposed_text:
        correction["proposed_text"] = proposed_text
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"{label} bbox must have four values")
        try:
            normalized_bbox = [int(round(float(item))) for item in bbox]
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} bbox must be numeric") from error
        x0, y0, x1, y1 = normalized_bbox
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError(f"{label} bbox must be a positive xyxy box")
        correction["bbox"] = normalized_bbox
    return correction


def build_report(
    input_path: Path,
    decisions_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, str]]]:
    rows = read_jsonl(input_path)
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    expected_sha = str(decisions.get("input_sha256") or "").strip().upper()
    actual_sha = file_sha256(input_path)
    if expected_sha and expected_sha != actual_sha:
        raise ValueError(f"input SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")

    default_decision = str(decisions.get("default_decision") or "keep").strip().lower()
    if default_decision not in {"keep", "hold"}:
        raise ValueError("default_decision must be 'keep' or 'hold'")
    default_hold_reason = str(
        decisions.get("default_hold_reason") or "not_selected_after_visual_review"
    ).strip()

    keep_by_row: dict[int, str] = {}
    for group in decisions.get("keep_groups", []):
        reason = str(group.get("reason") or "visual_contact_sheet_pass").strip()
        for row_number in parse_row_spec(str(group.get("rows") or "")):
            if row_number in keep_by_row:
                raise ValueError(f"row {row_number} appears in multiple keep groups")
            keep_by_row[row_number] = reason

    hold_by_row: dict[int, str] = {}
    for group in decisions.get("hold_groups", []):
        reason = str(group.get("reason") or "machine_visual_hold").strip()
        for row_number in parse_row_spec(str(group.get("rows") or "")):
            if row_number in hold_by_row:
                raise ValueError(f"row {row_number} appears in multiple hold groups")
            hold_by_row[row_number] = reason

    corrections: dict[int, dict[str, Any]] = {}
    for group in decisions.get("correction_groups", []):
        correction = normalize_correction(group, "correction group")
        for row_number in parse_row_spec(str(group.get("rows") or "")):
            if row_number in corrections:
                raise ValueError(f"row {row_number} appears in multiple correction groups")
            corrections[row_number] = dict(correction)

    for key, value in dict(decisions.get("corrections") or {}).items():
        row_number = int(key)
        if row_number in hold_by_row:
            raise ValueError(f"row {row_number} is both held and corrected")
        if row_number in corrections:
            raise ValueError(f"row {row_number} appears in multiple corrections")
        if not isinstance(value, dict):
            raise ValueError(f"correction for row {row_number} must be an object")
        corrections[row_number] = normalize_correction(
            value, f"correction for row {row_number}"
        )

    overlap = sorted(set(hold_by_row) & set(corrections))
    if overlap:
        raise ValueError(f"rows are both held and corrected: {overlap}")

    keep_hold_overlap = sorted(set(keep_by_row) & set(hold_by_row))
    if keep_hold_overlap:
        raise ValueError(f"rows are both kept and held: {keep_hold_overlap}")

    if default_decision == "hold":
        implicit_corrections = sorted(set(corrections) - set(keep_by_row))
        if implicit_corrections:
            raise ValueError(
                "default-hold corrections must also appear in keep_groups: "
                f"{implicit_corrections}"
            )

    referenced = set(keep_by_row) | set(hold_by_row) | set(corrections)
    out_of_range = sorted(number for number in referenced if number < 1 or number > len(rows))
    if out_of_range:
        raise ValueError(f"decision rows outside 1..{len(rows)}: {out_of_range}")

    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    audit_rows: list[dict[str, str]] = []
    for row_number, original in enumerate(rows, start=1):
        row = dict(original)
        original_category = str(row.get("category") or "")
        original_proposed_text = str(
            row.get("proposed_text") or row.get("target_text") or ""
        )
        original_bbox = row.get("bbox")
        reason = keep_by_row.get(row_number, "visual_contact_sheet_pass")
        decision = "keep"
        should_hold = row_number in hold_by_row or (
            default_decision == "hold" and row_number not in keep_by_row
        )
        if should_hold:
            decision = "hold"
            reason = hold_by_row.get(row_number, default_hold_reason)
            row["review_status"] = "machine_held"
            row["machine_qa_status"] = "machine_held"
            row["safe_to_merge_gold"] = False
            row["machine_hold_reason"] = reason
            row["machine_qa_notes"] = (
                "Held during full contact-sheet visual QA; do not assign for human review."
            )
            held.append(row)
        else:
            correction = corrections.get(row_number)
            if correction:
                if correction.get("category"):
                    row["machine_category_corrected_from"] = original_category
                    row["category"] = correction["category"]
                    if correction["category"] in QUESTION_BY_CATEGORY:
                        row["question_text"] = QUESTION_BY_CATEGORY[correction["category"]]
                    row["machine_category_correction_reason"] = correction["reason"]
                if correction.get("proposed_text"):
                    row["machine_text_corrected_from"] = original_proposed_text
                    corrected_text = correction["proposed_text"]
                    row["proposed_text"] = corrected_text
                    # Review queues can carry the same answer on several
                    # surfaces. Keep the verbatim extraction separately, then
                    # synchronize answer-bearing aliases with the correction.
                    for field in ("target_text", "text_context", "raw_text"):
                        original_value = str(row.get(field) or "")
                        if not original_value or original_value == original_proposed_text:
                            if field == "raw_text" and original_value:
                                row.setdefault("source_raw_text", original_value)
                            row[field] = corrected_text
                    row["machine_text_correction_reason"] = correction["reason"]
                if correction.get("bbox"):
                    row["machine_bbox_corrected_from"] = original_bbox
                    row["bbox"] = correction["bbox"]
                    row["machine_bbox_correction_reason"] = correction["reason"]
                reason = correction["reason"]
            row["review_status"] = "needs_review"
            row["machine_qa_status"] = "selected_for_human_review"
            row["safe_to_merge_gold"] = False
            row["machine_qa_notes"] = (
                "Passed full contact-sheet visual QA: readable target, usable crop, and defensible category."
            )
            kept.append(row)
        audit_rows.append(
            {
                "row_number": str(row_number),
                "candidate_id": str(row.get("candidate_id") or ""),
                "doc_id": str(row.get("doc_id") or ""),
                "decision": decision,
                "reason": reason,
                "original_category": original_category,
                "final_category": str(row.get("category") or ""),
                "original_proposed_text": original_proposed_text,
                "final_proposed_text": str(
                    row.get("proposed_text") or row.get("target_text") or ""
                ),
                "proposed_text": str(row.get("proposed_text") or row.get("target_text") or ""),
                "original_bbox": json.dumps(original_bbox, separators=(",", ":")),
                "final_bbox": json.dumps(row.get("bbox"), separators=(",", ":")),
            }
        )

    candidate_ids = [str(row.get("candidate_id") or "") for row in kept]
    duplicate_ids = sorted(key for key, count in Counter(candidate_ids).items() if key and count > 1)
    if duplicate_ids:
        raise ValueError(f"duplicate kept candidate IDs: {duplicate_ids}")

    report = {
        "input": input_path.as_posix(),
        "decisions": decisions_path.as_posix(),
        "input_sha256": actual_sha,
        "default_decision": default_decision,
        "totals": {
            "input_rows": len(rows),
            "kept_rows": len(kept),
            "held_rows": len(held),
            "corrected_rows": len(corrections),
            "category_corrected_rows": sum(
                1 for correction in corrections.values() if correction.get("category")
            ),
            "text_corrected_rows": sum(
                1 for correction in corrections.values() if correction.get("proposed_text")
            ),
            "bbox_corrected_rows": sum(
                1 for correction in corrections.values() if correction.get("bbox")
            ),
            "explicitly_kept_rows": len(keep_by_row),
            "unique_kept_candidate_ids": len(set(candidate_ids)),
        },
        "kept_categories": dict(sorted(Counter(str(row.get("category") or "") for row in kept).items())),
        "held_reasons": dict(
            sorted(
                Counter(
                    row["reason"]
                    for row in audit_rows
                    if row["decision"] == "hold"
                ).items()
            )
        ),
        "corrected_rows": [
            {"row_number": number, **corrections[number]} for number in sorted(corrections)
        ],
        "interpretation": "Machine visual curation only; kept rows still require human review before gold promotion.",
    }
    return kept, held, report, audit_rows


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Microtext Contact-Sheet Visual Audit",
        "",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Kept for human review: `{totals['kept_rows']}`",
        f"- Machine-held: `{totals['held_rows']}`",
        f"- Visual corrections: `{totals['corrected_rows']}`",
        f"- Category corrections: `{totals['category_corrected_rows']}`",
        f"- Text corrections: `{totals['text_corrected_rows']}`",
        f"- Bounding-box corrections: `{totals['bbox_corrected_rows']}`",
        f"- Input SHA-256: `{report['input_sha256']}`",
        "",
        "## Kept Categories",
        "",
    ]
    for category, count in report["kept_categories"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Hold Reasons", ""])
    for reason, count in report["held_reasons"].items():
        lines.append(f"- `{reason}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "row_number",
        "candidate_id",
        "doc_id",
        "decision",
        "reason",
        "original_category",
        "final_category",
        "original_proposed_text",
        "final_proposed_text",
        "proposed_text",
        "original_bbox",
        "final_bbox",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--kept-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--audit-csv", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    kept, held, report, audit_rows = build_report(args.input, args.decisions)
    write_jsonl(args.kept_output, kept)
    write_jsonl(args.held_output, held)
    write_audit_csv(args.audit_csv, audit_rows)
    write_report(args.report_json, report)
    write_markdown(args.report_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
