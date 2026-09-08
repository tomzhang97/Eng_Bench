#!/usr/bin/env python3
"""Mark intermediate review queues as superseded without deleting audit history."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


OPEN_STATUSES = {"", "candidate", "needs_review", "provisional_review", "todo"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def supersede_rows(rows: list[dict[str, Any]], superseded_by: str) -> tuple[list[dict[str, Any]], int]:
    updated_rows: list[dict[str, Any]] = []
    updated_count = 0
    for row in rows:
        updated = dict(row)
        status = str(updated.get("review_status") or updated.get("status") or "").strip().lower()
        if status in OPEN_STATUSES:
            updated["review_status"] = "machine_superseded"
            updated["machine_qa_status"] = "superseded"
            updated["machine_qa_notes"] = (
                f"Intermediate queue superseded by {superseded_by}; do not assign for human review."
            )
            updated["superseded_by"] = superseded_by
            updated_count += 1
        updated_rows.append(updated)
    return updated_rows, updated_count


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--superseded-by", required=True)
    args = parser.parse_args(argv)

    total_rows = 0
    total_updated = 0
    for path in args.input:
        rows = read_jsonl(path)
        updated_rows, updated_count = supersede_rows(rows, args.superseded_by)
        write_jsonl_atomic(path, updated_rows)
        total_rows += len(rows)
        total_updated += updated_count
        print(f"[OK] {path}: superseded {updated_count}/{len(rows)} rows")
    print(json.dumps({"files": len(args.input), "rows": total_rows, "superseded": total_updated}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
