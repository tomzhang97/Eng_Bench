#!/usr/bin/env python3
"""Select drawing-like PDF members from a source archive inventory."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_rows(
    rows: list[dict[str, str]],
    *,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> tuple[list[dict[str, str]], Counter[str]]:
    includes = [re.compile(pattern) for pattern in include_patterns]
    excludes = [re.compile(pattern) for pattern in exclude_patterns]
    output: list[dict[str, str]] = []
    reasons: Counter[str] = Counter()
    for source_row in rows:
        row = dict(source_row)
        member = str(row.get("member_path") or "")
        suffix = str(row.get("member_suffix") or Path(member).suffix).lower()
        archive_selected = str(row.get("selected_for_next_step") or "").lower() in {
            "true",
            "1",
            "yes",
            "y",
        }
        if not archive_selected:
            selected, reason = False, "not_selected_by_archive_inventory"
        elif suffix != ".pdf":
            selected, reason = False, "not_pdf"
        elif includes and not any(pattern.search(member) for pattern in includes):
            selected, reason = False, "include_pattern_miss"
        elif any(pattern.search(member) for pattern in excludes):
            selected, reason = False, "exclude_pattern_match"
        else:
            selected, reason = True, "selected_drawing_pdf"
        row["selected_for_next_step"] = "true" if selected else "false"
        row["next_step"] = (
            "Extract, validate, render, and mine as a staged drawing source."
            if selected
            else f"Hold from drawing conversion: {reason}."
        )
        output.append(row)
        reasons[reason] += 1
    return output, reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--include-regex", action="append", default=[])
    parser.add_argument("--exclude-regex", action="append", default=[])
    parser.add_argument("--expect-selected", type=int)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    input_path = args.input_csv if args.input_csv.is_absolute() else root / args.input_csv
    output_csv = args.output_csv if args.output_csv.is_absolute() else root / args.output_csv
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    selected_rows, reasons = select_rows(
        rows,
        include_patterns=args.include_regex,
        exclude_patterns=args.exclude_regex,
    )
    write_csv(output_csv, selected_rows, fieldnames)
    selected = sum(row["selected_for_next_step"] == "true" for row in selected_rows)
    valid = args.expect_selected is None or selected == args.expect_selected
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": args.date_label,
        "input_csv": input_path.relative_to(root).as_posix(),
        "output_csv": output_csv.relative_to(root).as_posix(),
        "rows": len(selected_rows),
        "selected": selected,
        "held": len(selected_rows) - selected,
        "selection_reasons": dict(sorted(reasons.items())),
        "include_regex": args.include_regex,
        "exclude_regex": args.exclude_regex,
        "expect_selected": args.expect_selected,
        "valid": valid,
        "safe_to_merge_gold": False,
    }
    write_json(output_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
