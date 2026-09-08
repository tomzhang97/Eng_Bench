#!/usr/bin/env python3
"""Replace held microtext review rows with clean rows from a staged buffer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import manifest_maps, read_csv, read_jsonl
from audit_staged_v2_capacity import audit_source_doc, row_identity


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_replacement(
    root: Path,
    input_path: Path,
    replacement_path: Path,
    drop_doc_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    input_absolute = input_path if input_path.is_absolute() else root / input_path
    replacement_absolute = replacement_path if replacement_path.is_absolute() else root / replacement_path
    input_rows = read_jsonl(input_absolute)
    replacement_rows = read_jsonl(replacement_absolute)
    held_rows = [row for row in input_rows if str(row.get("doc_id") or "") in drop_doc_ids]
    kept_rows = [row for row in input_rows if str(row.get("doc_id") or "") not in drop_doc_ids]
    needed = len(held_rows)
    active_rows = read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_aliases = {
        str(row.get(field) or "").strip()
        for row in active_rows
        for field in ("item_id", "candidate_id", "source_candidate_id")
        if str(row.get(field) or "").strip()
    }
    kept_ids = {row_identity(row) for row in kept_rows}
    docs, _pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    source_cache: dict[str, dict[str, Any]] = {}
    selected: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    for row in replacement_rows:
        identity = row_identity(row)
        raw_id = identity.removeprefix("microtext:")
        doc_id = str(row.get("doc_id") or "").strip()
        reason = ""
        if not identity:
            reason = "missing_identity"
        elif identity in kept_ids or identity in selected_ids:
            reason = "duplicate_output_identity"
        elif raw_id in active_aliases:
            reason = "active_gold_overlap"
        elif not doc_id:
            reason = "missing_doc_id"
        else:
            if doc_id not in source_cache:
                source_cache[doc_id] = audit_source_doc(root, doc_id, docs, inventory)
            if not source_cache[doc_id]["paper_ready"]:
                reason = "source_not_paper_ready"
        if reason:
            skipped.append({"identity": identity, "doc_id": doc_id, "reason": reason})
            continue
        if len(selected) < needed:
            enriched = dict(row)
            enriched["cohort_repair_status"] = "replacement_for_duplicate_source_alias"
            enriched["cohort_repair_source"] = replacement_absolute.relative_to(root).as_posix()
            selected.append(enriched)
            selected_ids.add(identity)
        else:
            remaining.append(row)
    output_rows = sorted(
        kept_rows + selected,
        key=lambda row: (str(row.get("doc_id") or ""), str(row.get("candidate_id") or "")),
    )
    output_ids = [row_identity(row) for row in output_rows]
    issues: list[str] = []
    if len(selected) != needed:
        issues.append(f"replacement_shortfall:{len(selected)}/{needed}")
    if len(output_rows) != len(input_rows):
        issues.append(f"output_count_changed:{len(output_rows)}/{len(input_rows)}")
    if len(set(output_ids)) != len(output_ids):
        issues.append("duplicate_output_identities")
    report = {
        "input_path": input_absolute.relative_to(root).as_posix(),
        "replacement_path": replacement_absolute.relative_to(root).as_posix(),
        "input_rows": len(input_rows),
        "drop_doc_ids": sorted(drop_doc_ids),
        "held_rows": len(held_rows),
        "selected_replacements": len(selected),
        "remaining_replacement_rows": len(remaining),
        "output_rows": len(output_rows),
        "skipped_replacements": skipped,
        "held_candidate_ids": [row_identity(row) for row in held_rows],
        "selected_candidate_ids": [row_identity(row) for row in selected],
        "issues": issues,
        "valid": not issues,
        "gold_rows_modified": 0,
    }
    return output_rows, remaining, held_rows, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--drop-doc-id", action="append", default=[], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--remaining-output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output, remaining, held, report = build_replacement(
        root, args.input, args.replacement, set(args.drop_doc_id)
    )
    output_path = args.output if args.output.is_absolute() else root / args.output
    remaining_path = (
        args.remaining_output if args.remaining_output.is_absolute() else root / args.remaining_output
    )
    hold_path = args.hold_output if args.hold_output.is_absolute() else root / args.hold_output
    report_path = args.report if args.report.is_absolute() else root / args.report
    write_jsonl(output_path, output)
    write_jsonl(remaining_path, remaining)
    write_jsonl(hold_path, held)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
