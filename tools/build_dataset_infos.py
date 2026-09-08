#!/usr/bin/env python3
"""Build lightweight Hugging Face-style dataset metadata for Eng_Bench."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


FEATURES = {
    "id": {"dtype": "string"},
    "task": {"dtype": "string"},
    "question": {"dtype": "string"},
    "answer": {"dtype": "string"},
    "images": {"sequence": {"dtype": "string"}},
    "evidence": {"sequence": {"dtype": "dict"}},
    "split": {"dtype": "string"},
    "metadata": {"dtype": "dict"},
}


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def sorted_counts(counter: Counter) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter)}


def row_source_id(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if row.get("task") == "visualdiff":
        value = metadata.get("project_id")
        if value:
            return str(value)
        pair_id = metadata.get("pair_id")
        if pair_id:
            pair_id = str(pair_id)
            prefix, separator, suffix = pair_id.rpartition("__")
            return prefix if separator and suffix.isdigit() else pair_id
    value = metadata.get("doc_id") or metadata.get("pair_id")
    return str(value) if value else None


def split_infos(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts = Counter(str(row.get("split", "unknown")) for row in rows)
    return {split: {"name": split, "num_examples": counts[split]} for split in sorted(counts)}


def build_dataset_info(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources = {source for source in (row_source_id(row) for row in rows) if source}
    return {
        "eng_bench": {
            "description": "Engineering-document benchmark with visualdiff and microtext tasks.",
            "citation": "See DATACARD.md for provisional citation metadata.",
            "homepage": "not yet released",
            "license": "mixed public-source candidate; see DATACARD.md and SOURCE_INVENTORY.csv",
            "features": FEATURES,
            "splits": split_infos(rows),
            "task_counts": sorted_counts(Counter(str(row.get("task", "unknown")) for row in rows)),
            "source_count": len(sources),
            "version": "0.95-package-clean-candidate",
        }
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build dataset_infos.json for Eng_Bench.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl")
    parser.add_argument("--output", default="dataset_infos.json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    info = build_dataset_info(load_jsonl(root / args.input))
    output = root / args.output
    output.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
