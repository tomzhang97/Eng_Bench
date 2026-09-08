#!/usr/bin/env python3
"""Normalize a human visualdiff polish CSV into merge-safe statuses.

The review pack allowed reviewers to use `edit` to describe what they saw. When
the notes say that the visible difference is outside the red evidence box, the
row is not usable as gold localization even if the full page changed. This tool
converts that pattern into a quarantine status while preserving the reviewer
description and notes.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDNAMES = [
    "review_index",
    "pair_id",
    "split",
    "project_id",
    "page_old",
    "page_new",
    "change_type",
    "current_description",
    "desc_source",
    "review_confidence",
    "mean_pixel_delta",
    "old_text_evidence",
    "new_text_evidence",
    "bbox_old",
    "bbox_new",
    "old_crop_path",
    "new_crop_path",
    "panel_path",
    "what_to_validate",
    "human_status",
    "human_description",
    "human_notes",
]


def load_csv(path: Path, encoding: str) -> list[dict[str, str]]:
    encodings = [encoding]
    if encoding.lower().replace("_", "-") != "gb18030":
        encodings.append("gb18030")
    last_error: UnicodeDecodeError | None = None
    for candidate in encodings:
        try:
            with path.open("r", encoding=candidate, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(FIELDNAMES)
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize_rows(rows: list[dict[str, str]], edit_with_notes_status: str) -> tuple[list[dict[str, str]], dict[str, int]]:
    stats = {
        "input_rows": len(rows),
        "valid": 0,
        "edit": 0,
        "reject_unclear": 0,
        "reject_no_change": 0,
        "reject_bbox_mismatch": 0,
        "needs_full_page": 0,
    }
    normalized = []
    for row in rows:
        out = dict(row)
        status = (out.get("human_status") or "").strip().lower()
        notes = (out.get("human_notes") or "").strip()
        if status == "reject_unclear":
            status = "reject_no_change"
        elif status == "edit" and notes and edit_with_notes_status:
            status = edit_with_notes_status
        out["human_status"] = status
        if status in stats:
            stats[status] += 1
        normalized.append(out)
    stats["output_rows"] = len(normalized)
    return normalized, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize visualdiff human-polish checklist statuses.")
    parser.add_argument("--input", required=True, help="Input human CSV")
    parser.add_argument("--output", required=True, help="Normalized UTF-8 CSV")
    parser.add_argument("--summary", required=True, help="Normalization summary JSON")
    parser.add_argument("--encoding", default="utf-8-sig", help="Input CSV encoding")
    parser.add_argument(
        "--edit-with-notes-status",
        default="reject_bbox_mismatch",
        choices=["", "reject_bbox_mismatch", "reject_unclear", "needs_full_page"],
        help="Status to use for edit rows that have reviewer notes",
    )
    args = parser.parse_args(argv)

    rows = load_csv(Path(args.input), args.encoding)
    normalized, stats = normalize_rows(rows, args.edit_with_notes_status)
    write_csv(Path(args.output), normalized)
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {args.output}")
    print(f"[OK] Wrote {args.summary}")
    print(f"[OK] Stats: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
