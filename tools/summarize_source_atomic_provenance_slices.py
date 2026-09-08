#!/usr/bin/env python3
"""Summarize source-atomic provenance slices into one deduplicated review queue."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def candidate_identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def build_summary(*, slices_dir: Path, output_dir: Path, date_label: str) -> dict[str, Any]:
    report_paths = sorted(slices_dir.rglob("source_atomic_slice_report.json"))
    issues: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    candidate_rows: dict[str, dict[str, Any]] = {}
    candidate_sources: dict[str, set[str]] = defaultdict(set)

    for report_path in report_paths:
        report = read_json(report_path)
        source_doc = str(report.get("source_doc") or "")
        outstanding_ref = str((report.get("artifacts") or {}).get("outstanding") or "")
        outstanding_path = slices_dir.parents[2] / outstanding_ref if outstanding_ref else None
        if outstanding_path is None or not outstanding_path.exists():
            issues.append({"code": "missing_outstanding_artifact", "source_doc": source_doc})
            outstanding_rows: list[dict[str, Any]] = []
        else:
            outstanding_rows = read_jsonl(outstanding_path)
        expected = int(report.get("selected_outstanding_review_rows") or 0)
        if len(outstanding_rows) != expected:
            issues.append(
                {
                    "code": "outstanding_count_mismatch",
                    "source_doc": source_doc,
                    "report": expected,
                    "artifact": len(outstanding_rows),
                }
            )
        source_row = {
            "source_doc": source_doc,
            "active_references": int(report.get("active_source_references") or 0),
            "affected_rows": int(report.get("affected_rows") or 0),
            "reviewed_replacements": int(report.get("selected_reviewed_replacements") or 0),
            "outstanding_review_rows": expected,
            "unfillable_rows": int(report.get("unfillable_replacement_rows") or 0),
            "ready_for_migration_readiness_audit": bool(
                report.get("ready_for_migration_readiness_audit")
            ),
        }
        sources.append(source_row)
        for candidate in outstanding_rows:
            identity = candidate_identity(candidate)
            if not identity:
                issues.append({"code": "missing_candidate_identity", "source_doc": source_doc})
                continue
            if identity in candidate_rows and candidate_rows[identity] != candidate:
                issues.append({"code": "candidate_payload_mismatch", "candidate_id": identity})
                continue
            candidate_rows[identity] = candidate
            candidate_sources[identity].add(source_doc)

    sources.sort(key=lambda row: (row["outstanding_review_rows"], row["source_doc"]))
    source_deficits = {row["source_doc"]: row["outstanding_review_rows"] for row in sources}
    queue: list[dict[str, Any]] = []
    for identity, candidate in candidate_rows.items():
        docs = sorted(candidate_sources[identity])
        queue.append(
            {
                **candidate,
                "source_atomic_priority_docs": docs,
                "source_atomic_priority_doc_count": len(docs),
                "source_atomic_minimum_closure_size": min(source_deficits[doc] for doc in docs),
            }
        )
    queue.sort(
        key=lambda row: (
            int(row["source_atomic_minimum_closure_size"]),
            -int(row["source_atomic_priority_doc_count"]),
            candidate_identity(row),
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "source_atomic_outstanding_deduplicated.jsonl"
    csv_path = output_dir / "source_atomic_coverage_summary.csv"
    priority_csv_path = output_dir / "source_atomic_priority_rows.csv"
    json_path = output_dir / "source_atomic_coverage_summary.json"
    md_path = output_dir / "source_atomic_coverage_summary.md"
    write_jsonl(queue_path, queue)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sources[0]) if sources else ["source_doc"])
        writer.writeheader()
        writer.writerows(sources)
    priority_fields = [
        "priority_rank",
        "candidate_id",
        "primary_index",
        "engineering_required",
        "task",
        "category",
        "proposed_text",
        "replacement_for_category",
        "source_atomic_priority_docs",
        "source_atomic_minimum_closure_size",
    ]
    with priority_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=priority_fields)
        writer.writeheader()
        for rank, row in enumerate(queue, start=1):
            writer.writerow(
                {
                    "priority_rank": rank,
                    "candidate_id": candidate_identity(row),
                    "primary_index": row.get("primary_index", ""),
                    "engineering_required": row.get("engineering_required", False),
                    "task": row.get("task", ""),
                    "category": row.get("category", ""),
                    "proposed_text": row.get("proposed_text") or row.get("description") or "",
                    "replacement_for_category": row.get("replacement_for_category", ""),
                    "source_atomic_priority_docs": ";".join(
                        row.get("source_atomic_priority_docs") or []
                    ),
                    "source_atomic_minimum_closure_size": row.get(
                        "source_atomic_minimum_closure_size", ""
                    ),
                }
            )

    summary = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "valid": not issues,
        "active_gold_modified": False,
        "source_count": len(sources),
        "ready_source_count": sum(
            1 for row in sources if row["ready_for_migration_readiness_audit"]
        ),
        "source_outstanding_review_assignments": sum(
            row["outstanding_review_rows"] for row in sources
        ),
        "deduplicated_outstanding_review_rows": len(queue),
        "sources": sources,
        "issues": issues,
        "artifacts": {
            "deduplicated_queue": queue_path.as_posix(),
            "coverage_csv": csv_path.as_posix(),
            "priority_csv": priority_csv_path.as_posix(),
            "coverage_md": md_path.as_posix(),
        },
        "interpretation": (
            "Review priorities only. No active Gold row may be replaced until one source is "
            "fully reviewed and passes readiness, preview, strict, split, leakage, duplicate, "
            "and provenance gates."
        ),
    }
    write_json(json_path, summary)
    lines = [
        "# Source-Atomic Provenance Replacement Coverage",
        "",
        f"- Goal: **{summary['goal']}**",
        f"- Valid: `{str(summary['valid']).lower()}`",
        f"- Sources ready now: `{summary['ready_source_count']}/{summary['source_count']}`",
        f"- Source-level outstanding assignments: `{summary['source_outstanding_review_assignments']}`",
        f"- Deduplicated outstanding rows: `{summary['deduplicated_outstanding_review_rows']}`",
        "- Active Gold modified: `false`",
        "- Human action: use `source_atomic_priority_rows.csv` to locate these rows by "
        "`primary_index` in the existing primary workbook; do not issue a duplicate packet.",
        "",
        "| Priority | Blocked source | Active refs | Reviewed | Outstanding | Unfillable | Ready |",
        "| ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, row in enumerate(sources, start=1):
        lines.append(
            f"| {index} | `{row['source_doc']}` | {row['active_references']} | "
            f"{row['reviewed_replacements']} | {row['outstanding_review_rows']} | "
            f"{row['unfillable_rows']} | "
            f"`{str(row['ready_for_migration_readiness_audit']).lower()}` |"
        )
    lines.extend(["", "## Issues", ""])
    lines.extend(
        ["- None."]
        if not issues
        else [f"- `{issue['code']}`: `{json.dumps(issue, ensure_ascii=False)}`" for issue in issues]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slices-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--require-valid", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = build_summary(
        slices_dir=args.slices_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        date_label=args.date_label,
    )
    print(
        json.dumps(
            {
                "valid": summary["valid"],
                "ready_sources": summary["ready_source_count"],
                "sources": summary["source_count"],
                "deduplicated_outstanding_rows": summary[
                    "deduplicated_outstanding_review_rows"
                ],
            },
            indent=2,
        )
    )
    return 1 if args.require_valid and not summary["valid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
