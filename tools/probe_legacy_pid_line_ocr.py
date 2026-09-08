#!/usr/bin/env python3
"""Probe unclassified OCR rows for compact legacy P&ID line-number morphology."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


COMPACT_LINE_RE = re.compile(
    r"^(?P<sequence>\d{2,3})-(?P<size>(?:\d{1,2}|\d?/[24])?)-(?P<service>[PQG0O])$",
    re.IGNORECASE,
)
CLIPPED_LINE_RE = re.compile(
    r"^(?P<sequence>\d{2,3})-(?P<size>[0-9/]{0,3})-(?P<service>[A-Z0-9]{0,2})$",
    re.IGNORECASE,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_input_row"] = line_number
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            clean = {key: value for key, value in row.items() if not key.startswith("_")}
            handle.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")


def normalized_text(value: object) -> str:
    return (
        str(value or "")
        .strip()
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
    )


def probe_reason(text: str, *, include_clipped: bool = False) -> str | None:
    normalized = normalized_text(text)
    match = COMPACT_LINE_RE.fullmatch(normalized)
    if not match:
        if include_clipped and CLIPPED_LINE_RE.fullmatch(normalized):
            return "clipped_compact_legacy_line_needs_context_probe"
        return None
    size = match.group("size")
    service = match.group("service").upper()
    if service in {"0", "O"}:
        return "compact_legacy_line_with_probable_q_ocr_confusion"
    if not size:
        return "compact_legacy_line_with_missing_size_probe"
    return "compact_legacy_line_identifier"


def build_probe(
    rows: list[dict[str, Any]],
    *,
    input_category: str = "unknown_microtext",
    include_clipped: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()

    for source_row in rows:
        row = dict(source_row)
        if str(row.get("category") or "") != input_category:
            row["machine_probe_hold_reason"] = "outside_input_category"
            held.append(row)
            reason_counts["outside_input_category"] += 1
            continue

        reason = probe_reason(
            str(row.get("proposed_text") or ""),
            include_clipped=include_clipped,
        )
        if reason is None:
            row["machine_probe_hold_reason"] = "outside_guarded_line_morphology"
            held.append(row)
            reason_counts["outside_guarded_line_morphology"] += 1
            continue

        row["machine_probe_status"] = "legacy_pid_line_morphology_needs_visual_qa"
        row["machine_probe_reason"] = reason
        row["safe_to_merge_gold"] = False
        selected.append(row)
        reason_counts[reason] += 1

    report = {
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "input_category": input_category,
        "include_clipped": include_clipped,
        "reasons": dict(sorted(reason_counts.items())),
        "interpretation": (
            "Diagnostic morphology probe only. Selected rows remain unknown_microtext and require "
            "visual QA plus human review before any Gold promotion."
        ),
    }
    return selected, held, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--input-category", default="unknown_microtext")
    parser.add_argument(
        "--include-clipped",
        action="store_true",
        help=(
            "Also include incomplete compact forms for page-context diagnosis. "
            "This does not make them category-ready."
        ),
    )
    args = parser.parse_args()

    selected, held, report = build_probe(
        read_jsonl(args.input),
        input_category=args.input_category,
        include_clipped=args.include_clipped,
    )
    write_jsonl(args.output, selected)
    write_jsonl(args.held_output, held)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
