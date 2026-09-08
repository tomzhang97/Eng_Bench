#!/usr/bin/env python3
"""Apply a SHA-bound machine reconciliation ledger to recovered human rows.

The command preserves original human semantics, localizes confirmed decisions,
and isolates conflicts. It never edits active Gold.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


CANONICAL_CHANGE_TYPES = {"addition", "deletion", "layout", "symbol", "text", "value"}
DECISIONS = {"retain_localized", "hold_conflict"}
HUMAN_SEMANTIC_SOURCE_FIELDS = {"human_description", "engineering_review_basis"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or "").strip()


def index_unique(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row_id(row)
        if not identifier:
            raise ValueError(f"{label}: missing pair_id")
        if identifier in indexed:
            raise ValueError(f"{label}: duplicate pair_id {identifier}")
        indexed[identifier] = row
    return indexed


def validate_ledger(
    input_path: Path,
    input_rows: list[dict[str, Any]],
    ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_hash = str(ledger.get("input_sha256") or "").strip().lower()
    actual_hash = file_sha256(input_path)
    if expected_hash != actual_hash:
        raise ValueError(
            f"input SHA-256 mismatch: expected={expected_hash or '<missing>'} actual={actual_hash}"
        )
    decisions = ledger.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("ledger decisions must be a list")
    input_index = index_unique(input_rows, "input")
    decision_index = index_unique(decisions, "decisions")
    missing = sorted(set(input_index) - set(decision_index))
    extra = sorted(set(decision_index) - set(input_index))
    if missing or extra:
        raise ValueError(f"decision coverage mismatch: missing={missing} extra={extra}")
    for decision in decisions:
        identifier = row_id(decision)
        disposition = str(decision.get("decision") or "").strip()
        if disposition not in DECISIONS:
            raise ValueError(f"{identifier}: invalid decision {disposition}")
        if not str(decision.get("evidence_sheet") or "").strip():
            raise ValueError(f"{identifier}: missing evidence_sheet")
        if disposition == "retain_localized":
            if not str(decision.get("localized_english_description") or "").strip():
                raise ValueError(f"{identifier}: missing localized_english_description")
            change_types = decision.get("confirmed_change_type")
            if not isinstance(change_types, list) or not change_types:
                raise ValueError(f"{identifier}: confirmed_change_type must be a nonempty list")
            invalid = sorted(set(str(value) for value in change_types) - CANONICAL_CHANGE_TYPES)
            if invalid:
                raise ValueError(f"{identifier}: invalid confirmed change types {invalid}")
            source_field = str(
                decision.get("source_human_field") or "human_description"
            ).strip()
            if source_field not in HUMAN_SEMANTIC_SOURCE_FIELDS:
                raise ValueError(f"{identifier}: invalid source_human_field {source_field}")
            if not str(input_index[identifier].get(source_field) or "").strip():
                raise ValueError(f"{identifier}: source_human_field is blank: {source_field}")
        elif not str(decision.get("hold_reason") or "").strip():
            raise ValueError(f"{identifier}: missing hold_reason")
    return decisions


def reconcile(
    input_path: Path,
    input_rows: list[dict[str, Any]],
    ledger: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    decisions = validate_ledger(input_path, input_rows, ledger)
    decision_index = index_unique(decisions, "decisions")
    ready: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for original in input_rows:
        identifier = row_id(original)
        decision = decision_index[identifier]
        disposition = str(decision["decision"])
        row = dict(original)
        source_human_field = str(
            decision.get("source_human_field") or "human_description"
        ).strip()
        row["original_primary_human_description"] = row.get("human_description")
        row["original_human_description"] = row.get(source_human_field)
        row["machine_localization_source_human_field"] = source_human_field
        row["original_human_review_status"] = row.get("human_review_status")
        row["reconciliation_evidence_sheet"] = decision["evidence_sheet"]
        row["reconciliation_notes"] = str(decision.get("notes") or "")
        row["safe_to_merge_gold"] = False
        if disposition == "hold_conflict":
            row["recovery_status"] = "machine_held_semantic_conflict"
            row["promotion_hold_reason"] = decision["hold_reason"]
            held.append(row)
            counts["held_conflict"] += 1
            continue
        row["localized_human_description"] = decision["localized_english_description"]
        row["reconciled_change_type"] = decision["confirmed_change_type"]
        row["localization_method"] = "machine_translation_and_visual_reconciliation"
        row["recovery_status"] = "human_semantics_machine_localized_preview_ready"
        row["human_completion_source_path"] = row.get("recovery_source_queue")
        ready.append(row)
        counts["retained_localized"] += 1

    report = {
        "goal": "Gold v2.0 Global",
        "mode": "read_only_human_semantic_reconciliation",
        "input": input_path.as_posix(),
        "input_sha256": file_sha256(input_path),
        "input_rows": len(input_rows),
        "ready_rows": len(ready),
        "held_rows": len(held),
        "decision_counts": dict(sorted(counts.items())),
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold_rows": 0,
        "valid": len(ready) + len(held) == len(input_rows),
        "interpretation": (
            "Ready rows preserve human semantic decisions and add transparent machine localization. "
            "Held conflicts are excluded from promotion. No active Gold file is modified."
        ),
    }
    return ready, held, report


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# VisualDiff Human Decision Reconciliation",
            "",
            f"- Goal: **{report['goal']}**",
            f"- Valid: `{str(report['valid']).lower()}`",
            f"- Input rows: `{report['input_rows']}`",
            f"- Localized preview rows: `{report['ready_rows']}`",
            f"- Machine-held semantic conflicts: `{report['held_rows']}`",
            f"- Active Gold rows modified: `{report['active_gold_rows_modified']}`",
            f"- Safe-to-merge-Gold rows: `{report['safe_to_merge_gold_rows']}`",
            "",
            report["interpretation"],
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)
    input_rows = read_jsonl(args.input)
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    ready, held, report = reconcile(args.input, input_rows, ledger)
    write_jsonl(args.output, ready)
    write_jsonl(args.hold_output, held)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
