#!/usr/bin/env python3
"""Build a fail-closed ledger explaining every remaining human-owned row."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ALLOWED_REASONS = {
    "evaluation_truth",
    "unresolved_visualdiff_semantics",
    "semantic_engineering_category",
    "ambiguous_or_clipped_evidence",
    "independent_calibration",
    "independent_agreement",
    "policy_exception",
}
SEMANTIC_CATEGORIES = {
    "room_label",
    "process_label",
    "equipment_tag",
    "instrument_tag",
    "pipe_line_tag",
    "unknown_microtext",
}
EVIDENCE_REASONS = {
    "independent_ocr_confidence_below_threshold",
    "independent_ocr_text_mismatch",
    "non_deterministic_extraction_source",
    "source_text_fields_do_not_exactly_agree",
    "category_text_pattern_not_deterministic",
}
REASON_PRIORITY = (
    "evaluation_truth",
    "unresolved_visualdiff_semantics",
    "semantic_engineering_category",
    "ambiguous_or_clipped_evidence",
    "policy_exception",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    path = path.resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def row_identity(row: dict[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or row.get("pair_id")
        or row.get("record_id")
        or row.get("id")
        or ""
    ).strip()


def infer_task(row: dict[str, Any]) -> str:
    task = str(row.get("task") or row.get("replacement_for_task") or "").strip().lower()
    if task:
        return task
    if row.get("image_old") or row.get("image_new") or row.get("pair_id"):
        return "visualdiff"
    return "microtext"


def human_assignment_reasons(row: dict[str, Any]) -> list[str]:
    machine_reasons = {
        str(value).strip().lower()
        for value in row.get("machine_certification_reasons") or []
        if str(value).strip()
    }
    split = str(row.get("reserved_split") or row.get("split") or "").strip().lower()
    category = str(row.get("category") or row.get("proposed_category") or "").strip().lower()
    task = infer_task(row)
    reasons: set[str] = set()
    if split in {"dev", "test"} or "evaluation_split_requires_human_review" in machine_reasons:
        reasons.add("evaluation_truth")
    if task == "visualdiff" or "visualdiff_requires_human_review" in machine_reasons:
        reasons.add("unresolved_visualdiff_semantics")
    if category in SEMANTIC_CATEGORIES or "semantic_category_requires_human_review" in machine_reasons:
        reasons.add("semantic_engineering_category")
    if machine_reasons & EVIDENCE_REASONS:
        reasons.add("ambiguous_or_clipped_evidence")
    if "pin_label_matches_source_revision" in machine_reasons:
        reasons.add("policy_exception")
    if not reasons:
        reasons.add("policy_exception")
    return [reason for reason in REASON_PRIORITY if reason in reasons]


def build_report(
    *,
    root: Path,
    human_required_path: Path,
    recovered_path: Path,
    provenance_candidates_path: Path,
    responsibility_report_path: Path,
    date_label: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    human_required_path = resolve(root, human_required_path).resolve()
    recovered_path = resolve(root, recovered_path).resolve()
    provenance_candidates_path = resolve(root, provenance_candidates_path).resolve()
    responsibility_report_path = resolve(root, responsibility_report_path).resolve()
    human_rows = read_jsonl(human_required_path)
    recovered_rows = read_jsonl(recovered_path)
    provenance_rows = read_jsonl(provenance_candidates_path)
    responsibility = read_json(responsibility_report_path)
    issues: list[str] = []

    raw_ids = [row_identity(row) for row in human_rows]
    if any(not value for value in raw_ids):
        issues.append("human_required_rows_missing_identity")
    duplicate_ids = sorted(value for value, count in Counter(raw_ids).items() if value and count > 1)
    if duplicate_ids:
        issues.append(f"duplicate_human_required_identities:{len(duplicate_ids)}")
    recovered_ids = {row_identity(row) for row in recovered_rows if row_identity(row)}
    raw_by_id = {row_identity(row): row for row in human_rows if row_identity(row)}
    missing_recovered = sorted(recovered_ids - set(raw_by_id))
    if missing_recovered:
        issues.append(f"recovered_rows_not_in_human_required:{len(missing_recovered)}")

    provenance_ids = {row_identity(row) for row in provenance_rows if row_identity(row)}
    current_ids = set(raw_by_id) - recovered_ids
    missing_provenance = sorted(provenance_ids - current_ids)
    if missing_provenance:
        issues.append(f"provenance_candidates_not_in_current_human_ledger:{len(missing_provenance)}")

    ledger: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    by_origin_task_split: Counter[str] = Counter()
    for identity in sorted(current_ids):
        row = raw_by_id[identity]
        reasons = human_assignment_reasons(row)
        invalid = sorted(set(reasons) - ALLOWED_REASONS)
        if invalid:
            issues.append(f"invalid_human_assignment_reason:{identity}:{','.join(invalid)}")
        if not reasons:
            issues.append(f"unjustified_human_assignment:{identity}")
            continue
        task = infer_task(row)
        split = str(row.get("reserved_split") or row.get("split") or "missing").strip().lower()
        origin = str(row.get("machine_certification_origin_cohort") or "missing").strip().lower()
        category = str(row.get("category") or row.get("proposed_category") or "missing").strip().lower()
        reason_counts.update(reasons)
        primary_counts[reasons[0]] += 1
        by_origin_task_split[f"{origin}|{task}|{split}"] += 1
        ledger.append(
            {
                "identity": identity,
                "origin": origin,
                "task": task,
                "split": split,
                "category": category,
                "primary_reason": reasons[0],
                "human_assignment_reasons": reasons,
                "provenance_replacement": identity in provenance_ids,
                "machine_preflight_status": "eligibility_preflight_complete",
                "safe_to_merge_gold": False,
            }
        )

    provenance_ledger = [row for row in ledger if row["provenance_replacement"]]
    provenance_without_evaluation_reason = [
        row["identity"]
        for row in provenance_ledger
        if "evaluation_truth" not in row["human_assignment_reasons"]
    ]
    if provenance_without_evaluation_reason:
        issues.append(
            "provenance_replacements_missing_evaluation_truth_reason:"
            f"{len(provenance_without_evaluation_reason)}"
        )

    human_owned = responsibility.get("human_owned") or {}
    expected_current = int(human_owned.get("human_required_rows") or 0)
    if expected_current != len(ledger):
        issues.append(f"responsibility_human_row_count_mismatch:{expected_current}:{len(ledger)}")
    calibration_actions = int(
        (responsibility.get("machine_owned") or {}).get("calibration_rows_required_once") or 0
    )
    agreement_actions = int(human_owned.get("formal_agreement_reviewer_actions_remaining") or 0)
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "schema": "eng_bench_human_assignment_justification_v1",
        "status": "PASS" if not issues else "FAIL",
        "valid": not issues,
        "active_gold_modified": False,
        "counts": {
            "raw_post_eligibility_human_rows": len(human_rows),
            "machine_recovered_rows_removed": len(recovered_ids),
            "current_human_owned_benchmark_rows": len(ledger),
            "provenance_replacement_rows": len(provenance_ledger),
            "unjustified_human_rows": sum(
                1 for row in ledger if not row["human_assignment_reasons"]
            ),
            "independent_calibration_actions": calibration_actions,
            "independent_agreement_actions": agreement_actions,
        },
        "primary_reason_counts": dict(sorted(primary_counts.items())),
        "all_reason_counts": dict(sorted(reason_counts.items())),
        "by_origin_task_split": dict(sorted(by_origin_task_split.items())),
        "inputs": {
            "human_required": display(root, human_required_path),
            "human_required_sha256": file_sha256(human_required_path),
            "recovered": display(root, recovered_path),
            "recovered_sha256": file_sha256(recovered_path),
            "provenance_candidates": display(root, provenance_candidates_path),
            "provenance_candidates_sha256": file_sha256(provenance_candidates_path),
            "responsibility_report": display(root, responsibility_report_path),
            "responsibility_report_sha256": file_sha256(responsibility_report_path),
        },
        "issues": issues,
        "interpretation": (
            "This ledger proves why each remaining benchmark row still needs a person after machine "
            "eligibility preflight. It does not certify, review, or promote any row. Calibration and "
            "agreement actions are reported separately because they are statistical release controls."
        ),
    }
    return report, ledger


def materialize_human_owned_cohorts(
    human_rows: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Attach the audited human rationale to full rows and partition by origin."""
    issues: list[str] = []
    ledger_by_id = {str(row.get("identity") or ""): row for row in ledger}
    human_by_id = {
        row_identity(row): row for row in human_rows if row_identity(row) in ledger_by_id
    }
    missing = sorted(set(ledger_by_id) - set(human_by_id))
    if missing:
        issues.append(f"ledger_rows_missing_full_human_payload:{len(missing)}")
    cohorts: dict[str, list[dict[str, Any]]] = {"current": [], "future": []}
    for identity in sorted(set(ledger_by_id) & set(human_by_id)):
        ledger_row = ledger_by_id[identity]
        origin = str(ledger_row.get("origin") or "").strip().lower()
        if origin not in cohorts:
            issues.append(f"invalid_human_assignment_origin:{identity}:{origin or 'missing'}")
            continue
        output = dict(human_by_id[identity])
        output.update(
            {
                "human_assignment_primary_reason": ledger_row["primary_reason"],
                "human_assignment_reasons": list(ledger_row["human_assignment_reasons"]),
                "human_assignment_origin": origin,
                "human_assignment_preflight_status": ledger_row[
                    "machine_preflight_status"
                ],
                "provenance_replacement": bool(
                    ledger_row.get("provenance_replacement")
                ),
                "safe_to_merge_gold": False,
            }
        )
        cohorts[origin].append(output)
    return cohorts, issues


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Human Assignment Justification Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Status: `{report['status']}`",
        f"- Current human-owned benchmark rows: `{counts['current_human_owned_benchmark_rows']}`",
        f"- Machine-recovered rows removed: `{counts['machine_recovered_rows_removed']}`",
        f"- Provenance replacement rows: `{counts['provenance_replacement_rows']}`",
        f"- Unjustified human rows: `{counts['unjustified_human_rows']}`",
        f"- Independent calibration actions: `{counts['independent_calibration_actions']}`",
        f"- Independent agreement actions: `{counts['independent_agreement_actions']}`",
        "",
        "## Primary Reasons",
        "",
    ]
    lines.extend(
        f"- `{reason}`: {count}" for reason, count in report["primary_reason_counts"].items()
    )
    lines.extend(["", "## Issues", ""])
    lines.extend(f"- `{issue}`" for issue in report["issues"])
    if not report["issues"]:
        lines.append("- None.")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--human-required", type=Path, required=True)
    parser.add_argument("--recovered", type=Path, required=True)
    parser.add_argument("--provenance-candidates", type=Path, required=True)
    parser.add_argument("--responsibility-report", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-current-cohort", type=Path)
    parser.add_argument("--output-future-cohort", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report, ledger = build_report(
        root=root,
        human_required_path=args.human_required,
        recovered_path=args.recovered,
        provenance_candidates_path=args.provenance_candidates,
        responsibility_report_path=args.responsibility_report,
        date_label=args.date_label,
    )
    if bool(args.output_current_cohort) != bool(args.output_future_cohort):
        parser.error(
            "--output-current-cohort and --output-future-cohort must be supplied together"
        )
    if args.output_current_cohort and args.output_future_cohort:
        cohorts, materialization_issues = materialize_human_owned_cohorts(
            read_jsonl(resolve(root, args.human_required)), ledger
        )
        if materialization_issues:
            report["issues"].extend(materialization_issues)
            report["issues"] = sorted(set(report["issues"]))
            report["status"] = "FAIL"
            report["valid"] = False
        current_path = resolve(root, args.output_current_cohort)
        future_path = resolve(root, args.output_future_cohort)
        write_jsonl(current_path, cohorts["current"])
        write_jsonl(future_path, cohorts["future"])
        report["cohort_outputs"] = {
            "current": {
                "path": display(root, current_path),
                "rows": len(cohorts["current"]),
                "sha256": file_sha256(current_path),
            },
            "future": {
                "path": display(root, future_path),
                "rows": len(cohorts["future"]),
                "sha256": file_sha256(future_path),
            },
        }
    output_json = resolve(root, args.output_json)
    output_jsonl = resolve(root, args.output_jsonl)
    output_md = resolve(root, args.output_md)
    write_json(output_json, report)
    write_jsonl(output_jsonl, ledger)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "counts": report["counts"],
                "primary_reason_counts": report["primary_reason_counts"],
                "cohort_outputs": report.get("cohort_outputs", {}),
                "issues": report["issues"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
