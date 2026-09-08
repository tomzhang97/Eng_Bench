#!/usr/bin/env python3
"""Capture a frozen SHA-256 reference for the active Eng_Bench release."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ACTIVE_RELEASE_PATHS = (
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "eng_bench.jsonl",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def jsonl_rows(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_reference(root: Path, date_label: str, source_transaction: str = "") -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative in ACTIVE_RELEASE_PATHS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"missing_active_release_file:{relative}")
        files.append({
            "path": relative,
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
        })
    reference = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_rows": jsonl_rows(root / "eng_bench.jsonl"),
        "files": files,
        "valid": True,
    }
    if source_transaction:
        reference["source_transaction"] = source_transaction.replace("\\", "/")
    return reference


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--source-transaction", default="")
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output_json if args.output_json.is_absolute() else root / args.output_json
    reference = build_reference(root, args.date_label, args.source_transaction)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "valid": True,
        "active_gold_rows": reference["active_gold_rows"],
        "files": len(reference["files"]),
        "output": output.as_posix(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
