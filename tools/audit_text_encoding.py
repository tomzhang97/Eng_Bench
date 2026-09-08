#!/usr/bin/env python3
"""Audit JSON/JSONL rows for benchmark-facing text encoding corruption."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from text_encoding import mojibake_signatures


PROVENANCE_ONLY_FIELDS = {
    "machine_text_corrected_from",
    "source_raw_text",
    "source_text_parts",
    "upstream_raw_text",
}


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            return [payload]
        raise ValueError(f"JSON root must be an object or list: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row {line_number} must be an object: {path}")
            rows.append(row)
    return rows


def iter_strings(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_strings(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_strings(child, (*path, f"[{index}]"))
    elif isinstance(value, str):
        yield path, value


def is_provenance_only(path: tuple[str, ...]) -> bool:
    return any(part in PROVENANCE_ONLY_FIELDS for part in path)


def audit_paths(paths: list[Path], example_limit: int = 100) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    rows_scanned = 0
    strings_scanned = 0
    for path in paths:
        rows = read_rows(path)
        rows_scanned += len(rows)
        for row_index, row in enumerate(rows, 1):
            row_id = str(
                row.get("candidate_id")
                or row.get("id")
                or row.get("item_id")
                or row.get("pair_id")
                or row.get("qid")
                or row_index
            )
            for field_path, text in iter_strings(row):
                strings_scanned += 1
                signatures = mojibake_signatures(text)
                if not signatures:
                    continue
                issues.append(
                    {
                        "file": path.as_posix(),
                        "row": row_index,
                        "row_id": row_id,
                        "field": ".".join(field_path),
                        "severity": "provenance_warning" if is_provenance_only(field_path) else "benchmark_error",
                        "signatures": list(signatures),
                        "text": text,
                    }
                )
    counts = Counter(issue["severity"] for issue in issues)
    return {
        "valid": counts["benchmark_error"] == 0,
        "files_scanned": len(paths),
        "rows_scanned": rows_scanned,
        "strings_scanned": strings_scanned,
        "benchmark_errors": counts["benchmark_error"],
        "provenance_warnings": counts["provenance_warning"],
        "issues": issues[:example_limit],
        "issues_truncated": max(0, len(issues) - example_limit),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Text Encoding Audit",
        "",
        f"- Valid benchmark-facing text: `{str(report['valid']).lower()}`",
        f"- Files scanned: {report['files_scanned']}",
        f"- Rows scanned: {report['rows_scanned']}",
        f"- Benchmark-facing errors: {report['benchmark_errors']}",
        f"- Provenance-only warnings: {report['provenance_warnings']}",
    ]
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        for issue in report["issues"]:
            lines.append(
                f"- `{issue['severity']}` `{issue['file']}:{issue['row']}` "
                f"`{issue['field']}` ({', '.join(issue['signatures'])})"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="JSON or JSONL path; repeatable")
    parser.add_argument("--report-json")
    parser.add_argument("--report-md")
    parser.add_argument("--fail-on-provenance-warning", action="store_true")
    args = parser.parse_args()

    paths = [Path(value) for value in args.input]
    report = audit_paths(paths)
    if args.report_json:
        output = Path(args.report_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.report_md:
        output = Path(args.report_md)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(report), encoding="utf-8")

    summary_keys = (
        "valid",
        "files_scanned",
        "rows_scanned",
        "strings_scanned",
        "benchmark_errors",
        "provenance_warnings",
        "issues_truncated",
    )
    print(json.dumps({key: report[key] for key in summary_keys}, indent=2))
    if report["benchmark_errors"]:
        return 1
    if args.fail_on_provenance_warning and report["provenance_warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
