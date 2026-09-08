#!/usr/bin/env python3
"""Build a SHA-bound, full-coverage VisualDiff machine decision ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def jsonl_row_count(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            count += 1
    return count


def _entry_rows(entry: dict[str, Any]) -> list[int]:
    selectors = sum(
        [
            "row_number" in entry,
            "rows" in entry,
            "start" in entry or "end" in entry,
        ]
    )
    if selectors != 1:
        raise ValueError(
            "each hold must use exactly one selector: row_number, rows, or start/end"
        )
    if "row_number" in entry:
        return [int(entry["row_number"])]
    if "rows" in entry:
        values = entry["rows"]
        if not isinstance(values, list) or not values:
            raise ValueError("hold rows must be a non-empty list")
        return [int(value) for value in values]
    if "start" not in entry or "end" not in entry:
        raise ValueError("range holds require both start and end")
    start = int(entry["start"])
    end = int(entry["end"])
    if end < start:
        raise ValueError(f"invalid hold range: start={start} end={end}")
    return list(range(start, end + 1))


def build_decisions(input_path: Path, hold_spec_path: Path) -> dict[str, Any]:
    row_count = jsonl_row_count(input_path)
    if row_count < 1:
        raise ValueError("input JSONL has no rows")
    actual_sha = file_sha256(input_path)
    spec = json.loads(hold_spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("hold specification must be a JSON object")
    expected_sha = str(spec.get("input_sha256") or "").strip().upper()
    if expected_sha and expected_sha != actual_sha:
        raise ValueError(
            f"hold specification input SHA mismatch: expected={expected_sha} "
            f"actual={actual_sha}"
        )

    holds_by_row: dict[int, dict[str, Any]] = {}
    for entry in spec.get("holds", []):
        if not isinstance(entry, dict):
            raise ValueError("each hold must be a JSON object")
        reason = str(entry.get("reason") or "").strip()
        if not reason:
            raise ValueError("each hold requires a non-empty reason")
        for row_number in _entry_rows(entry):
            if row_number < 1 or row_number > row_count:
                raise ValueError(
                    f"hold row_number out of range: {row_number} (rows={row_count})"
                )
            if row_number in holds_by_row:
                raise ValueError(f"duplicate hold row_number: {row_number}")
            holds_by_row[row_number] = {
                "row_number": row_number,
                "reason": reason,
            }

    corrections: list[dict[str, Any]] = []
    correction_rows: set[int] = set()
    for raw in spec.get("corrections", []):
        if not isinstance(raw, dict):
            raise ValueError("each correction must be a JSON object")
        correction = dict(raw)
        row_number = int(correction.get("row_number") or 0)
        if row_number < 1 or row_number > row_count:
            raise ValueError(
                f"correction row_number out of range: {row_number} (rows={row_count})"
            )
        if row_number in correction_rows:
            raise ValueError(f"duplicate correction row_number: {row_number}")
        if row_number in holds_by_row:
            raise ValueError(f"held row cannot also be corrected: {row_number}")
        correction_rows.add(row_number)
        corrections.append(correction)

    held_rows = set(holds_by_row)
    keep_rows = [row_number for row_number in range(1, row_count + 1) if row_number not in held_rows]
    if "expected_keep_rows" in spec:
        expected_keep_rows = [int(value) for value in spec["expected_keep_rows"]]
        if len(expected_keep_rows) != len(set(expected_keep_rows)):
            raise ValueError("duplicate row number in expected_keep_rows")
        invalid_expected = sorted(
            value for value in expected_keep_rows if value < 1 or value > row_count
        )
        if invalid_expected:
            raise ValueError(f"expected_keep_rows out of range: {invalid_expected}")
        if sorted(expected_keep_rows) != keep_rows:
            missing = sorted(set(expected_keep_rows) - set(keep_rows))
            unexpected = sorted(set(keep_rows) - set(expected_keep_rows))
            raise ValueError(
                "derived keep rows do not match expected_keep_rows: "
                f"missing={missing} unexpected={unexpected}"
            )
    return {
        "input_sha256": actual_sha,
        "keep_rows": keep_rows,
        "holds": [holds_by_row[row_number] for row_number in sorted(holds_by_row)],
        "corrections": sorted(corrections, key=lambda value: int(value["row_number"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--hold-spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    decisions = build_decisions(args.input, args.hold_spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "input_rows": len(decisions["keep_rows"]) + len(decisions["holds"]),
                "kept_rows": len(decisions["keep_rows"]),
                "held_rows": len(decisions["holds"]),
                "corrected_rows": len(decisions["corrections"]),
                "input_sha256": decisions["input_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
