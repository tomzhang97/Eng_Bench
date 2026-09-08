#!/usr/bin/env python3
"""Build a deterministic review queue for visualdiff TODO descriptions."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


PLACEHOLDER = "CHANGE_DESC_GT_TODO"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_todo(row: dict[str, Any]) -> bool:
    return row.get("change_desc_gt") in ("", PLACEHOLDER)


def image_path(row: dict[str, Any], version_key: str, page_key: str) -> str:
    doc_id = row.get("doc_id", "")
    version_id = row.get(version_key, "")
    page = int(row.get(page_key, 0))
    return f"images/{doc_id}__{version_id}/page_{page:04d}.png"


def review_bucket(row: dict[str, Any]) -> tuple[int, str]:
    split = row.get("split", "")
    is_release = split in {"dev", "test"}
    is_titleblock = bool(row.get("is_titleblock"))

    if is_release and not is_titleblock:
        return 10, "release_non_titleblock"
    if is_release and is_titleblock:
        return 20, "release_titleblock"
    if not is_titleblock:
        return 30, "train_non_titleblock"
    return 40, "train_titleblock"


def build_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for row in rows:
        if not is_todo(row):
            continue
        priority, bucket = review_bucket(row)
        item = dict(row)
        item["review_priority"] = priority
        item["review_bucket"] = bucket
        item["image_old"] = image_path(row, "version_id_old", "page_index_old")
        item["image_new"] = image_path(row, "version_id_new", "page_index_new")
        queue.append(item)

    split_order = {"dev": 0, "test": 1, "train": 2}
    queue.sort(
        key=lambda row: (
            row["review_priority"],
            split_order.get(str(row.get("split", "")), 99),
            str(row.get("project_id", "")),
            int(row.get("page_index_old", 0)),
            str(row.get("pair_id", "")),
        )
    )
    return queue


def summarize(queue: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        "by_split": dict(Counter(str(row.get("split", "unknown")) for row in queue)),
        "by_bucket": dict(Counter(str(row.get("review_bucket", "unknown")) for row in queue)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build visualdiff TODO review queue")
    parser.add_argument(
        "--input",
        default="visualdiff/annotations/visualdiff_pairs_TODO.jsonl",
        help="Input TODO pairs JSONL",
    )
    parser.add_argument(
        "--output",
        default="visualdiff/annotations/visualdiff_review_queue.jsonl",
        help="Output review queue JSONL",
    )
    args = parser.parse_args()

    rows = load_jsonl(Path(args.input))
    queue = build_queue(rows)
    save_jsonl(Path(args.output), queue)

    print(f"[OK] Wrote {len(queue)} review rows to {args.output}")
    summary = summarize(queue)
    print(f"by_split: {dict(sorted(summary['by_split'].items()))}")
    print(f"by_bucket: {dict(sorted(summary['by_bucket'].items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
