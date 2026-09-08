#!/usr/bin/env python3
"""Split a review JSONL queue into curated keep and machine-hold outputs."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected JSON object")
        rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_keep_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            ids.append(value)
    return ids


def row_id(row: dict[str, Any], field: str = "") -> str:
    if field:
        return str(row.get(field) or "").strip()
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def row_group(row: dict[str, Any]) -> str:
    return str(row.get("doc_id") or row.get("project_id") or "unknown").strip()


def build_report(
    root: str | Path,
    *,
    input_jsonl: str | Path,
    keep_ids_file: str | Path,
    hold_reason: str,
    row_id_field: str = "",
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(input_jsonl)
    keep_path = Path(keep_ids_file)
    if not input_path.is_absolute():
        input_path = root / input_path
    if not keep_path.is_absolute():
        keep_path = root / keep_path
    rows = read_jsonl(input_path)
    keep_ids = read_keep_ids(keep_path)
    duplicate_keep_ids = sorted(
        candidate_id for candidate_id, count in Counter(keep_ids).items() if count > 1
    )
    if duplicate_keep_ids:
        raise ValueError(f"duplicate keep IDs: {', '.join(duplicate_keep_ids)}")
    row_ids = [row_id(row, row_id_field) for row in rows]
    missing_row_ids = [index for index, value in enumerate(row_ids, start=1) if not value]
    if missing_row_ids:
        raise ValueError(f"queue rows missing a stable ID: {missing_row_ids[:10]}")
    duplicate_row_ids = sorted(
        identifier for identifier, count in Counter(row_ids).items() if count > 1
    )
    if duplicate_row_ids:
        raise ValueError(f"duplicate queue IDs: {', '.join(duplicate_row_ids)}")
    by_id = {row_id(row, row_id_field): row for row in rows}
    missing_keep_ids = sorted(set(keep_ids) - set(by_id))
    if missing_keep_ids:
        raise ValueError(f"keep IDs missing from queue: {', '.join(missing_keep_ids)}")
    keep_set = set(keep_ids)
    kept = [row for row in rows if row_id(row, row_id_field) in keep_set]
    held = []
    for row in rows:
        if row_id(row, row_id_field) in keep_set:
            continue
        held_row = dict(row)
        held_row["machine_hold_reason"] = hold_reason
        held.append(held_row)
    return {
        "input_jsonl": input_path.as_posix(),
        "keep_ids_file": keep_path.as_posix(),
        "row_id_field": row_id_field or "auto",
        "hold_reason": hold_reason,
        "totals": {
            "input_rows": len(rows),
            "keep_ids": len(keep_ids),
            "kept_rows": len(kept),
            "held_rows": len(held),
        },
        "kept_by_doc": dict(sorted(Counter(row_group(row) for row in kept).items())),
        "held_by_doc": dict(sorted(Counter(row_group(row) for row in held).items())),
        "kept_rows": kept,
        "held_rows": held,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Review Queue Machine Curation",
        "",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Kept rows: `{totals['kept_rows']}`",
        f"- Held rows: `{totals['held_rows']}`",
        f"- Hold reason: `{report['hold_reason']}`",
        "- Kept rows remain human-review candidates, not gold.",
        "",
        "## Kept By Document",
        "",
    ]
    for doc_id, count in report["kept_by_doc"].items():
        lines.append(f"- `{doc_id}`: `{count}`")
    lines.extend(["", "## Held By Document", ""])
    for doc_id, count in report["held_by_doc"].items():
        lines.append(f"- `{doc_id}`: `{count}`")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a keep-ID curation to a review JSONL queue.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", required=True)
    parser.add_argument("--keep-ids", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--hold-output", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    parser.add_argument(
        "--row-id-field",
        default="",
        help="Optional row field to match against the keep-ID file (for example pre_padding_candidate_id).",
    )
    parser.add_argument(
        "--hold-reason",
        default="machine_curation_not_review_ready",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        input_jsonl=args.input,
        keep_ids_file=args.keep_ids,
        hold_reason=args.hold_reason,
        row_id_field=args.row_id_field,
    )
    output_path = root / args.output
    hold_output_path = root / args.hold_output
    report_json_path = root / args.report_json
    report_md_path = root / args.report_md
    write_jsonl(output_path, report["kept_rows"])
    write_jsonl(hold_output_path, report["held_rows"])
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(
        json.dumps({key: value for key, value in report.items() if not key.endswith("_rows")}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
