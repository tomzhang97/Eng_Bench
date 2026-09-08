#!/usr/bin/env python3
"""Materialize an audited evidence-render plan into a review-only JSONL queue."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_plan(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def path_missing(root: Path, row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for value in source_readiness.path_fields(row):
        path = Path(value)
        full_path = path if path.is_absolute() else root / path
        if not full_path.exists():
            missing.append(value)
    return missing


def materialize(root: Path, plan_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan_rows = read_plan(plan_path)
    queue_cache: dict[str, dict[str, dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[str] = set()
    by_doc: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_queue: Counter[str] = Counter()

    for plan_row in plan_rows:
        candidate_id = str(plan_row.get("candidate_id") or "").strip()
        queue_path = str(plan_row.get("queue_path") or "").strip()
        if not candidate_id or not queue_path:
            issues.append("plan row is missing candidate_id or queue_path")
            continue
        if candidate_id in seen:
            issues.append(f"duplicate candidate ID in plan: {candidate_id}")
            continue
        seen.add(candidate_id)
        if queue_path not in queue_cache:
            full_queue_path = root / queue_path
            if not full_queue_path.is_file():
                issues.append(f"queue file is missing: {queue_path}")
                queue_cache[queue_path] = {}
            else:
                queue_cache[queue_path] = {
                    source_readiness.row_identity(row, "microtext"): row
                    for row in source_readiness.read_jsonl(full_queue_path)
                    if source_readiness.row_identity(row, "microtext")
                }
        source_row = queue_cache[queue_path].get(candidate_id)
        if source_row is None:
            issues.append(f"candidate is missing from planned queue: {candidate_id} @ {queue_path}")
            continue
        if str(source_row.get("doc_id") or "") != str(plan_row.get("doc_id") or ""):
            issues.append(f"document mismatch for {candidate_id}")
            continue
        page_value = source_row.get("page_index", source_row.get("page", 0))
        try:
            page_index = int(page_value or 0)
            planned_page = int(plan_row.get("page_index_0based") or 0)
        except (TypeError, ValueError):
            issues.append(f"invalid page index for {candidate_id}")
            continue
        if page_index != planned_page:
            issues.append(f"page mismatch for {candidate_id}: {page_index} != {planned_page}")
            continue
        exclusion = source_readiness.review_exclusion_reason(source_row)
        if exclusion:
            issues.append(f"candidate became non-actionable: {candidate_id}: {exclusion}")
            continue
        missing = path_missing(root, source_row)
        if missing:
            issues.append(f"candidate still has missing evidence: {candidate_id}: {';'.join(missing)}")
            continue
        row = dict(source_row)
        row["review_status"] = "needs_review"
        row["promotion_state"] = "unreviewed_candidate"
        row["safe_to_merge_gold"] = False
        row["machine_qa_status"] = "evidence_repair_materialized"
        row["evidence_repair_plan"] = plan_path.relative_to(root).as_posix()
        row["evidence_repair_source_queue"] = queue_path
        output.append(row)
        by_doc[str(row.get("doc_id") or "unknown")] += 1
        by_category[str(row.get("category") or "unknown")] += 1
        by_queue[queue_path] += 1

    report = {
        "goal": "Gold v2.0 Global",
        "plan_path": plan_path.relative_to(root).as_posix(),
        "plan_sha256": sha256_file(plan_path),
        "plan_rows": len(plan_rows),
        "materialized_rows": len(output),
        "by_doc": dict(sorted(by_doc.items())),
        "by_category": dict(sorted(by_category.items())),
        "by_source_queue": dict(sorted(by_queue.items())),
        "issues": issues,
        "valid": not issues and len(output) == len(plan_rows),
        "active_gold_modified": False,
        "interpretation": (
            "Rows are review-only and safe_to_merge_gold=false. They still require strict "
            "assembly, collision checks, visual QA, and human acceptance."
        ),
    }
    return output, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Materialized Review Evidence Plan",
        "",
        "- Goal: **Gold v2.0 Global**",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Planned rows: `{report['plan_rows']}`",
        f"- Materialized review-only rows: `{report['materialized_rows']}`",
        f"- Issues: `{len(report['issues'])}`",
        "- Active gold modified: `false`",
        "",
        "## Documents",
        "",
    ]
    lines.extend(f"- `{doc_id}`: {count}" for doc_id, count in report["by_doc"].items())
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.extend(["", report["interpretation"], ""])
    return "\n".join(lines)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--plan-csv", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    plan_path = (root / args.plan_csv).resolve()
    rows, report = materialize(root, plan_path)
    output_path = root / args.output_jsonl
    write_jsonl(output_path, rows)
    report["output_jsonl"] = output_path.relative_to(root).as_posix()
    report["output_sha256"] = sha256_file(output_path)
    report_json = root / args.report_json
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md = root / args.report_md
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("plan_rows", "materialized_rows", "valid")}, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
