#!/usr/bin/env python3
"""Apply a SHA-bound, full-coverage machine visual audit to VisualDiff rows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


CORRECTABLE_FIELDS = {
    "change_type",
    "description",
    "old_text",
    "new_text",
    "notes",
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def numbered_mapping(
    values: list[dict[str, Any]],
    *,
    label: str,
) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for value in values:
        row_number = int(value.get("row_number") or 0)
        if row_number < 1:
            raise ValueError(f"{label} entry has invalid row_number: {row_number}")
        if row_number in mapping:
            raise ValueError(f"duplicate {label} row_number: {row_number}")
        mapping[row_number] = dict(value)
    return mapping


def apply_decisions(
    input_path: Path,
    decisions_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, str]]]:
    rows = read_jsonl(input_path)
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    expected_sha = str(decisions.get("input_sha256") or "").strip().upper()
    actual_sha = file_sha256(input_path)
    if not expected_sha or expected_sha != actual_sha:
        raise ValueError(
            f"decision ledger input SHA mismatch: expected={expected_sha or '<missing>'} "
            f"actual={actual_sha}"
        )

    keep_rows = [int(value) for value in decisions.get("keep_rows", [])]
    if len(keep_rows) != len(set(keep_rows)):
        raise ValueError("duplicate row number in keep_rows")
    keep_set = set(keep_rows)
    holds = numbered_mapping(decisions.get("holds", []), label="hold")
    corrections = numbered_mapping(decisions.get("corrections", []), label="correction")
    all_numbers = set(range(1, len(rows) + 1))
    decided = keep_set | set(holds)
    if keep_set & set(holds):
        raise ValueError(f"rows are both kept and held: {sorted(keep_set & set(holds))}")
    if decided != all_numbers:
        missing = sorted(all_numbers - decided)
        unknown = sorted(decided - all_numbers)
        raise ValueError(f"decision coverage mismatch: missing={missing} unknown={unknown}")
    unknown_corrections = sorted(set(corrections) - keep_set)
    if unknown_corrections:
        raise ValueError(f"corrections must target kept rows: {unknown_corrections}")

    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    audit_rows: list[dict[str, str]] = []
    for row_number, original in enumerate(rows, start=1):
        row = dict(original)
        original_type = str(row.get("change_type") or "")
        original_description = str(row.get("description") or "")
        if row_number in holds:
            reason = str(holds[row_number].get("reason") or "machine_visual_hold").strip()
            row["review_status"] = "machine_held"
            row["machine_qa_status"] = "machine_held"
            row["machine_visual_qa_status"] = "machine_held"
            row["machine_hold_reason"] = reason
            row["safe_to_merge_gold"] = False
            held.append(row)
            decision = "hold"
        else:
            correction = corrections.get(row_number, {})
            unexpected = sorted(set(correction) - (CORRECTABLE_FIELDS | {"row_number", "reason"}))
            if unexpected:
                raise ValueError(
                    f"unsupported correction fields for row {row_number}: {unexpected}"
                )
            for field in CORRECTABLE_FIELDS:
                if field in correction:
                    row[field] = correction[field]
            description = str(row.get("description") or "").strip()
            if not description or description == "CHANGE_DESC_GT_TODO":
                raise ValueError(f"kept row {row_number} is missing a machine description")
            reason = str(correction.get("reason") or "visually_readable_defensible_change").strip()
            row["review_status"] = "needs_review"
            row["machine_qa_status"] = "selected_for_human_review"
            row["machine_visual_qa_status"] = "selected_for_human_review"
            row["machine_visual_qa_notes"] = reason
            row["description_source"] = "machine_visual_candidate"
            row["review_confidence"] = "machine_candidate"
            row["safe_to_merge_gold"] = False
            if str(row.get("description") or "") != original_description:
                row["machine_description_corrected_from"] = original_description
            if str(row.get("change_type") or "") != original_type:
                row["machine_change_type_corrected_from"] = original_type
            kept.append(row)
            decision = "keep"
        audit_rows.append(
            {
                "row_number": str(row_number),
                "pair_id": str(row.get("pair_id") or row.get("id") or ""),
                "project_id": str(row.get("project_id") or ""),
                "decision": decision,
                "reason": reason,
                "original_change_type": original_type,
                "final_change_type": str(row.get("change_type") or ""),
                "original_description": original_description,
                "final_description": str(row.get("description") or ""),
            }
        )

    pair_ids = [str(row.get("pair_id") or row.get("id") or "") for row in kept]
    duplicate_ids = sorted(
        value for value, count in Counter(pair_ids).items() if value and count > 1
    )
    if any(not value for value in pair_ids) or duplicate_ids:
        raise ValueError(f"invalid kept pair IDs: missing={pair_ids.count('')} duplicates={duplicate_ids}")

    report = {
        "input": input_path.as_posix(),
        "decisions": decisions_path.as_posix(),
        "input_sha256": actual_sha,
        "totals": {
            "input_rows": len(rows),
            "kept_rows": len(kept),
            "held_rows": len(held),
            "corrected_rows": len(corrections),
            "unique_kept_pair_ids": len(set(pair_ids)),
            "kept_families": len({str(row.get("project_id") or "") for row in kept}),
        },
        "kept_change_types": dict(
            sorted(Counter(str(row.get("change_type") or "") for row in kept).items())
        ),
        "held_reasons": dict(
            sorted(Counter(str(row.get("machine_hold_reason") or "") for row in held).items())
        ),
        "interpretation": (
            "Machine visual curation only. Kept rows remain unreviewed and cannot be "
            "promoted without human acceptance and strict release gates."
        ),
    }
    return kept, held, report, audit_rows


def write_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def write_report_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# VisualDiff Machine Visual Audit",
        "",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Kept for human review: `{totals['kept_rows']}`",
        f"- Machine-held: `{totals['held_rows']}`",
        f"- Kept revision families: `{totals['kept_families']}`",
        f"- Input SHA-256: `{report['input_sha256']}`",
        "",
        "## Kept Change Types",
        "",
    ]
    for change_type, count in report["kept_change_types"].items():
        lines.append(f"- `{change_type}`: `{count}`")
    lines.extend(["", "## Hold Reasons", ""])
    for reason, count in report["held_reasons"].items():
        lines.append(f"- `{reason}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


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

    kept, held, report, audit_rows = apply_decisions(args.input, args.decisions)
    write_jsonl(args.kept_output, kept)
    write_jsonl(args.held_output, held)
    write_audit_csv(args.audit_csv, audit_rows)
    write_report_json(args.report_json, report)
    write_report_md(args.report_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
