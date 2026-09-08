#!/usr/bin/env python3
"""Audit whether an agreement sample is current, release-safe, and review-complete."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import agreement_audit
from audit_active_gold_provenance import file_sha256, read_jsonl


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "id",
        "task",
        "split",
        "stratum",
        "source_doc_ids",
        "active_gold",
        "release_ready_sources",
        "source_blockers",
        "reference_evidence_valid",
        "evidence_paths_valid",
        "reviewer_a_complete",
        "reviewer_b_complete",
        "row_ready",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_doc_ids(row: dict[str, Any]) -> list[str]:
    value = str(row.get("source_doc_ids") or row.get("doc_id") or "")
    return [part.strip() for part in value.split(";") if part.strip()]


def evidence_paths_valid(packet_root: Path, row: dict[str, Any]) -> bool:
    values = [
        str(row.get(field) or "").strip()
        for field in ("primary_evidence_path", "page_path", "old_page_path", "new_page_path")
    ]
    required = values[0]
    if not required:
        return False
    for value in values:
        if not value:
            continue
        path = Path(value)
        resolved = path if path.is_absolute() else packet_root / path
        if not resolved.is_file():
            return False
    return True


def build_report(
    root: Path,
    *,
    reference_path: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    provenance_path: Path,
    expected_rows: int = 185,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reference_rows = agreement_audit.read_csv(reference_path)
    reviewer_a_rows = agreement_audit.read_csv(reviewer_a_path)
    reviewer_b_rows = agreement_audit.read_csv(reviewer_b_path)
    input_contract_issues = agreement_audit.validate_input_contract(
        reference_rows, reviewer_a_rows, reviewer_b_rows
    )
    input_contract_valid = not input_contract_issues
    reviewer_a = {str(row.get("id") or ""): row for row in reviewer_a_rows}
    reviewer_b = {str(row.get("id") or ""): row for row in reviewer_b_rows}
    active_ids = {
        str(row.get("id") or "").strip()
        for row in read_jsonl(root / "eng_bench.jsonl")
        if str(row.get("id") or "").strip()
    }
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance_docs = {
        str(row.get("doc_id") or "").strip(): row
        for row in provenance.get("documents", [])
        if str(row.get("doc_id") or "").strip()
    }

    row_results: list[dict[str, Any]] = []
    for row in reference_rows:
        identifier = str(row.get("id") or "").strip()
        docs = source_doc_ids(row)
        source_rows = [provenance_docs.get(doc_id) for doc_id in docs]
        release_ready = bool(docs) and all(
            source is not None and bool(source.get("release_ready")) for source in source_rows
        )
        blockers = sorted(
            {
                str(
                    source.get("blocker")
                    if source is not None
                    else "missing_provenance_record"
                )
                for source in source_rows
                if source is None or not source.get("release_ready")
            }
        )
        reference_valid = agreement_audit.valid_evidence(
            agreement_audit.parse_evidence(row.get("reference_evidence_json"))
        )
        paths_valid = evidence_paths_valid(reference_path.parent, row)
        a_complete = input_contract_valid and agreement_audit.completed_review(
            row, reviewer_a.get(identifier, {})
        )
        b_complete = input_contract_valid and agreement_audit.completed_review(
            row, reviewer_b.get(identifier, {})
        )
        active = identifier in active_ids
        row_results.append(
            {
                "id": identifier,
                "task": str(row.get("task") or ""),
                "split": str(row.get("split") or ""),
                "stratum": str(row.get("stratum") or ""),
                "source_doc_ids": ";".join(docs),
                "active_gold": active,
                "release_ready_sources": release_ready,
                "source_blockers": ";".join(blockers),
                "reference_evidence_valid": reference_valid,
                "evidence_paths_valid": paths_valid,
                "reviewer_a_complete": a_complete,
                "reviewer_b_complete": b_complete,
                "row_ready": active
                and release_ready
                and reference_valid
                and paths_valid
                and a_complete
                and b_complete,
            }
        )

    ids = [row["id"] for row in row_results]
    task_counts = Counter(row["task"] for row in row_results)
    blocker_counts = Counter(
        blocker
        for row in row_results
        for blocker in str(row["source_blockers"] or "").split(";")
        if blocker
    )
    sample_issues: list[str] = []
    if len(reference_rows) != expected_rows:
        sample_issues.append("reference_row_count_mismatch")
    if not all(ids) or len(ids) != len(set(ids)):
        sample_issues.append("reference_ids_missing_or_duplicate")
    if set(task_counts) != {"microtext", "visualdiff"}:
        sample_issues.append("both_tasks_not_represented")
    if any(not row["active_gold"] for row in row_results):
        sample_issues.append("sample_contains_non_active_rows")
    if any(not row["release_ready_sources"] for row in row_results):
        sample_issues.append("sample_contains_non_release_ready_sources")
    if any(not row["reference_evidence_valid"] for row in row_results):
        sample_issues.append("sample_contains_invalid_reference_evidence")
    if any(not row["evidence_paths_valid"] for row in row_results):
        sample_issues.append("sample_contains_missing_evidence_paths")
    if not input_contract_valid:
        sample_issues.append("reviewer_input_contract_invalid")

    review_issues: list[str] = []
    if any(not row["reviewer_a_complete"] for row in row_results):
        review_issues.append("reviewer_a_incomplete")
    if any(not row["reviewer_b_complete"] for row in row_results):
        review_issues.append("reviewer_b_incomplete")
    issues = sample_issues + review_issues

    report = {
        "goal": "Gold v2.0 Global",
        "expected_rows": expected_rows,
        "reference_rows": len(row_results),
        "unique_reference_ids": len(set(ids)),
        "task_counts": dict(sorted(task_counts.items())),
        "active_gold_rows": sum(bool(row["active_gold"]) for row in row_results),
        "release_ready_rows": sum(
            bool(row["release_ready_sources"]) for row in row_results
        ),
        "non_release_ready_rows": sum(
            not bool(row["release_ready_sources"]) for row in row_results
        ),
        "source_blocker_counts": dict(sorted(blocker_counts.items())),
        "valid_reference_evidence_rows": sum(
            bool(row["reference_evidence_valid"]) for row in row_results
        ),
        "valid_evidence_path_rows": sum(
            bool(row["evidence_paths_valid"]) for row in row_results
        ),
        "reviewer_a_complete_rows": sum(
            bool(row["reviewer_a_complete"]) for row in row_results
        ),
        "reviewer_b_complete_rows": sum(
            bool(row["reviewer_b_complete"]) for row in row_results
        ),
        "fully_ready_rows": sum(bool(row["row_ready"]) for row in row_results),
        "sample_review_ready": not sample_issues,
        "input_contract_valid": input_contract_valid,
        "input_contract_issues": input_contract_issues,
        "review_completion_pending": bool(review_issues),
        "sample_issues": sample_issues,
        "review_issues": review_issues,
        "issues": issues,
        "release_sample_ready": not issues,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "inputs": {
            "reference": reference_path.as_posix(),
            "reference_sha256": file_sha256(reference_path),
            "reviewer_a": reviewer_a_path.as_posix(),
            "reviewer_a_sha256": file_sha256(reviewer_a_path),
            "reviewer_b": reviewer_b_path.as_posix(),
            "reviewer_b_sha256": file_sha256(reviewer_b_path),
            "provenance": provenance_path.as_posix(),
            "provenance_sha256": file_sha256(provenance_path),
        },
        "interpretation": (
            "Agreement metrics are release evidence only after every sampled row is active, "
            "release-ready, evidence-complete, and independently completed by both reviewers."
        ),
    }
    return report, row_results


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agreement Sample Readiness",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Reference rows: `{report['reference_rows']}/{report['expected_rows']}`",
        f"- Active Gold rows: `{report['active_gold_rows']}/{report['reference_rows']}`",
        f"- Release-ready source rows: `{report['release_ready_rows']}/{report['reference_rows']}`",
        f"- Reviewer A complete: `{report['reviewer_a_complete_rows']}/{report['reference_rows']}`",
        f"- Reviewer B complete: `{report['reviewer_b_complete_rows']}/{report['reference_rows']}`",
        f"- Fully ready rows: `{report['fully_ready_rows']}/{report['reference_rows']}`",
        f"- Sample review-ready: `{str(report['sample_review_ready']).lower()}`",
        f"- Reviewer completion pending: `{str(report['review_completion_pending']).lower()}`",
        f"- Release sample ready: `{str(report['release_sample_ready']).lower()}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{issue}`" for issue in report["issues"])
    if not report["issues"]:
        lines.append("- None")
    lines.extend(
        [
            "",
            "This audit does not modify Gold. A completed but provenance-blocked sample must not be used as final Gold v2.0 agreement evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=185)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    report, rows = build_report(
        root,
        reference_path=resolve(args.reference),
        reviewer_a_path=resolve(args.reviewer_a),
        reviewer_b_path=resolve(args.reviewer_b),
        provenance_path=resolve(args.provenance),
        expected_rows=args.expected_rows,
    )
    output_json = resolve(args.output_json)
    output_md = resolve(args.output_md)
    output_csv = resolve(args.output_csv)
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, rows)
    print(
        json.dumps(
            {
                "reference_rows": report["reference_rows"],
                "release_ready_rows": report["release_ready_rows"],
                "reviewer_a_complete_rows": report["reviewer_a_complete_rows"],
                "reviewer_b_complete_rows": report["reviewer_b_complete_rows"],
                "sample_review_ready": report["sample_review_ready"],
                "review_completion_pending": report["review_completion_pending"],
                "release_sample_ready": report["release_sample_ready"],
            },
            indent=2,
        )
    )
    return 1 if args.strict and not report["release_sample_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
