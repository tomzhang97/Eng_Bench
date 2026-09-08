#!/usr/bin/env python3
"""Materialize one canonical current-assignment registry from a primary payload.

The output combines the main primary pool and separately listed specialist
actions with their original source geometry. It is review capacity only and
never changes active Gold.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.expand_primary_intern_to_3000 import (
    flatten_specialist_visual,
    identifier,
    read_jsonl,
    sha256_file,
    source_group,
    split_name,
    task,
    write_json,
    write_jsonl,
)


def resolved(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def active_gold_ids(root: Path) -> set[str]:
    rows = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    rows += read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    return {identifier(row) for row in rows if identifier(row)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_map(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        identity = identifier(row)
        if not identity:
            raise ValueError(f"{label} contains a row without an identity")
        if identity in result:
            duplicates.append(identity)
        result[identity] = row
    if duplicates:
        raise ValueError(f"{label} contains duplicate identities: {duplicates[:5]}")
    return result


def assignment_row(
    source: dict[str, Any],
    payload_row: dict[str, Any],
    *,
    role: str,
    date_label: str,
) -> dict[str, Any]:
    row = dict(source)
    identity = str(payload_row["record_id"])
    row.update(
        {
            "record_id": identity,
            "task": task(source),
            "reserved_split": str(payload_row.get("reserved_split") or split_name(source)),
            "source_group": str(payload_row.get("source_group") or source_group(source)),
            "current_assignment_role": role,
            "current_assignment_date_label": date_label,
            "current_assignment_status": "issued_pending_human_return",
            "review_status": "needs_review",
            "promotion_state": "unreviewed_candidate",
            "safe_to_merge_gold": False,
        }
    )
    if role == "primary":
        row["primary_index"] = payload_row.get("primary_index")
        row["engineering_required"] = bool(payload_row.get("engineering_required"))
        row["engineering_index"] = payload_row.get("engineering_index", "")
        row["mandatory_description_rewrite"] = bool(
            payload_row.get("mandatory_description_rewrite")
        )
    else:
        row["specialist_index"] = payload_row.get("specialist_index")
        row["specialist_task"] = payload_row.get("task")
        row["type_required"] = bool(payload_row.get("type_required"))
    return row


def build_assignment(
    payload: dict[str, Any],
    main_pool: list[dict[str, Any]],
    specialist_visual_sources: list[dict[str, Any]],
    specialist_micro_sources: list[dict[str, Any]],
    *,
    date_label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary_payload = list(payload.get("rows") or [])
    specialist_visual_payload = list(
        (payload.get("specialist") or {}).get("visualdiff_english") or []
    )
    specialist_micro_payload = list(
        (payload.get("specialist") or {}).get("microtext_balance") or []
    )
    primary_by_id = source_map(main_pool, "primary pool")
    visual_by_id = source_map(specialist_visual_sources, "specialist VisualDiff source")
    micro_by_id = source_map(specialist_micro_sources, "specialist MicroText source")

    issues: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    for payload_row in primary_payload:
        identity = str(payload_row.get("record_id") or "")
        source = primary_by_id.get(identity)
        if source is None:
            issues.append({"record_id": identity, "reason": "primary_source_missing"})
            continue
        output.append(assignment_row(source, payload_row, role="primary", date_label=date_label))
    for payload_row in specialist_visual_payload:
        identity = str(payload_row.get("record_id") or "")
        source = visual_by_id.get(identity)
        if source is None:
            issues.append({"record_id": identity, "reason": "specialist_visual_source_missing"})
            continue
        output.append(assignment_row(source, payload_row, role="specialist", date_label=date_label))
    for payload_row in specialist_micro_payload:
        identity = str(payload_row.get("record_id") or "")
        source = micro_by_id.get(identity)
        if source is None:
            issues.append({"record_id": identity, "reason": "specialist_micro_source_missing"})
            continue
        output.append(assignment_row(source, payload_row, role="specialist", date_label=date_label))

    expected = int((payload.get("counts") or {}).get("total_human_actions") or 0)
    identities = [identifier(row) for row in output]
    if len(output) != expected:
        issues.append({"reason": "assignment_row_count_mismatch", "expected": expected, "actual": len(output)})
    if len(identities) != len(set(identities)):
        issues.append({"reason": "assignment_duplicate_identity"})
    if any(row.get("safe_to_merge_gold") is not False for row in output):
        issues.append({"reason": "assignment_gold_safety_flag_invalid"})
    role_counts = Counter(str(row.get("current_assignment_role") or "") for row in output)
    task_counts = Counter(task(row) for row in output)
    return output, {
        "valid": not issues,
        "issues": issues,
        "expected_rows": expected,
        "rows": len(output),
        "unique_identities": len(set(identities)),
        "role_counts": dict(sorted(role_counts.items())),
        "task_counts": dict(sorted(task_counts.items())),
        "safe_to_merge_gold": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--date-label", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    payload_path = resolved(root, args.payload)
    output_path = resolved(root, args.output_jsonl)
    report_json = resolved(root, args.report_json)
    report_md = resolved(root, args.report_md)
    for path in (output_path, report_json, report_md):
        if path.exists():
            raise FileExistsError(f"output already exists: {path}")

    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = file_sha256(gold_path)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    inputs = payload.get("inputs") or {}
    pool_path = resolved(root, inputs["pool"])
    visual_path = resolved(root, inputs["specialist_visual"]["path"])
    micro_path = resolved(root, inputs["specialist_micro"]["path"])
    if sha256_file(pool_path) != inputs["pool_sha256"]:
        raise ValueError("primary pool hash does not match payload")
    if sha256_file(visual_path) != inputs["specialist_visual"]["sha256"]:
        raise ValueError("specialist VisualDiff source hash does not match payload")
    if sha256_file(micro_path) != inputs["specialist_micro"]["sha256"]:
        raise ValueError("specialist MicroText source hash does not match payload")

    rows, report = build_assignment(
        payload,
        read_jsonl(pool_path),
        flatten_specialist_visual(visual_path),
        read_jsonl(micro_path),
        date_label=args.date_label,
    )
    overlap = sorted(set(identifier(row) for row in rows) & active_gold_ids(root))
    if overlap:
        report["issues"].append(
            {"reason": "assignment_active_gold_overlap", "examples": overlap[:10]}
        )
        report["valid"] = False
    report.update(
        {
            "goal": payload.get("goal", "Gold v2.0 Global"),
            "date_label": args.date_label,
            "payload": str(payload_path),
            "payload_sha256": sha256_file(payload_path),
            "output_jsonl": str(output_path),
            "active_gold_overlap": len(overlap),
            "active_gold_sha256_before": gold_hash_before,
            "active_gold_sha256_after": file_sha256(gold_path),
            "gold_rows_modified": 0,
        }
    )
    write_jsonl(output_path, rows)
    report["output_sha256"] = sha256_file(output_path)
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(
        "# Current Primary Assignment Capacity\n\n"
        f"- Goal: **{report['goal']}**\n"
        f"- Valid: **{str(report['valid']).lower()}**\n"
        f"- Assignment rows: **{report['rows']}**\n"
        f"- Primary rows: **{report['role_counts'].get('primary', 0)}**\n"
        f"- Specialist rows: **{report['role_counts'].get('specialist', 0)}**\n"
        f"- Unique identities: **{report['unique_identities']}**\n"
        "- Safe to merge Gold: **false**\n"
        "- Active Gold rows modified: **0**\n\n"
        "This file is the current human-assignment exclusion registry. It is not Gold.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
