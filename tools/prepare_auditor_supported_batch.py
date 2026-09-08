#!/usr/bin/env python3
"""Stage balanced, audit-supported inputs with unchanged source split reservations."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from . import preview_reviewed_gold_promotion as preview
    from . import candidate_evidence_holds
    from .reconcile_auditor_active_links import p
except ImportError:
    import preview_reviewed_gold_promotion as preview
    import candidate_evidence_holds
    from reconcile_auditor_active_links import p


def select_rows(
    rows: list[dict],
    task: str,
    micro_categories: set[str],
    machine_evidence_holds: set[str] | None = None,
) -> tuple[list, list]:
    selected, holds = [], []
    seen = set()
    for row in rows:
        identity = preview.identity_for(row, task)
        if not identity or identity in seen:
            raise ValueError(f"missing or duplicate candidate identity: {identity}")
        seen.add(identity)
        reasons = []
        finals = preview.FINAL_MICROTEXT if task == "microtext" else preview.FINAL_VISUALDIFF
        if preview.status_for(row, task) not in finals:
            reasons.append("primary_review_not_final")
        if row.get("independent_audit_support", {}).get("decision_code") != "1":
            reasons.append("positive_independent_audit_missing")
        if row.get("safe_to_merge_gold") is not False:
            reasons.append("staging_safety_flag_missing")
        if candidate_evidence_holds.is_evidence_held(
            row, machine_evidence_holds or set()
        ):
            reasons.append("unresolved_machine_evidence_hold")
        if task == "microtext":
            category = row.get("corrected_category") or row.get("category")
            if category not in micro_categories:
                reasons.append("deferred_outside_priority_categories")
        else:
            description = preview.visualdiff_merge.description(row)
            if not description or description == preview.visualdiff_merge.TODO_DESCRIPTION:
                reasons.append("missing_visualdiff_description")
            elif preview.tentative_description_details(description):
                reasons.append("tentative_visualdiff_description")
            elif preview.visualdiff_description_requires_english_localization(description):
                reasons.append("visualdiff_description_requires_english_localization")
        if reasons:
            holds.append({"task": task, "identity": identity, "reasons": reasons, "row": row})
        else:
            selected.append(row)
    return selected, holds


def subset_reservations(rows: list[dict], records: dict) -> list[dict]:
    units = {preview.staged.staged_split_unit(row) for row in rows}
    missing = units - records.keys()
    if missing:
        raise ValueError(f"selected source units lack reservations: {sorted(missing)}")
    # Copy reservation records exactly, including their original IDs and splits.
    return [records[unit] for unit in sorted(units)]


def prepare(root: Path, reconciliation: Path, split_plan: Path, output: Path, categories: set[str]) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    report_path = reconciliation / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    before = preview.active_hashes(root)
    if report.get("status") != "PASS" or report["active_gold_hashes_after"] != p.active_gold_hashes(root):
        raise ValueError("reconciliation missing or stale")
    for name, expected in report["input_hashes"].items():
        if p.sha256_file(Path(name)) != expected:
            raise ValueError(f"reconciliation input changed: {name}")
    records, issues = preview.reservation_records(split_plan)
    if issues:
        raise ValueError(f"invalid source split plan: {issues}")
    machine_holds = candidate_evidence_holds.evidence_hold_ids(root)
    hold_pointer = root / candidate_evidence_holds.CURRENT_HOLDS
    selected, holds, input_hashes = {}, [], {str(report_path): p.sha256_file(report_path), str(split_plan): p.sha256_file(split_plan)}
    if hold_pointer.is_file():
        input_hashes[str(hold_pointer)] = p.sha256_file(hold_pointer)
    for task in ("microtext", "visualdiff"):
        name = f"supported_not_active_{task}.jsonl"
        path = reconciliation / name
        if p.sha256_file(path) != report["output_hashes"][name]:
            raise ValueError(f"reconciled output changed: {name}")
        input_hashes[str(path)] = p.sha256_file(path)
        selected[task], deferred = select_rows(
            preview.read_jsonl(path), task, categories, machine_holds
        )
        holds.extend(deferred)
    reservations = subset_reservations(selected["microtext"] + selected["visualdiff"], records)
    output.mkdir(parents=True)
    artifacts = []
    for task, rows in selected.items():
        name = f"{task}_reviewed.jsonl"
        preview.write_jsonl(output / name, rows)
        artifacts.append(name)
    preview.write_jsonl(output / "deferred_candidates.jsonl", holds)
    artifacts.append("deferred_candidates.jsonl")
    preview.write_json(output / "selected_split_reservations.json", {
        "valid": True, "mode": "exact_selected_source_unit_subset", "reservations": reservations,
        "source_plan": split_plan.relative_to(root).as_posix(), "source_plan_sha256": p.sha256_file(split_plan),
        "excluded_reservations": len(records) - len(reservations),
        "note": "No split was reassigned. Combined active split, source and leakage checks remain mandatory in strict preview.",
    })
    artifacts.append("selected_split_reservations.json")
    after = preview.active_hashes(root)
    if before != after:
        raise ValueError("active release changed during read-only preparation")
    result = {
        "goal": "Gold v2.0 Global", "status": "STAGED_PENDING_IMAGE_QA_AND_STRICT_PREVIEW",
        "selected": {task: len(rows) for task, rows in selected.items()}, "deferred": len(holds),
        "deferred_reasons": dict(Counter(reason for row in holds for reason in row["reasons"])),
        "priority_microtext_categories": sorted(categories), "split_reservations": len(reservations),
        "machine_evidence_holds_consulted": len(machine_holds),
        "active_gold_modified": False, "safe_to_merge_gold": False,
        "active_hashes_before": before, "active_hashes_after": after,
        "input_hashes": input_hashes, "output_hashes": {name: p.sha256_file(output / name) for name in artifacts},
    }
    preview.write_json(output / "report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--split-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--microtext-category", action="append", default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    result = prepare(root, root / args.reconciliation, root / args.split_plan, root / args.output_dir,
                     set(args.microtext_category or ["dimension_value"]))
    print(json.dumps({key: result[key] for key in ("status", "selected", "deferred", "deferred_reasons")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
