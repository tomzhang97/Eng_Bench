#!/usr/bin/env python3
"""Combine microtext candidate files and hold categories outside a balance target."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


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


def prepare_rows(
    input_paths: list[Path], target_categories: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not target_categories:
        raise ValueError("at least one target category is required")

    combined: list[dict[str, Any]] = []
    input_details: list[dict[str, Any]] = []
    for path in sorted(input_paths, key=lambda value: value.as_posix().casefold()):
        rows = read_jsonl(path)
        combined.extend(rows)
        input_details.append(
            {"path": path.as_posix(), "rows": len(rows), "sha256": file_sha256(path)}
        )

    target: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for original in combined:
        row = dict(original)
        category = str(row.get("category") or "").strip()
        if category in target_categories:
            row["review_status"] = "needs_review"
            row["balance_queue_status"] = "target_category"
            row["safe_to_merge_gold"] = False
            target.append(row)
            continue

        row.update(
            {
                "review_status": "machine_held",
                "balance_queue_status": "machine_held",
                "machine_hold_reason": "category_outside_balance_target",
                "safe_to_merge_gold": False,
            }
        )
        held.append(row)

    report = {
        "inputs": input_details,
        "target_categories": sorted(target_categories),
        "totals": {
            "input_files": len(input_details),
            "combined_rows": len(combined),
            "target_rows": len(target),
            "held_rows": len(held),
            "documents": len({str(row.get("doc_id") or "") for row in combined}),
        },
        "combined_categories": dict(
            sorted(Counter(str(row.get("category") or "") for row in combined).items())
        ),
        "target_category_counts": dict(
            sorted(Counter(str(row.get("category") or "") for row in target).items())
        ),
        "held_category_counts": dict(
            sorted(Counter(str(row.get("category") or "") for row in held).items())
        ),
        "interpretation": (
            "Target rows remain unreviewed candidates. Held rows are preserved for audit but "
            "must not be assigned or merged into Gold in this balance wave."
        ),
    }
    return combined, target, held, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Microtext Balance Queue Preparation",
        "",
        f"- Input files: `{totals['input_files']}`",
        f"- Combined rows: `{totals['combined_rows']}`",
        f"- Target rows: `{totals['target_rows']}`",
        f"- Machine-held rows: `{totals['held_rows']}`",
        f"- Source documents: `{totals['documents']}`",
        f"- Target categories: `{', '.join(report['target_categories'])}`",
        "",
        "## Target Counts",
        "",
    ]
    for category, count in report["target_category_counts"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Held Counts", ""])
    if report["held_category_counts"]:
        for category, count in report["held_category_counts"].items():
            lines.append(f"- `{category}`: `{count}`")
    else:
        lines.append("- None.")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--target-category", action="append", required=True)
    parser.add_argument("--combined-output", type=Path, required=True)
    parser.add_argument("--target-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    combined, target, held, report = prepare_rows(
        args.input, {str(value).strip() for value in args.target_category if str(value).strip()}
    )
    write_jsonl(args.combined_output, combined)
    write_jsonl(args.target_output, target)
    write_jsonl(args.held_output, held)
    report["outputs"] = {
        "combined": args.combined_output.as_posix(),
        "target": args.target_output.as_posix(),
        "held": args.held_output.as_posix(),
    }
    write_report(args.report_json, report)
    write_markdown(args.report_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
