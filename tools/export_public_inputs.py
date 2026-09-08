#!/usr/bin/env python3
"""Export input-only Eng_Bench JSONL files with labels removed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


LABEL_FIELDS = {"answer", "answer_text", "evidence", "evidence_ids", "text_gt", "change_desc_gt"}


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def strip_labels(row: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in row.items() if key not in LABEL_FIELDS}
    metadata = public.get("metadata")
    if isinstance(metadata, dict):
        public["metadata"] = {
            key: value
            for key, value in metadata.items()
            if key not in LABEL_FIELDS and not str(key).endswith("_gt")
        }
    return public


def export_inputs(rows: list[dict[str, Any]], split: str = "all") -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected = [row for row in rows if split == "all" or row.get("split") == split]
    outputs = [strip_labels(row) for row in selected]
    return outputs, {"input_rows": len(rows), "output_rows": len(outputs)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export public input-only Eng_Bench JSONL.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified labeled JSONL")
    parser.add_argument("--split", default="test", help="Split to export, or all")
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSONL path; defaults to release/public_inputs/eng_bench_{split}_inputs.jsonl",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    outputs, stats = export_inputs(rows, args.split)
    output = (
        Path(args.output)
        if args.output
        else Path("release") / "public_inputs" / f"eng_bench_{args.split}_inputs.jsonl"
    )
    write_jsonl(root / output, outputs)
    print(f"[OK] Input rows: {stats['input_rows']}")
    print(f"[OK] Output rows: {stats['output_rows']}")
    print(f"[OK] Wrote {root / output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
