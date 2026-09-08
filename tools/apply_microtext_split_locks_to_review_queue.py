#!/usr/bin/env python3
"""Apply conflict-checked microtext split locks from a staged plan and active gold."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


VALID_SPLITS = {"train", "dev", "test"}


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
    return digest.hexdigest()


def row_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("item_id") or row.get("id") or "").strip()


def active_gold_doc_splits(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        task = str(row.get("task") or metadata.get("task") or "").strip().lower()
        if task and task != "microtext":
            continue
        doc_id = str(row.get("doc_id") or metadata.get("doc_id") or "").strip()
        split = str(row.get("split") or metadata.get("split") or "").strip().lower()
        if doc_id and split in VALID_SPLITS:
            result.setdefault(doc_id, set()).add(split)
    return result


def plan_doc_splits(plan: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    reservations = plan.get("reservations")
    if not isinstance(reservations, list):
        return result
    for reservation in reservations:
        if not isinstance(reservation, dict):
            continue
        if str(reservation.get("task") or "").strip().lower() != "microtext":
            continue
        doc_id = str(reservation.get("unit_id") or "").strip()
        split = str(reservation.get("split") or "").strip().lower()
        if doc_id and split in VALID_SPLITS:
            result.setdefault(doc_id, set()).add(split)
    return result


def held_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
    held = dict(row)
    held.update(
        {
            "review_status": "machine_held",
            "machine_qa_status": "machine_held",
            "machine_hold_reason": reason,
            "machine_qa_notes": "Split lock is unresolved or conflicting; do not assign or merge.",
            "safe_to_merge_gold": False,
        }
    )
    return held


def apply_split_locks(
    rows: list[dict[str, Any]],
    plan: dict[str, Any],
    active_gold_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    plan_splits = plan_doc_splits(plan)
    gold_splits = active_gold_doc_splits(active_gold_rows)
    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    doc_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for original in rows:
        row = dict(original)
        identifier = row_id(row)
        if not identifier:
            raise ValueError("review row is missing candidate_id/item_id/id")
        if identifier in seen_ids:
            raise ValueError(f"duplicate review row id: {identifier}")
        seen_ids.add(identifier)

        doc_id = str(row.get("doc_id") or "").strip()
        doc_counts[doc_id or "<missing_doc_id>"] += 1
        plan_values = plan_splits.get(doc_id, set())
        gold_values = gold_splits.get(doc_id, set())
        reason = ""
        locked_split = ""
        source = ""
        if not doc_id:
            reason = "missing_doc_id"
        elif len(plan_values) > 1:
            reason = "conflicting_staged_plan_splits"
        elif len(gold_values) > 1:
            reason = "active_gold_doc_split_leakage"
        elif plan_values and gold_values and plan_values != gold_values:
            reason = "staged_plan_active_gold_split_conflict"
        elif plan_values:
            locked_split = next(iter(plan_values))
            source = "staged_plan+active_gold" if gold_values else "staged_plan"
        elif gold_values:
            locked_split = next(iter(gold_values))
            source = "active_gold_doc_family"
        else:
            reason = "unresolved_split_lock"

        existing = str(row.get("split") or "").strip().lower()
        if not reason and existing and existing != locked_split:
            reason = "existing_row_split_conflict"

        if reason:
            status_counts[reason] += 1
            held.append(held_row(row, reason))
            continue

        row["split"] = locked_split
        row["split_locked"] = True
        row["machine_split_lock_source"] = source
        row["safe_to_merge_gold"] = False
        passing.append(row)
        split_counts[locked_split] += 1
        status_counts["locked"] += 1

    report = {
        "totals": {
            "input_rows": len(rows),
            "passing_rows": len(passing),
            "held_rows": len(held),
            "unique_row_ids": len(seen_ids),
        },
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "document_counts": dict(sorted(doc_counts.items())),
        "valid": not held,
        "interpretation": (
            "Split locks do not make rows gold. Every passing row remains unreviewed and "
            "safe_to_merge_gold=false until human acceptance and merge gates pass."
        ),
    }
    return passing, held, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Microtext Split-Lock Audit",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Passing rows: `{totals['passing_rows']}`",
        f"- Held rows: `{totals['held_rows']}`",
        "",
        "## Locked Splits",
        "",
    ]
    lines.extend(f"- `{key}`: `{value}`" for key, value in report["split_counts"].items())
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--split-plan", type=Path, required=True)
    parser.add_argument("--active-gold", type=Path, default=Path("eng_bench.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    absolute = lambda path: path if path.is_absolute() else root / path
    inputs = [absolute(path) for path in args.input]
    rows = [row for path in inputs for row in read_jsonl(path)]
    plan = json.loads(absolute(args.split_plan).read_text(encoding="utf-8"))
    passing, held, report = apply_split_locks(
        rows,
        plan,
        read_jsonl(absolute(args.active_gold)),
    )
    report["inputs"] = [
        {"path": path.relative_to(root).as_posix(), "sha256": file_sha256(path), "rows": len(read_jsonl(path))}
        for path in inputs
    ]
    report["split_plan"] = {
        "path": absolute(args.split_plan).relative_to(root).as_posix(),
        "sha256": file_sha256(absolute(args.split_plan)),
    }
    write_jsonl(absolute(args.output), passing)
    write_jsonl(absolute(args.held_output), held)
    write_report(absolute(args.report_json), report)
    write_markdown(absolute(args.report_md), report)
    print(json.dumps(report["totals"], indent=2))
    print(json.dumps(report["split_counts"], indent=2))
    return 1 if args.strict and held else 0


if __name__ == "__main__":
    raise SystemExit(main())
