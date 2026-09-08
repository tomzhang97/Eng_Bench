#!/usr/bin/env python3
"""Merge reviewed visualdiff annotations back into the main pairs file."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
    return items


def save_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def clean_review_metadata(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    status = out.get("annotation_status")
    if status == "accepted":
        out.setdefault("desc_source", "human_from_llm_draft")
    elif status == "edited":
        out.setdefault("desc_source", "human")
    out.pop("annotation_source", None)
    out.pop("annotation_status", None)
    out.pop("reviewed_at", None)
    out.pop("flag_reason", None)
    out.pop("review_priority", None)
    out.pop("review_bucket", None)
    out.pop("image_old", None)
    out.pop("image_new", None)
    return out


def merge_reviewed_pairs(
    existing_pairs: list[dict[str, Any]], reviewed_pairs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    valid_reviewed = {
        row["pair_id"]: clean_review_metadata(row)
        for row in reviewed_pairs
        if row.get("annotation_status") in {"accepted", "edited"}
    }
    flagged = {
        row["pair_id"]
        for row in reviewed_pairs
        if row.get("annotation_status") == "flagged"
    }

    merged: list[dict[str, Any]] = []
    stats = {"updated": 0, "preserved": 0, "flagged": len(flagged), "reviewed_without_pair": 0}
    seen_pair_ids: set[str] = set()

    for pair in existing_pairs:
        pair_id = str(pair.get("pair_id", ""))
        seen_pair_ids.add(pair_id)
        if pair_id in valid_reviewed:
            merged.append(valid_reviewed[pair_id])
            stats["updated"] += 1
        else:
            merged.append(pair)
            stats["preserved"] += 1

    stats["reviewed_without_pair"] = len(set(valid_reviewed) - seen_pair_ids)
    return merged, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge reviewed annotations into visualdiff pairs")
    parser.add_argument(
        "--pairs",
        default="visualdiff/annotations/visualdiff_pairs.jsonl",
        help="Existing main pairs JSONL",
    )
    parser.add_argument(
        "--reviewed",
        default="visualdiff/annotations/visualdiff_pairs_REVIEWED.jsonl",
        help="Reviewed annotations JSONL",
    )
    parser.add_argument(
        "--output",
        default="visualdiff/annotations/visualdiff_pairs.jsonl",
        help="Output pairs JSONL",
    )
    parser.add_argument("--backup", default=None, help="Optional backup path before overwrite")
    args = parser.parse_args()

    pairs_path = Path(args.pairs)
    reviewed_path = Path(args.reviewed)
    output_path = Path(args.output)
    backup_path = Path(args.backup) if args.backup else output_path.with_suffix(".jsonl.bak")

    print("[*] MAAP Merge Tool")
    print(f"    Existing pairs: {pairs_path}")
    print(f"    Reviewed: {reviewed_path}")
    print(f"    Output: {output_path}")

    existing_pairs = load_jsonl(pairs_path)
    reviewed_pairs = load_jsonl(reviewed_path)
    merged, stats = merge_reviewed_pairs(existing_pairs, reviewed_pairs)

    if output_path.exists():
        shutil.copy2(output_path, backup_path)
        print(f"    Backed up existing output to {backup_path}")

    save_jsonl(output_path, merged)

    print("\nMERGE COMPLETE")
    print(f"Existing pairs:        {len(existing_pairs)}")
    print(f"Reviewed rows:         {len(reviewed_pairs)}")
    print(f"Updated rows:          {stats['updated']}")
    print(f"Preserved rows:        {stats['preserved']}")
    print(f"Flagged rows skipped:  {stats['flagged']}")
    if stats["reviewed_without_pair"]:
        print(f"[WARN] Reviewed rows without existing pair: {stats['reviewed_without_pair']}")
        return 1
    print("\nNext step: python tools/rebuild_visualdiff_questions.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
