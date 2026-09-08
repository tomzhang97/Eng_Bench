#!/usr/bin/env python3
"""Merge overlapping MicroText OCR proposals while preserving alternate readings."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def overlap_over_minimum(left: list[float], right: list[float]) -> float:
    ix1 = max(left[0], right[0])
    iy1 = max(left[1], right[1])
    ix2 = min(left[2], right[2])
    iy2 = min(left[3], right[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    minimum = min(area(left), area(right))
    return intersection / minimum if minimum else 0.0


def same_region(left: dict[str, Any], right: dict[str, Any], threshold: float) -> bool:
    if (
        left.get("doc_id") != right.get("doc_id")
        or left.get("page_index") != right.get("page_index")
        or left.get("category") != right.get("category")
    ):
        return False
    return overlap_over_minimum(left["bbox"], right["bbox"]) >= threshold


def merge_rows(
    inputs: Iterable[tuple[str, dict[str, Any]]],
    *,
    category: str = "",
    threshold: float = 0.7,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = [
        (source, row)
        for source, row in inputs
        if not category or str(row.get("category") or "") == category
    ]
    groups: list[list[tuple[str, dict[str, Any]]]] = []
    for source, row in records:
        matching = [
            index
            for index, group in enumerate(groups)
            if any(same_region(row, member, threshold) for _, member in group)
        ]
        if not matching:
            groups.append([(source, row)])
            continue
        target = matching[0]
        groups[target].append((source, row))
        for index in reversed(matching[1:]):
            groups[target].extend(groups.pop(index))

    output: list[dict[str, Any]] = []
    for group in groups:
        source, winner = max(
            group,
            key=lambda item: float(item[1].get("ocr_confidence") or 0.0),
        )
        merged = dict(winner)
        merged["ocr_merge_sources"] = sorted({item[0] for item in group})
        merged["ocr_alternates"] = sorted(
            {
                str(item[1].get("proposed_text") or "").strip()
                for item in group
                if str(item[1].get("proposed_text") or "").strip()
            }
        )
        merged["ocr_merged_detection_count"] = len(group)
        output.append(merged)
    output.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    return output, {
        "input_rows": len(records),
        "output_rows": len(output),
        "collapsed_rows": len(records) - len(output),
        "category": category or "all",
        "overlap_over_minimum_threshold": threshold,
        "output_categories": dict(sorted(Counter(str(row.get("category")) for row in output).items())),
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--category", default="")
    parser.add_argument("--overlap-threshold", type=float, default=0.7)
    args = parser.parse_args()
    if not 0 < args.overlap_threshold <= 1:
        raise ValueError("overlap threshold must be greater than 0 and at most 1")
    inputs = [
        (path.as_posix(), row)
        for path in args.input
        for row in read_jsonl(path)
    ]
    rows, report = merge_rows(
        inputs,
        category=args.category,
        threshold=args.overlap_threshold,
    )
    write_jsonl(args.output, rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
