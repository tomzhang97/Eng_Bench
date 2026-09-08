#!/usr/bin/env python3
"""Merge machine-certification OCR caches with deterministic latest-result wins."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(row)
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_caches(
    caches: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    replacement_rows = 0
    for name, rows in caches:
        for row_number, row in enumerate(rows, start=1):
            evidence_sha = str(row.get("evidence_sha256") or "").strip().lower()
            if len(evidence_sha) != 64:
                raise ValueError(f"{name}:{row_number}: invalid evidence_sha256")
            existing = merged.get(evidence_sha)
            if existing is not None:
                duplicate_rows += 1
                if existing != row:
                    replacement_rows += 1
            merged[evidence_sha] = dict(row)
    return [merged[key] for key in sorted(merged)], {
        "input_rows": sum(len(rows) for _, rows in caches),
        "output_rows": len(merged),
        "duplicate_rows": duplicate_rows,
        "replacement_rows": replacement_rows,
    }


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cache", action="append", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    source_paths = [resolve(path) for path in args.cache]
    rows, counts = merge_caches(
        [(path.as_posix(), read_jsonl(path)) for path in source_paths]
    )
    output_path = resolve(args.output_jsonl)
    write_jsonl_atomic(output_path, rows)
    report = {
        "goal": "Gold v2.0 Global",
        "valid": True,
        "mode": "machine_ocr_cache_merge",
        "source_caches": [
            {"path": path.as_posix(), "sha256": file_sha256(path)}
            for path in source_paths
        ],
        "counts": counts,
        "output_path": output_path.as_posix(),
        "output_sha256": file_sha256(output_path),
        "active_gold_modified": False,
    }
    write_json_atomic(resolve(args.report_json), report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
