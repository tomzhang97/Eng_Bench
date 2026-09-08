#!/usr/bin/env python3
"""Partition VisualDiff family candidates into primary, backup, and excluded rows.

The partition is bound to a SHA-256 input hash and a human-readable machine
selection ledger. It is a review-planning operation only and never promotes
rows into Gold.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_v2_0_gate import visualdiff_family


CANONICAL_CHANGE_TYPES = {
    "addition",
    "deletion",
    "layout",
    "symbol",
    "text",
    "value",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pair_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or row.get("record_id") or "").strip()


def family_id(row: dict[str, Any]) -> str:
    identifier = pair_id(row)
    return str(row.get("visualdiff_family_id") or visualdiff_family(identifier)).strip()


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows
    )


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def partition_family_priority(
    root: Path,
    input_path: Path,
    ledger_path: Path,
    *,
    date_label: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    root = root.resolve()
    input_path = input_path if input_path.is_absolute() else root / input_path
    ledger_path = ledger_path if ledger_path.is_absolute() else root / ledger_path
    rows = read_jsonl(input_path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    input_sha256 = sha256_path(input_path)
    ledger_sha256 = sha256_path(ledger_path)
    issues: list[str] = []

    expected_sha256 = str(ledger.get("input_sha256") or "").strip().lower()
    if expected_sha256 != input_sha256:
        issues.append(
            f"input hash mismatch: ledger={expected_sha256 or 'missing'} actual={input_sha256}"
        )

    by_pair: dict[str, dict[str, Any]] = {}
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        identifier = pair_id(row)
        family = family_id(row)
        if not identifier:
            issues.append("input contains a row without a pair ID")
            continue
        if identifier in by_pair:
            issues.append(f"duplicate input pair ID: {identifier}")
            continue
        if not family:
            issues.append(f"input pair has no family identity: {identifier}")
            continue
        by_pair[identifier] = row
        by_family[family].append(row)

    selections = ledger.get("selections")
    exclusions = ledger.get("excluded_families")
    if not isinstance(selections, list):
        issues.append("ledger selections must be a list")
        selections = []
    if not isinstance(exclusions, list):
        issues.append("ledger excluded_families must be a list")
        exclusions = []

    selected_entries: dict[str, dict[str, Any]] = {}
    excluded_entries: dict[str, dict[str, Any]] = {}
    for entry in selections:
        family = str(entry.get("family_id") or "").strip() if isinstance(entry, dict) else ""
        if not family:
            issues.append("selection is missing family_id")
        elif family in selected_entries:
            issues.append(f"duplicate selected family: {family}")
        else:
            selected_entries[family] = entry
    for entry in exclusions:
        family = str(entry.get("family_id") or "").strip() if isinstance(entry, dict) else ""
        if not family:
            issues.append("excluded family is missing family_id")
        elif family in excluded_entries:
            issues.append(f"duplicate excluded family: {family}")
        else:
            excluded_entries[family] = entry

    overlap = set(selected_entries) & set(excluded_entries)
    if overlap:
        issues.append(f"families appear in selected and excluded partitions: {sorted(overlap)}")
    missing_families = set(by_family) - set(selected_entries) - set(excluded_entries)
    unknown_families = (set(selected_entries) | set(excluded_entries)) - set(by_family)
    if missing_families:
        issues.append(f"input families are not accounted for: {sorted(missing_families)}")
    if unknown_families:
        issues.append(f"ledger references unknown families: {sorted(unknown_families)}")

    expected_primary_families = int(ledger.get("target_primary_families") or 0)
    if expected_primary_families and len(selected_entries) != expected_primary_families:
        issues.append(
            f"selected {len(selected_entries)} families but ledger target is {expected_primary_families}"
        )

    primary_rows: list[dict[str, Any]] = []
    backup_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    selection_summaries: list[dict[str, Any]] = []
    used_pair_ids: set[str] = set()
    machine_proposal_rows = 0
    require_machine_proposals = bool(ledger.get("require_machine_proposals"))

    for family, entry in selected_entries.items():
        primary_identifier = str(entry.get("primary_pair_id") or "").strip()
        backup_identifiers = entry.get("backup_pair_ids")
        if not isinstance(backup_identifiers, list):
            issues.append(f"backup_pair_ids must be a list for family {family}")
            backup_identifiers = []
        backup_identifiers = [str(value).strip() for value in backup_identifiers if str(value).strip()]
        chosen_identifiers = [primary_identifier, *backup_identifiers]
        family_identifiers = {pair_id(row) for row in by_family.get(family, [])}
        if not primary_identifier:
            issues.append(f"selected family has no primary_pair_id: {family}")
        if len(chosen_identifiers) != len(set(chosen_identifiers)):
            issues.append(f"selected family repeats pair IDs: {family}")
        if set(chosen_identifiers) != family_identifiers:
            issues.append(
                f"selected family pair partition mismatch for {family}: "
                f"ledger={sorted(set(chosen_identifiers))} input={sorted(family_identifiers)}"
            )
        evidence_path = str(entry.get("evidence_path") or "").strip()
        evidence = root / evidence_path if evidence_path else None
        if not evidence_path or evidence is None or not evidence.is_file():
            issues.append(f"missing visual evidence for selected family {family}: {evidence_path or 'none'}")
        basis = str(entry.get("selection_basis") or "").strip()
        if not basis:
            issues.append(f"selected family has no selection_basis: {family}")
        proposed_description = str(entry.get("proposed_description") or "").strip()
        proposed_change_type = str(entry.get("proposed_change_type") or "").strip().lower()
        if require_machine_proposals and not proposed_description:
            issues.append(f"selected family has no proposed_description: {family}")
        if require_machine_proposals and proposed_change_type not in CANONICAL_CHANGE_TYPES:
            issues.append(
                f"selected family has invalid proposed_change_type {proposed_change_type!r}: {family}"
            )
        if proposed_change_type and proposed_change_type not in CANONICAL_CHANGE_TYPES:
            issues.append(
                f"selected family has invalid proposed_change_type {proposed_change_type!r}: {family}"
            )

        if primary_identifier in by_pair:
            primary = dict(by_pair[primary_identifier])
            primary.update(
                {
                    "family_priority_role": "primary",
                    "priority_activation_status": "review_now",
                    "machine_visual_qa_status": "selected_as_strongest_family_primary",
                    "machine_visual_qa_notes": basis,
                    "family_priority_backup_pair_ids": backup_identifiers,
                    "family_priority_evidence_path": evidence_path,
                    "family_priority_partition_date_label": date_label,
                    "safe_to_merge_gold": False,
                }
            )
            if proposed_description and proposed_change_type in CANONICAL_CHANGE_TYPES:
                primary.update(
                    {
                        "description": proposed_description,
                        "change_type": proposed_change_type,
                        "description_source": "machine_visual_qa_ledger_v1",
                        "machine_proposal_status": "ready_for_human_confirmation",
                    }
                )
                machine_proposal_rows += 1
            primary_rows.append(primary)
            used_pair_ids.add(primary_identifier)
        for identifier in backup_identifiers:
            if identifier not in by_pair:
                continue
            backup = dict(by_pair[identifier])
            backup.update(
                {
                    "family_priority_role": "backup",
                    "priority_activation_status": "review_only_if_primary_rejected",
                    "machine_visual_qa_status": "held_as_family_backup",
                    "machine_visual_qa_notes": (
                        f"Deferred while primary {primary_identifier} is reviewed. {basis}"
                    ),
                    "family_priority_primary_pair_id": primary_identifier,
                    "family_priority_evidence_path": evidence_path,
                    "family_priority_partition_date_label": date_label,
                    "safe_to_merge_gold": False,
                }
            )
            backup_rows.append(backup)
            used_pair_ids.add(identifier)
        selection_summaries.append(
            {
                "family_id": family,
                "primary_pair_id": primary_identifier,
                "backup_pair_ids": backup_identifiers,
                "evidence_path": evidence_path,
                "selection_basis": basis,
                "proposed_change_type": proposed_change_type,
                "proposed_description": proposed_description,
            }
        )

    exclusion_summaries: list[dict[str, Any]] = []
    for family, entry in excluded_entries.items():
        reason = str(entry.get("reason") or "").strip()
        if not reason:
            issues.append(f"excluded family has no reason: {family}")
        for row in by_family.get(family, []):
            identifier = pair_id(row)
            excluded = dict(row)
            excluded.update(
                {
                    "family_priority_role": "excluded_reserve",
                    "priority_activation_status": "not_in_current_gate_shortest_path",
                    "machine_visual_qa_status": "held_outside_primary_family_set",
                    "machine_visual_qa_notes": reason,
                    "family_priority_partition_date_label": date_label,
                    "safe_to_merge_gold": False,
                }
            )
            excluded_rows.append(excluded)
            used_pair_ids.add(identifier)
        exclusion_summaries.append({"family_id": family, "reason": reason})

    all_pair_ids = set(by_pair)
    if used_pair_ids != all_pair_ids:
        issues.append(
            f"row partition is incomplete: missing={sorted(all_pair_ids - used_pair_ids)} "
            f"extra={sorted(used_pair_ids - all_pair_ids)}"
        )

    primary_rows.sort(key=lambda row: (int(row.get("family_priority_rank") or 0), pair_id(row)))
    backup_rows.sort(key=lambda row: (int(row.get("family_priority_rank") or 0), pair_id(row)))
    excluded_rows.sort(key=lambda row: (family_id(row), pair_id(row)))
    active_families_before = int(ledger.get("active_gold_families_before") or 0)
    two_per_family_baseline = len(primary_rows) * 2
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "machine_visual_family_primary_backup_partition",
        "valid": not issues,
        "input_path": relative_or_absolute(root, input_path),
        "input_sha256": input_sha256,
        "ledger_path": relative_or_absolute(root, ledger_path),
        "ledger_sha256": ledger_sha256,
        "input_rows": len(rows),
        "input_families": len(by_family),
        "primary_rows": len(primary_rows),
        "primary_families": len(selected_entries),
        "primary_rows_with_machine_proposals": machine_proposal_rows,
        "backup_rows_deferred": len(backup_rows),
        "excluded_reserve_rows": len(excluded_rows),
        "excluded_reserve_families": len(excluded_entries),
        "two_rows_per_selected_family_baseline": two_per_family_baseline,
        "immediate_review_actions_avoided_vs_two_per_family": max(
            0, two_per_family_baseline - len(primary_rows)
        ),
        "total_rows_deferred_or_excluded": len(backup_rows) + len(excluded_rows),
        "active_gold_families_before": active_families_before,
        "projected_family_upper_bound_if_all_primaries_are_accepted": (
            active_families_before + len(primary_rows)
        ),
        "target_family_gate": int(ledger.get("target_family_gate") or 30),
        "selection_policy": (
            "Review one visually strongest localized row per missing family. Activate its bound "
            "backup only if the primary is rejected. Excluded families remain reserve capacity."
        ),
        "selections": selection_summaries,
        "excluded_families": exclusion_summaries,
        "issues": issues,
        "active_gold_modified": False,
        "output_sha256": {
            "primary": hashlib.sha256(jsonl_bytes(primary_rows)).hexdigest(),
            "backup": hashlib.sha256(jsonl_bytes(backup_rows)).hexdigest(),
            "excluded": hashlib.sha256(jsonl_bytes(excluded_rows)).hexdigest(),
        },
    }
    return primary_rows, backup_rows, excluded_rows, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# VisualDiff Family Primary/Backup Partition",
        "",
        f"- Goal: `{report['goal']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input: `{report['input_path']}`",
        f"- Input SHA-256: `{report['input_sha256']}`",
        f"- Primary review rows now: `{report['primary_rows']}`",
        f"- Deferred family backups: `{report['backup_rows_deferred']}`",
        f"- Excluded reserve rows: `{report['excluded_reserve_rows']}`",
        f"- Immediate actions avoided vs two rows/family: `{report['immediate_review_actions_avoided_vs_two_per_family']}`",
        f"- Projected family upper bound: `{report['projected_family_upper_bound_if_all_primaries_are_accepted']}/{report['target_family_gate']}`",
        "- Active Gold modified: `false`",
        "",
        f"- Primaries with machine-written proposals: `{report['primary_rows_with_machine_proposals']}`",
        "",
        "| Family | Primary | Type | Machine proposal | Backup |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["selections"]:
        backups = ", ".join(f"`{value}`" for value in row["backup_pair_ids"])
        lines.append(
            f"| `{row['family_id']}` | `{row['primary_pair_id']}` | "
            f"`{row['proposed_change_type']}` | {row['proposed_description']} | {backups} |"
        )
    lines.extend(["", "## Excluded Reserve", ""])
    for row in report["excluded_families"]:
        lines.append(f"- `{row['family_id']}`: {row['reason']}")
    if report["issues"]:
        lines.extend(["", "## Issues", "", *[f"- {issue}" for issue in report["issues"]]])
    lines.append("")
    return "\n".join(lines)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jsonl_bytes(rows))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--primary-output", type=Path, required=True)
    parser.add_argument("--backup-output", type=Path, required=True)
    parser.add_argument("--excluded-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    primary, backup, excluded, report = partition_family_priority(
        root, args.input, args.ledger, date_label=args.date_label
    )
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    if report["valid"]:
        for value, rows in (
            (args.primary_output, primary),
            (args.backup_output, backup),
            (args.excluded_output, excluded),
        ):
            path = value if value.is_absolute() else root / value
            write_jsonl(path, rows)
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "valid": report["valid"],
                "primary_rows": report["primary_rows"],
                "backup_rows_deferred": report["backup_rows_deferred"],
                "excluded_reserve_rows": report["excluded_reserve_rows"],
                "immediate_review_actions_avoided": report[
                    "immediate_review_actions_avoided_vs_two_per_family"
                ],
                "projected_family_upper_bound": report[
                    "projected_family_upper_bound_if_all_primaries_are_accepted"
                ],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
