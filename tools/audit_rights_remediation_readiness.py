#!/usr/bin/env python3
"""Audit whether rights-blocked active rows have a safe review-only replacement path."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


VALID_TASKS = {"microtext", "visualdiff"}
VALID_SPLITS = {"train", "dev", "test"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_identity(row: dict[str, Any]) -> str:
    for key in ("pair_id", "candidate_id", "item_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def count_task_split(
    rows: list[dict[str, Any]],
    *,
    task_field: str,
    split_field: str,
) -> Counter[tuple[str, str]]:
    return Counter(
        (
            str(row.get(task_field) or "").strip().lower(),
            str(row.get(split_field) or "").strip().lower(),
        )
        for row in rows
    )


def format_counts(counts: Counter[tuple[str, str]]) -> dict[str, int]:
    return {f"{task}:{split}": count for (task, split), count in sorted(counts.items())}


def build_report(
    *,
    plan: dict[str, Any],
    affected_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    selected_path: Path,
    rights_report: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []

    affected_counts = count_task_split(
        affected_rows, task_field="task", split_field="split"
    )
    selected_counts = count_task_split(
        selected_rows,
        task_field="replacement_target_task",
        split_field="replacement_target_split",
    )
    invalid_affected = sorted(
        {
            f"{task}:{split}"
            for task, split in affected_counts
            if task not in VALID_TASKS or split not in VALID_SPLITS
        }
    )
    invalid_selected = sorted(
        {
            f"{task}:{split}"
            for task, split in selected_counts
            if task not in VALID_TASKS or split not in VALID_SPLITS
        }
    )
    if invalid_affected:
        issues.append(f"invalid affected task/split values: {', '.join(invalid_affected)}")
    if invalid_selected:
        issues.append(f"invalid selected task/split values: {', '.join(invalid_selected)}")
    if affected_counts != selected_counts:
        issues.append("selected task/split coverage does not exactly match affected active rows")

    affected_ids = [row_identity(row) for row in affected_rows]
    selected_ids = [row_identity(row) for row in selected_rows]
    if any(not value for value in affected_ids):
        issues.append("affected rows contain missing identities")
    if any(not value for value in selected_ids):
        issues.append("selected rows contain missing identities")
    if len(set(affected_ids)) != len(affected_ids):
        issues.append("affected rows contain duplicate identities")
    if len(set(selected_ids)) != len(selected_ids):
        issues.append("selected rows contain duplicate identities")

    unsafe_flags = sum(row.get("safe_to_merge_gold") is not False for row in selected_rows)
    if unsafe_flags:
        issues.append(f"{unsafe_flags} selected rows do not explicitly forbid pre-review merge")
    split_mismatches = sum(
        str(row.get("replacement_target_split") or "").strip().lower()
        != str(row.get("replacement_reserved_split") or "").strip().lower()
        for row in selected_rows
    )
    if split_mismatches:
        issues.append(f"{split_mismatches} selected rows disagree with reserved splits")
    non_review_rows = sum(
        str(row.get("replacement_plan_status") or "").strip().lower()
        != "awaiting_human_review"
        for row in selected_rows
    )
    if non_review_rows:
        issues.append(f"{non_review_rows} selected rows lack awaiting_human_review status")

    expected_affected = int(plan.get("active_rows_requiring_replacement") or 0)
    expected_selected = int(plan.get("review_only_replacement_rows_selected") or 0)
    if expected_affected != len(affected_rows):
        issues.append("plan affected-row count disagrees with affected JSONL")
    if expected_selected != len(selected_rows):
        issues.append("plan selected-row count disagrees with selected JSONL")
    if int(plan.get("residual_replacement_rows_needed") or 0) != 0:
        issues.append("replacement plan still has residual rows")
    if plan.get("issues"):
        issues.append("replacement plan reports issues")

    if not rights_report.get("valid"):
        issues.append("source-rights partition is invalid")
    if int(rights_report.get("input_rows") or 0) != len(selected_rows):
        issues.append("source-rights input count disagrees with selected JSONL")
    if int(rights_report.get("passing_rows") or 0) != len(selected_rows):
        issues.append("not every selected row passed the source-rights partition")
    if int(rights_report.get("held_rows") or 0):
        issues.append("source-rights partition contains held rows")

    contract_counts = contract.get("counts") or {}
    if int(contract_counts.get("rows") or 0) != len(selected_rows):
        issues.append("promotion contract row count disagrees with selected JSONL")
    if int(contract_counts.get("fatal_issue_rows") or 0):
        issues.append("promotion contract contains fatal rows")
    if not contract.get("structurally_ready_for_human_return_promotion"):
        issues.append("promotion contract is not structurally ready")
    if contract.get("active_gold_modified") is not False:
        issues.append("promotion contract does not confirm active Gold stayed untouched")

    selected_sha256 = sha256_file(selected_path)
    contract_hashes = {
        str(row.get("sha256") or "").strip().lower()
        for row in contract.get("cohorts") or []
    }
    if selected_sha256 not in contract_hashes:
        issues.append("selected JSONL hash is not bound by the promotion contract")

    human_actions = {
        str(key): int(value)
        for key, value in sorted((contract.get("human_actions") or {}).items())
    }
    human_decisions_remaining = sum(
        value for key, value in human_actions.items() if key.endswith(":decision_required")
    )
    if contract.get("human_review_complete"):
        warnings.append(
            "contract reports human review complete; strict return and retirement preview gates are still required"
        )

    machine_ready = not issues
    return {
        "goal": "Gold v2.0 Global",
        "blocked_active_source_documents": int(plan.get("blocked_active_source_docs") or 0),
        "active_rows_requiring_replacement": len(affected_rows),
        "review_only_replacements_selected": len(selected_rows),
        "affected_by_task_split": format_counts(affected_counts),
        "selected_by_task_split": format_counts(selected_counts),
        "paper_ready_replacement_documents": int(rights_report.get("paper_ready_documents") or 0),
        "human_actions": human_actions,
        "human_decisions_remaining": human_decisions_remaining,
        "selected_jsonl_sha256": selected_sha256,
        "machine_remediation_path_ready": machine_ready,
        "human_review_complete": bool(contract.get("human_review_complete")),
        "safe_to_retire_blocked_active_rows": False,
        "issues": issues,
        "warnings": warnings,
        "interpretation": (
            "A machine-ready remediation path proves that rights-cleared, split-compatible, review-only "
            "capacity exists. It never authorizes active-row retirement. Human acceptance plus duplicate, "
            "provenance, leakage, annotation, unified, strict-v2, and release-hash gates remain mandatory."
        ),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Rights Remediation Readiness",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Blocked active source documents: `{report['blocked_active_source_documents']}`",
        f"- Active rows requiring replacement: `{report['active_rows_requiring_replacement']}`",
        f"- Review-only replacements selected: `{report['review_only_replacements_selected']}`",
        f"- Paper-ready replacement documents: `{report['paper_ready_replacement_documents']}`",
        f"- Human decisions remaining: `{report['human_decisions_remaining']}`",
        f"- Machine remediation path ready: `{str(report['machine_remediation_path_ready']).lower()}`",
        f"- Safe to retire blocked active rows: `{str(report['safe_to_retire_blocked_active_rows']).lower()}`",
        "",
        "| Task and split | Active affected | Review-only selected |",
        "|---|---:|---:|",
    ]
    keys = sorted(set(report["affected_by_task_split"]) | set(report["selected_by_task_split"]))
    for key in keys:
        lines.append(
            f"| {key} | {report['affected_by_task_split'].get(key, 0)} | "
            f"{report['selected_by_task_split'].get(key, 0)} |"
        )
    lines.extend(["", report["interpretation"], ""])
    if report["issues"]:
        lines.extend(["## Issues", "", *[f"- {issue}" for issue in report["issues"]], ""])
    if report["warnings"]:
        lines.extend(["## Warnings", "", *[f"- {warning}" for warning in report["warnings"]], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a review-only remediation path for rights-blocked active rows"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--affected", required=True)
    parser.add_argument("--selected", required=True)
    parser.add_argument("--rights-report", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    selected_path = resolve(args.selected)
    report = build_report(
        plan=read_json(resolve(args.plan)),
        affected_rows=read_jsonl(resolve(args.affected)),
        selected_rows=read_jsonl(selected_path),
        selected_path=selected_path,
        rights_report=read_json(resolve(args.rights_report)),
        contract=read_json(resolve(args.contract)),
    )
    output_json = resolve(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_markdown(resolve(args.output_md), report)
    print(
        json.dumps(
            {
                "active_rows_requiring_replacement": report["active_rows_requiring_replacement"],
                "review_only_replacements_selected": report["review_only_replacements_selected"],
                "human_decisions_remaining": report["human_decisions_remaining"],
                "machine_remediation_path_ready": report["machine_remediation_path_ready"],
                "safe_to_retire_blocked_active_rows": report["safe_to_retire_blocked_active_rows"],
                "issues": report["issues"],
            },
            indent=2,
        )
    )
    return 0 if report["machine_remediation_path_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
