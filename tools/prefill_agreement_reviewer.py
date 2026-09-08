#!/usr/bin/env python3
"""Prefill one agreement-audit reviewer from a prior returned workbook.

The output is deliberately reviewer-specific. It can preserve one person's
earlier decisions, but it must never be copied into the second independent
reviewer's checklist and it never mutates active gold JSONL.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from process_human_completion_workbook import read_review_workbook, text, yes_no
except ModuleNotFoundError:  # Imported as tools.prefill_agreement_reviewer in tests.
    from tools.process_human_completion_workbook import read_review_workbook, text, yes_no


DEFAULT_SHEET = "OLD GOLD CHECK"
REVIEW_FIELDS = (
    "answer_correct",
    "corrected_answer",
    "bbox_correct",
    "corrected_evidence_json",
    "accept_reject",
    "ambiguity",
    "rights_concern",
    "notes",
)
YES_NO = {"yes", "no"}
ACCEPT_REJECT = {"accept", "reject"}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalized(value: Any) -> str:
    return text(value).lower()


def parse_evidence(value: Any) -> list[dict[str, Any]]:
    raw = text(value)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


def valid_evidence(value: Any) -> bool:
    entries = parse_evidence(value)
    if not entries:
        return False
    for entry in entries:
        bbox = entry.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return False
        if any(not isinstance(number, (int, float)) for number in bbox):
            return False
    return True


def completion_issues(reference: dict[str, str], review: dict[str, str]) -> list[str]:
    issues: list[str] = []
    answer_correct = yes_no(review.get("answer_correct"))
    bbox_correct = yes_no(review.get("bbox_correct"))
    accept_reject = normalized(review.get("accept_reject"))
    ambiguity = yes_no(review.get("ambiguity"))
    rights_concern = yes_no(review.get("rights_concern"))

    if answer_correct not in YES_NO:
        issues.append("missing_or_invalid_answer_correct")
    if bbox_correct not in YES_NO:
        issues.append("missing_or_invalid_bbox_correct")
    if accept_reject not in ACCEPT_REJECT:
        issues.append("missing_or_invalid_accept_reject")
    if ambiguity not in YES_NO:
        issues.append("missing_or_invalid_ambiguity")
    if rights_concern not in YES_NO:
        issues.append("missing_or_invalid_rights_concern")
    if answer_correct == "no" and not text(review.get("corrected_answer")):
        issues.append("answer_marked_wrong_without_corrected_answer")
    if bbox_correct == "no" and not valid_evidence(review.get("corrected_evidence_json")):
        issues.append("bbox_marked_wrong_without_valid_corrected_evidence_json")
    if bbox_correct == "yes" and not valid_evidence(reference.get("reference_evidence_json")):
        issues.append("reference_evidence_json_invalid")
    return issues


def duplicate_ids(rows: list[dict[str, str]]) -> list[str]:
    counts = Counter(text(row.get("id")) for row in rows if text(row.get("id")))
    return sorted(identifier for identifier, count in counts.items() if count > 1)


def prefill_rows(
    references: list[dict[str, str]],
    template_rows: list[dict[str, str]],
    returned_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    errors: list[str] = []
    reference_ids = [text(row.get("id")) for row in references]
    template_ids = [text(row.get("id")) for row in template_rows]
    if reference_ids != template_ids:
        errors.append("template IDs/order differ from sample reference")
    if blanks := sum(not identifier for identifier in reference_ids):
        errors.append(f"sample reference has {blanks} blank IDs")
    for label, rows in (
        ("sample reference", references),
        ("template", template_rows),
        ("returned workbook", returned_rows),
    ):
        duplicates = duplicate_ids(rows)
        if duplicates:
            errors.append(f"{label} has duplicate IDs: {', '.join(duplicates[:10])}")

    returned_by_id = {
        text(row.get("id")): row
        for row in returned_rows
        if text(row.get("id"))
    }
    template_by_id = {
        text(row.get("id")): row
        for row in template_rows
        if text(row.get("id"))
    }
    output_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    remaining_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for reference in references:
        identifier = text(reference.get("id"))
        output = dict(template_by_id.get(identifier, reference))
        returned = returned_by_id.get(identifier)
        issues: list[str] = []
        if returned is None:
            status = "fresh_review"
            counts[status] += 1
        else:
            counts["overlap_rows"] += 1
            for field in REVIEW_FIELDS:
                output[field] = text(returned.get(field))
            issues = completion_issues(reference, output)
            status = "needs_correction" if issues else "prefilled_complete"
            counts[status] += 1
        output["prefill_status"] = status
        output["prefill_issues"] = "; ".join(issues)
        output_rows.append(output)
        coverage = {
            "id": identifier,
            "task": text(reference.get("task")),
            "stratum": text(reference.get("stratum")),
            "category": text(reference.get("category")),
            "prefill_status": status,
            "prefill_issues": "; ".join(issues),
            "primary_evidence_path": text(reference.get("primary_evidence_path")),
            "page_path": text(reference.get("page_path")),
            "old_page_path": text(reference.get("old_page_path")),
            "new_page_path": text(reference.get("new_page_path")),
        }
        coverage_rows.append(coverage)
        if status != "prefilled_complete":
            remaining_rows.append(output)

    reference_id_set = set(reference_ids)
    returned_only = sorted(identifier for identifier in returned_by_id if identifier not in reference_id_set)
    for identifier in returned_only:
        coverage_rows.append(
            {
                "id": identifier,
                "prefill_status": "returned_not_in_agreement_sample",
                "prefill_issues": "not copied into reviewer checklist",
            }
        )

    summary = {
        "agreement_complete": False,
        "safe_to_merge_gold": False,
        "safe_to_use_as_single_reviewer_prefill": not errors,
        "structural_errors": errors,
        "counts": {
            "agreement_rows": len(references),
            "returned_old_gold_rows": len(returned_rows),
            "overlap_rows": counts["overlap_rows"],
            "prefilled_complete_rows": counts["prefilled_complete"],
            "needs_correction_rows": counts["needs_correction"],
            "fresh_review_rows": counts["fresh_review"],
            "remaining_human_rows": len(remaining_rows),
            "returned_not_in_agreement_sample": len(returned_only),
        },
        "returned_not_in_agreement_sample_ids": returned_only,
        "independence_rule": (
            "This output may represent only one reviewer. The second reviewer must work "
            "independently and must not see or copy these prefilled answers."
        ),
    }
    return output_rows, coverage_rows, remaining_rows, summary


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Agreement Reviewer Prefill",
        "",
        f"- Safe as one-reviewer prefill: `{str(report['safe_to_use_as_single_reviewer_prefill']).lower()}`",
        "- Agreement complete: `false`",
        "- Safe to merge gold: `false`",
        f"- Agreement rows: `{counts['agreement_rows']}`",
        f"- Strictly reusable rows: `{counts['prefilled_complete_rows']}`",
        f"- Overlapping rows needing correction: `{counts['needs_correction_rows']}`",
        f"- Fresh rows needing review: `{counts['fresh_review_rows']}`",
        f"- Remaining human rows for this reviewer: `{counts['remaining_human_rows']}`",
        "",
        "## Independence Rule",
        "",
        report["independence_rule"],
        "",
        "Do not use this file for both reviewer A and reviewer B. The second reviewer must receive a blank checklist and work independently.",
    ]
    if report["structural_errors"]:
        lines.extend(["", "## Structural Errors", ""])
        lines.extend(f"- {error}" for error in report["structural_errors"])
    lines.append("")
    return "\n".join(lines)


def run_prefill(
    root: Path,
    workbook: Path,
    reference: Path,
    template: Path,
    output_dir: Path,
    reviewer: str,
    sheet: str = DEFAULT_SHEET,
) -> dict[str, Any]:
    root = root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    workbook = resolve(workbook)
    reference = resolve(reference)
    template = resolve(template)
    output_dir = resolve(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_fields, references = read_csv(reference)
    template_fields, template_rows = read_csv(template)
    workbook_rows = read_review_workbook(workbook)
    returned_rows = workbook_rows.get(sheet, [])
    output_rows, coverage_rows, remaining_rows, report = prefill_rows(
        references,
        template_rows,
        returned_rows,
    )
    if sheet not in workbook_rows:
        report["structural_errors"].append(f"returned workbook missing sheet: {sheet}")
        report["safe_to_use_as_single_reviewer_prefill"] = False

    output_fields = list(template_fields)
    for field in REVIEW_FIELDS + ("prefill_status", "prefill_issues"):
        if field not in output_fields:
            output_fields.append(field)
    coverage_fields = [
        "id",
        "task",
        "stratum",
        "category",
        "prefill_status",
        "prefill_issues",
        "primary_evidence_path",
        "page_path",
        "old_page_path",
        "new_page_path",
    ]
    reviewer_name = reviewer.strip().lower().replace(" ", "_")
    checklist_path = output_dir / f"{reviewer_name}_prefilled_checklist.csv"
    remaining_path = output_dir / f"{reviewer_name}_remaining_{len(remaining_rows)}_rows.csv"
    coverage_path = output_dir / "prefill_coverage.csv"
    write_csv(checklist_path, output_rows, output_fields)
    write_csv(remaining_path, remaining_rows, output_fields)
    write_csv(coverage_path, coverage_rows, coverage_fields)
    report.update(
        {
            "root": str(root),
            "workbook": str(workbook),
            "workbook_sheet": sheet,
            "reference": str(reference),
            "template": str(template),
            "reviewer": reviewer_name,
            "outputs": {
                "prefilled_checklist": str(checklist_path),
                "remaining_rows": str(remaining_path),
                "coverage": str(coverage_path),
            },
            "reference_fields": reference_fields,
        }
    )
    write_json(output_dir / "prefill_summary.json", report)
    (output_dir / "prefill_summary.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--reviewer", default="reviewer_a")
    parser.add_argument("--sheet", default=DEFAULT_SHEET)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    report = run_prefill(
        root=Path(args.root),
        workbook=Path(args.workbook),
        reference=Path(args.reference),
        template=Path(args.template),
        output_dir=Path(args.output_dir),
        reviewer=args.reviewer,
        sheet=args.sheet,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if args.strict and report["structural_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
