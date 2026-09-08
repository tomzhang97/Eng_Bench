#!/usr/bin/env python3
"""Order a review queue by membership in a higher-priority overlay."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def identity(row: dict[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or row.get("pair_id")
        or row.get("item_id")
        or row.get("id")
        or ""
    ).strip()


def validate_unique(rows: list[dict[str, Any]], label: str) -> list[str]:
    values = [identity(row) for row in rows]
    if any(not value for value in values):
        raise ValueError(f"{label} contains blank row identity")
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"{label} contains duplicate row identities: {duplicates[:5]}")
    return values


def partition_rows(
    rows: list[dict[str, Any]],
    overlay_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    row_ids = validate_unique(rows, "input")
    overlay_ids = set(validate_unique(overlay_rows, "overlay"))
    priority: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    priority_rank = 0
    deferred_rank = 0
    for row, row_id in zip(rows, row_ids, strict=True):
        output = dict(row)
        if row_id in overlay_ids:
            priority_rank += 1
            output["human_review_priority"] = "category_closure_priority"
            output["human_review_priority_rank"] = priority_rank
            priority.append(output)
        else:
            deferred_rank += 1
            output["human_review_priority"] = "deferred_reserve"
            output["human_review_priority_rank"] = deferred_rank
            deferred.append(output)
    return priority, deferred, priority + deferred


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "categories": dict(sorted(Counter(str(row.get("category") or "") for row in rows).items())),
        "source_documents": dict(sorted(Counter(str(row.get("doc_id") or "") for row in rows).items())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--priority-overlay", type=Path, required=True)
    parser.add_argument("--priority-output", type=Path, required=True)
    parser.add_argument("--deferred-output", type=Path, required=True)
    parser.add_argument("--ordered-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-priority-rows", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    overlay_rows = read_jsonl(args.priority_overlay)
    priority, deferred, ordered = partition_rows(rows, overlay_rows)
    issues: list[str] = []
    if args.expected_priority_rows is not None and len(priority) != args.expected_priority_rows:
        issues.append(
            f"priority_row_count_mismatch:{len(priority)}!={args.expected_priority_rows}"
        )
    if len(ordered) != len(rows):
        issues.append("partition_count_mismatch")
    if {identity(row) for row in priority} & {identity(row) for row in deferred}:
        issues.append("partition_identity_overlap")
    report = {
        "valid": not issues,
        "active_gold_modified": False,
        "input": args.input.as_posix(),
        "priority_overlay": args.priority_overlay.as_posix(),
        "priority": summarize(priority),
        "deferred": summarize(deferred),
        "ordered_rows": len(ordered),
        "issues": issues,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if issues:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1
    write_jsonl(args.priority_output, priority)
    write_jsonl(args.deferred_output, deferred)
    write_jsonl(args.ordered_output, ordered)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
