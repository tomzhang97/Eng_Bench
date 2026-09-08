#!/usr/bin/env python3
"""Materialize terminal tombstones for an obsolete review-queue representation."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HUMAN_DECISION_FIELDS = {
    "human_description",
    "human_review_status",
    "human_status",
    "reviewed_by",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


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


def row_id(row: dict[str, Any]) -> str:
    return str(
        row.get("pair_id")
        or row.get("candidate_id")
        or row.get("id")
        or row.get("item_id")
        or ""
    ).strip()


def has_human_decision(row: dict[str, Any]) -> bool:
    return any(str(row.get(field) or "").strip() for field in HUMAN_DECISION_FIELDS)


def materialize(
    rows: list[dict[str, Any]],
    *,
    replacement: str,
    reason: str,
) -> list[dict[str, Any]]:
    identifiers = [row_id(row) for row in rows]
    missing = sum(not identifier for identifier in identifiers)
    duplicates = sorted(
        identifier
        for identifier, count in Counter(identifiers).items()
        if identifier and count > 1
    )
    protected = sorted(row_id(row) for row in rows if has_human_decision(row))
    if missing or duplicates or protected:
        raise ValueError(
            "invalid supersession input: "
            f"missing_ids={missing} duplicate_ids={duplicates} human_decisions={protected}"
        )

    output: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        row["supersession_original_review_status"] = str(row.get("review_status") or "")
        row["review_status"] = "machine_superseded"
        row["machine_qa_status"] = "machine_superseded"
        row["superseded_by"] = replacement
        row["machine_hold_reason"] = reason
        row["machine_qa_notes"] = (
            "Obsolete queue representation; use the named canonical replacement. "
            "Do not assign or merge this row."
        )
        row["safe_to_merge_gold"] = False
        output.append(row)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--replacement", required=True)
    parser.add_argument("--reason", default="superseded_by_canonical_review_queue")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args(argv)

    input_sha256 = file_sha256(args.input)
    rows = read_jsonl(args.input)
    output_rows = materialize(rows, replacement=args.replacement, reason=args.reason)
    write_jsonl(args.output, output_rows)
    report = {
        "input": args.input.as_posix(),
        "input_sha256": input_sha256,
        "replacement": args.replacement,
        "reason": args.reason,
        "output": args.output.as_posix(),
        "output_sha256": file_sha256(args.output),
        "input_rows": len(rows),
        "superseded_rows": len(output_rows),
        "unique_ids": len({row_id(row) for row in output_rows}),
        "safe_to_merge_gold": False,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
