#!/usr/bin/env python3
"""Partition staged Eng_Bench rows by strict, reproducible source rights."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import manifest_maps, read_csv
from audit_staged_v2_capacity import (
    audit_source_doc,
    read_rows,
    row_identity,
    row_source_docs,
    task_for_row,
)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def partition_rows(
    root: Path, input_paths: list[Path]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    source_cache: dict[str, dict[str, Any]] = {}

    def source_audit(doc_id: str) -> dict[str, Any]:
        if doc_id not in source_cache:
            source_cache[doc_id] = audit_source_doc(root, doc_id, docs, inventory)
        return source_cache[doc_id]

    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    input_counts: dict[str, int] = {}
    task_input: Counter[str] = Counter()
    task_passing: Counter[str] = Counter()
    hold_reasons: Counter[str] = Counter()
    held_examples: list[dict[str, Any]] = []

    for input_path in input_paths:
        absolute = input_path if input_path.is_absolute() else root / input_path
        rows = read_rows(absolute)
        input_counts[display_path(root, absolute)] = len(rows)
        for row in rows:
            task = task_for_row(row)
            task_input[task] += 1
            doc_ids, resolution_issue = row_source_docs(row, docs, manifest_pairs)
            reasons: list[str] = []
            if resolution_issue:
                reasons.append(f"unresolved_source:{resolution_issue}")
            if not doc_ids and not resolution_issue:
                reasons.append("unresolved_source:no_source_documents")
            for doc_id in doc_ids:
                audit = source_audit(doc_id)
                for issue in audit["issues"]:
                    reasons.append(f"source_not_paper_ready:{doc_id}:{issue}")

            if not reasons:
                passing.append(dict(row))
                task_passing[task] += 1
                continue

            unique_reasons = list(dict.fromkeys(reasons))
            held_row = dict(row)
            held_row["source_rights_status"] = "held"
            held_row["source_rights_doc_ids"] = doc_ids
            held_row["source_rights_hold_reasons"] = unique_reasons
            held.append(held_row)
            hold_reasons.update(unique_reasons)
            if len(held_examples) < 100:
                held_examples.append(
                    {
                        "identity": row_identity(row),
                        "task": task,
                        "doc_ids": doc_ids,
                        "reasons": unique_reasons,
                    }
                )

    document_rows = [source_cache[key] for key in sorted(source_cache)]
    report = {
        "goal": "Gold v2.0 Global",
        "policy": (
            "Only explicit redistribution-license or public-domain statuses pass. "
            "Public availability alone, no-derivatives licenses, and noncommercial-only "
            "licenses remain held."
        ),
        "inputs": input_counts,
        "input_rows": sum(input_counts.values()),
        "passing_rows": len(passing),
        "held_rows": len(held),
        "task_input_rows": dict(sorted(task_input.items())),
        "task_passing_rows": dict(sorted(task_passing.items())),
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "documents": document_rows,
        "paper_ready_documents": sum(row["paper_ready"] for row in document_rows),
        "held_documents": sum(not row["paper_ready"] for row in document_rows),
        "held_examples": held_examples,
        "active_gold_modified": False,
        "partition_complete": len(passing) + len(held) == sum(input_counts.values()),
    }
    report["valid"] = report["partition_complete"]
    return passing, held, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strict Source-Rights Partition",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Input rows: `{report['input_rows']}`",
        f"- Rights-cleared rows: `{report['passing_rows']}`",
        f"- Held rows: `{report['held_rows']}`",
        f"- Paper-ready documents: `{report['paper_ready_documents']}`",
        f"- Held documents: `{report['held_documents']}`",
        f"- Active gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Valid partition: `{str(report['valid']).lower()}`",
        "",
        "## Holds",
        "",
        "| Reason | Rows |",
        "| --- | ---: |",
    ]
    for reason, count in report["hold_reasons"].items():
        lines.append(f"| `{reason}` | {count} |")
    lines.extend(
        [
            "",
            "Held rows remain useful annotation work but cannot enter public gold until their source rights are resolved.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--passing-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    passing, held, report = partition_rows(root, args.input)
    passing_output = args.passing_output if args.passing_output.is_absolute() else root / args.passing_output
    held_output = args.held_output if args.held_output.is_absolute() else root / args.held_output
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    write_jsonl(passing_output, passing)
    write_jsonl(held_output, held)
    report["passing_output"] = display_path(root, passing_output)
    report["held_output"] = display_path(root, held_output)
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "input_rows": report["input_rows"],
                "passing_rows": report["passing_rows"],
                "held_rows": report["held_rows"],
                "paper_ready_documents": report["paper_ready_documents"],
                "held_documents": report["held_documents"],
                "valid": report["valid"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
