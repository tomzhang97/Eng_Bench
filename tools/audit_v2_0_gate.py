#!/usr/bin/env python3
"""Audit Eng_Bench against the v2.0 Global release gate."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from agreement_audit import agreement_release_gate
from audit_active_gold_provenance import build_report as build_provenance_report
from audit_active_gold_provenance import file_sha256
from audit_active_gold_provenance import paper_ready_provenance_gate
from audit_source_payload_duplicates import build_report as build_source_payload_duplicate_report
from evaluation_registry import baseline_registry, submission_registry
from source_rights import is_release_safe_status
from visualdiff_description_finality import visualdiff_description_finality
from reconcile_auditor_active_links import release_constraint as auditor_return_release_constraint


V2_0_TARGETS = {
    "rows_min": 25000,
    "rows_max": 50000,
    "source_docs": 150,
    "visualdiff_revision_families": 30,
    "hidden_public_test_examples": 5000,
    "baselines_or_external_submissions": 20,
}

# Gold v2.0 inherits the category floors and pin-label cap established for the
# first defensible Gold release. Scale alone must not erase task diversity.
MICROTEXT_CATEGORY_MINIMUMS = {
    "pin_label": 1000,
    "component_value": 300,
    "dimension_value": 900,
    "equipment_tag": 300,
    "instrument_tag": 300,
    "pipe_line_tag": 150,
    "process_label": 150,
    "process_value": 150,
    "room_label": 150,
    "tolerance_value": 150,
}
MICROTEXT_PIN_SHARE_MAX = 0.45

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def source_doc_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("source_doc_id") or metadata.get("doc_id") or row.get("doc_id") or "")


def visualdiff_family(pair_id: str) -> str:
    parts = pair_id.split("__")
    if len(parts) >= 3:
        return "__".join(parts[:3])
    return pair_id.rsplit("__", 1)[0]


def is_release_safe_active(row: dict[str, str]) -> bool:
    if row.get("task") == "reference":
        return False
    status = str(row.get("public_status") or row.get("rights_tier") or "").lower()
    return is_release_safe_status(status)


def baseline_or_submission_names(
    root: Path,
    baselines: dict[str, list[dict[str, Any]]] | None = None,
    submissions: dict[str, list[dict[str, Any]]] | None = None,
) -> list[str]:
    names = set()
    for item in (baselines or baseline_registry(root))["counted"]:
        names.add(f"baseline:{item['name']}")
    for item in (submissions or submission_registry(root))["counted"]:
        names.add(f"submission:{item['name']}")
    return sorted(names)


def baseline_or_submission_count(root: Path) -> int:
    return len(baseline_or_submission_names(root))


def count_jsonl(path: Path) -> int:
    return len(read_jsonl(path))


def microtext_category_balance(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("category") or "unknown").strip() or "unknown" for row in items)
    total = len(items)
    pin_labels = counts.get("pin_label", 0)
    pin_share = pin_labels / total if total else 0.0
    shortfalls = {
        category: max(0, minimum - counts.get(category, 0))
        for category, minimum in MICROTEXT_CATEGORY_MINIMUMS.items()
    }
    return {
        "current": (
            f"pin share {pin_share:.2%}; "
            f"{sum(value > 0 for value in shortfalls.values())} category floors open"
        ),
        "target": "pin_label <=45% and inherited Gold category minimums",
        "passes": bool(total) and pin_share <= MICROTEXT_PIN_SHARE_MAX and not any(shortfalls.values()),
        "total_rows": total,
        "category_counts": dict(sorted(counts.items())),
        "category_minimums": dict(MICROTEXT_CATEGORY_MINIMUMS),
        "category_shortfalls": shortfalls,
        "pin_label_rows": pin_labels,
        "pin_label_share": round(pin_share, 6),
        "pin_label_share_max": MICROTEXT_PIN_SHARE_MAX,
    }


def unique_release_safe_inventory_status(
    inventory: list[dict[str, str]],
    duplicate_report: dict[str, Any],
) -> dict[str, Any]:
    release_safe_by_doc = {
        str(row.get("doc_id") or "").strip(): row
        for row in inventory
        if str(row.get("doc_id") or "").strip() and is_release_safe_active(row)
    }
    aliases = set(duplicate_report.get("alias_doc_ids") or []) & set(release_safe_by_doc)
    return {
        "current": len(release_safe_by_doc) - len(aliases),
        "raw_current": len(release_safe_by_doc),
        "duplicate_alias_docs": len(aliases),
        "duplicate_alias_doc_ids": sorted(aliases),
    }


def unique_active_source_status(provenance: dict[str, Any]) -> dict[str, Any]:
    all_documents_by_id = {
        str(row.get("doc_id") or "").strip(): row
        for row in provenance.get("documents", [])
        if str(row.get("doc_id") or "").strip()
    }
    documents_by_id = {
        doc_id: row
        for doc_id, row in all_documents_by_id.items()
        if bool(row.get("paper_ready"))
    }
    payload_groups: dict[str, list[str]] = {}
    for doc_id, row in documents_by_id.items():
        computed_sha256 = str(row.get("computed_sha256") or "").strip().lower()
        recorded_sha256 = str(row.get("recorded_sha256") or "").strip().lower()
        sha256 = computed_sha256 if len(computed_sha256) == 64 else recorded_sha256
        key = f"sha256:{sha256}" if len(sha256) == 64 else f"doc_id:{doc_id}"
        payload_groups.setdefault(key, []).append(doc_id)

    duplicate_groups = []
    duplicate_alias_doc_ids = []
    for key, doc_ids in sorted(payload_groups.items()):
        if len(doc_ids) < 2:
            continue
        ordered_ids = sorted(doc_ids)
        aliases = ordered_ids[1:]
        duplicate_alias_doc_ids.extend(aliases)
        duplicate_groups.append(
            {
                "payload_key": key,
                "canonical_doc_id": ordered_ids[0],
                "alias_doc_ids": aliases,
                "doc_ids": ordered_ids,
            }
        )

    return {
        "current": len(payload_groups),
        "raw_current": len(documents_by_id),
        "raw_active_docs": len(all_documents_by_id),
        "excluded_not_paper_ready_docs": len(all_documents_by_id) - len(documents_by_id),
        "excluded_not_paper_ready_doc_ids": sorted(
            set(all_documents_by_id) - set(documents_by_id)
        ),
        "duplicate_alias_docs": len(duplicate_alias_doc_ids),
        "duplicate_alias_doc_ids": sorted(duplicate_alias_doc_ids),
        "duplicate_payload_groups": duplicate_groups,
    }


def machine_certification_status(
    root: Path,
    eligibility_report: Path | None,
    calibration_reuse_report: Path | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "status": "not_supplied",
        "auto_eligible_pending_calibration": 0,
        "calibration_rows": 0,
        "calibration_rows_completed_reused": 0,
        "calibration_rows_remaining": 0,
        "net_row_by_row_human_decisions_avoided": 0,
        "active_gold_modified": False,
        "reuse_evidence_status": "not_supplied",
    }
    if eligibility_report is None:
        return status

    report_path = (
        eligibility_report
        if eligibility_report.is_absolute()
        else root / eligibility_report
    )
    if not report_path.is_file():
        raise FileNotFoundError(f"machine certification report not found: {report_path}")
    machine_report = json.loads(report_path.read_text(encoding="utf-8"))
    counts = machine_report.get("counts") or {}
    sample = machine_report.get("calibration_sample") or {}
    eligible = int(counts.get("auto_eligible_pending_calibration") or 0)
    pending_nonpin = int(counts.get("auto_eligible_nonpin") or 0)
    pending_pin = int(counts.get("auto_eligible_deferred_pin") or 0)
    calibration_rows = int(sample.get("rows") or 0)
    completed_reused = 0
    reuse_status = "not_supplied"
    reuse_details: dict[str, Any] = {}

    if calibration_reuse_report is not None:
        reuse_path = (
            calibration_reuse_report
            if calibration_reuse_report.is_absolute()
            else root / calibration_reuse_report
        )
        if not reuse_path.is_file():
            raise FileNotFoundError(f"machine calibration reuse report not found: {reuse_path}")
        reuse = json.loads(reuse_path.read_text(encoding="utf-8"))
        reuse_counts = reuse.get("counts") or {}
        reused = int(reuse_counts.get("reused_correct_rows") or 0)
        remaining = int(reuse_counts.get("remaining_rows") or 0)
        reuse_total = int(reuse_counts.get("calibration_rows") or 0)
        conflicts = int(reuse_counts.get("conflict_rows") or 0)
        original_eligible = int(reuse_counts.get("eligible_rows_original") or 0)
        active_eligible = int(reuse_counts.get("eligible_rows_already_active") or 0)
        current_pending_eligible = int(reuse_counts.get("eligible_rows_current_pending") or 0)
        current_pending_nonpin = int(
            reuse_counts.get("eligible_rows_current_pending_nonpin") or 0
        )
        current_pending_pin = int(
            reuse_counts.get("eligible_rows_current_pending_pin") or 0
        )
        reuse_eligibility_sha = str(
            (reuse.get("cohort") or {}).get("eligibility_report_sha256") or ""
        ).lower()
        eligibility_sha = file_sha256(report_path).lower()
        valid_reuse = (
            reuse_total == calibration_rows
            and reused >= 0
            and remaining >= 0
            and reused + remaining == calibration_rows
            and conflicts == 0
            and not bool(reuse.get("active_gold_modified"))
            and reuse_eligibility_sha == eligibility_sha
            and original_eligible == eligible
            and active_eligible >= 0
            and current_pending_eligible >= 0
            and active_eligible + current_pending_eligible == original_eligible
            and current_pending_nonpin >= 0
            and current_pending_pin >= 0
            and current_pending_nonpin + current_pending_pin == current_pending_eligible
        )
        reuse_status = "valid" if valid_reuse else "invalid"
        if valid_reuse:
            completed_reused = reused
            eligible = current_pending_eligible
            pending_nonpin = current_pending_nonpin
            pending_pin = current_pending_pin
        reuse_details = {
            "reuse_report_path": reuse_path.resolve().relative_to(root.resolve()).as_posix(),
            "reuse_report_sha256": file_sha256(reuse_path),
            "reuse_conflict_rows": conflicts,
            "reuse_reported_remaining_rows": remaining,
            "eligible_rows_already_active": active_eligible,
        }

    remaining_rows = max(0, calibration_rows - completed_reused)
    status = {
        "status": "pending_calibration" if eligible and remaining_rows else (
            "calibration_complete_pending_finalization" if eligible else "no_auto_eligible_rows"
        ),
        "report_path": report_path.resolve().relative_to(root.resolve()).as_posix(),
        "report_sha256": file_sha256(report_path),
        "auto_eligible_pending_calibration": eligible,
        "auto_eligible_balance_closing_nonpin": pending_nonpin,
        "auto_eligible_deferred_pin": pending_pin,
        "human_required": int(counts.get("human_required") or 0),
        "reject_or_hold": int(counts.get("reject_or_hold") or 0),
        "calibration_rows": calibration_rows,
        "calibration_rows_completed_reused": completed_reused,
        "calibration_rows_remaining": remaining_rows,
        "net_row_by_row_human_decisions_avoided": max(0, eligible - remaining_rows),
        "active_gold_modified": bool(machine_report.get("active_gold_modified")),
        "reuse_evidence_status": reuse_status,
        **reuse_details,
    }
    return status


def collect_status(
    root: Path,
    machine_certification_report: Path | None = None,
    machine_responsibility_report: Path | None = None,
    machine_calibration_reuse_report: Path | None = None,
) -> dict[str, Any]:
    unified = read_jsonl(root / "eng_bench.jsonl")
    pairs = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    microtext_items = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    inventory = read_csv(root / "SOURCE_INVENTORY.csv")
    duplicate_report = build_source_payload_duplicate_report(root)
    provenance = build_provenance_report(root)
    active_source_status = unique_active_source_status(provenance)
    visualdiff_families = {
        visualdiff_family(str(row.get("id") or row.get("pair_id") or ""))
        for row in pairs
        if row.get("id") or row.get("pair_id")
    }
    release_safe_status = unique_release_safe_inventory_status(inventory, duplicate_report)
    hidden_public_test_rows = (
        count_jsonl(root / "release" / "public_inputs" / "eng_bench_public_test_inputs.jsonl")
        + count_jsonl(root / "release" / "public_inputs" / "eng_bench_hidden_test_inputs.jsonl")
    )
    infrastructure = {
        "make_public_private_split_tool": (root / "tools" / "make_public_private_split.py").exists(),
        "submission_validator": (root / "tools" / "validate_submission_format.py").exists(),
        "leaderboard_registration_tool": (
            root / "tools" / "register_leaderboard_submission.py"
        ).exists(),
        "leaderboard_protocol_doc": (root / "docs" / "LEADERBOARD_PROTOCOL.md").exists(),
        "leaderboard_dir": (root / "leaderboard").exists(),
        "hidden_test_inputs": (root / "release" / "public_inputs" / "eng_bench_hidden_test_inputs.jsonl").exists(),
        "hidden_private_labels": (root / "release" / "private_labels" / "eng_bench_hidden_test_labels.jsonl").exists(),
    }
    registry = baseline_registry(root)
    submissions = submission_registry(root)
    counted_baselines_or_submissions = baseline_or_submission_names(root, registry, submissions)
    gates = {
        "total_rows": {
            "current": len(unified),
            "target": f"{V2_0_TARGETS['rows_min']}-{V2_0_TARGETS['rows_max']}",
            "passes": V2_0_TARGETS["rows_min"] <= len(unified) <= V2_0_TARGETS["rows_max"],
        },
        "gold_source_docs": {
            **active_source_status,
            "target": V2_0_TARGETS["source_docs"],
            "passes": active_source_status["current"] >= V2_0_TARGETS["source_docs"],
        },
        "release_safe_inventory_docs": {
            **release_safe_status,
            "target": V2_0_TARGETS["source_docs"],
            "passes": release_safe_status["current"] >= V2_0_TARGETS["source_docs"],
        },
        "visualdiff_revision_families": {
            "current": len(visualdiff_families),
            "target": V2_0_TARGETS["visualdiff_revision_families"],
            "passes": len(visualdiff_families) >= V2_0_TARGETS["visualdiff_revision_families"],
        },
        "hidden_public_test_examples": {
            "current": hidden_public_test_rows,
            "target": V2_0_TARGETS["hidden_public_test_examples"],
            "passes": hidden_public_test_rows >= V2_0_TARGETS["hidden_public_test_examples"],
        },
        "baselines_or_external_submissions": {
            "current": len(counted_baselines_or_submissions),
            "target": V2_0_TARGETS["baselines_or_external_submissions"],
            "passes": len(counted_baselines_or_submissions)
            >= V2_0_TARGETS["baselines_or_external_submissions"],
        },
        "leaderboard_infrastructure": {
            "current": sum(1 for passed in infrastructure.values() if passed),
            "target": len(infrastructure),
            "passes": all(infrastructure.values()),
        },
        "human_agreement_audit": agreement_release_gate(root),
        "paper_ready_provenance": paper_ready_provenance_gate(root),
    }
    release_constraints = {
        "microtext_category_balance": microtext_category_balance(microtext_items),
        "visualdiff_description_finality": visualdiff_description_finality(pairs),
        "active_auditor_return_holds": auditor_return_release_constraint(root),
    }
    machine_certification = machine_certification_status(
        root,
        machine_certification_report,
        machine_calibration_reuse_report,
    )
    machine_responsibility: dict[str, Any] = {"status": "not_supplied"}
    if machine_responsibility_report is not None:
        responsibility_path = (
            machine_responsibility_report
            if machine_responsibility_report.is_absolute()
            else root / machine_responsibility_report
        )
        if not responsibility_path.is_file():
            raise FileNotFoundError(
                f"machine responsibility report not found: {responsibility_path}"
            )
        responsibility = json.loads(responsibility_path.read_text(encoding="utf-8"))
        machine_responsibility = {
            "status": str(responsibility.get("status") or "unknown"),
            "structurally_valid": bool(responsibility.get("structurally_valid")),
            "report_path": responsibility_path.resolve().relative_to(root.resolve()).as_posix(),
            "report_sha256": file_sha256(responsibility_path),
            "active_gold_modified": bool(responsibility.get("active_gold_modified")),
            "machine_owned": responsibility.get("machine_owned") or {},
            "human_owned": responsibility.get("human_owned") or {},
            "issues": responsibility.get("issues") or [],
        }
    return {
        "gates": gates,
        "baseline_or_submission_names": counted_baselines_or_submissions,
        "baseline_registry": registry,
        "submission_registry": submissions,
        "split_counts": dict(sorted(Counter(str(row.get("split", "unknown")) for row in unified).items())),
        "infrastructure": infrastructure,
        "source_payload_duplicates": {
            "duplicate_groups": duplicate_report["totals"]["duplicate_groups"],
            "duplicate_alias_doc_ids": duplicate_report["totals"]["duplicate_alias_doc_ids"],
            "local_hash_mismatches": duplicate_report["totals"]["local_hash_mismatches"],
        },
        "release_constraints": release_constraints,
        "machine_certification": machine_certification,
        "machine_responsibility": machine_responsibility,
        "v2_0_global_complete": all(row["passes"] for row in gates.values())
        and all(row["passes"] for row in release_constraints.values()),
    }


def render_markdown(status: dict[str, Any]) -> str:
    lines = [
        "# Eng_Bench v2.0 Global Gate Audit",
        "",
    ]
    if status.get("date_label"):
        lines.extend([f"- audit date label: `{status['date_label']}`"])
    lines.extend(
        [
            f"- v2.0 global complete: `{status['v2_0_global_complete']}`",
            "",
            "## Gate Checklist",
            "",
            "| Gate | Current | Target | Pass |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for name, row in status["gates"].items():
        lines.append(f"| {name} | {row['current']} | {row['target']} | `{row['passes']}` |")
    lines.extend(["", "## Split Counts", ""])
    for split, count in status["split_counts"].items():
        lines.append(f"- {split}: `{count}`")
    lines.extend(["", "## Counted Baselines And Submissions", ""])
    if status.get("baseline_or_submission_names"):
        for name in status["baseline_or_submission_names"]:
            lines.append(f"- `{name}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Release Constraints", ""])
    for name, row in status.get("release_constraints", {}).items():
        lines.append(f"- {name}: `{row['current']}` -> `{row['target']}`; pass `{row['passes']}`")
        if name == "microtext_category_balance":
            open_shortfalls = {
                category: value
                for category, value in row.get("category_shortfalls", {}).items()
                if value
            }
            lines.append(f"  - open category shortfalls: `{open_shortfalls}`")
    lines.extend(["", "## Leaderboard Infrastructure", ""])
    for name, passed in status["infrastructure"].items():
        lines.append(f"- {name}: `{passed}`")
    machine = status.get("machine_certification") or {}
    lines.extend(
        [
            "",
            "## Machine Certification",
            "",
            f"- status: `{machine.get('status', 'not_supplied')}`",
            f"- auto-eligible pending calibration: `{machine.get('auto_eligible_pending_calibration', 0)}`",
            f"- balance-closing non-pin rows: `{machine.get('auto_eligible_balance_closing_nonpin', 0)}`",
            f"- deferred pin rows: `{machine.get('auto_eligible_deferred_pin', 0)}`",
            f"- calibration rows total: `{machine.get('calibration_rows', 0)}`",
            f"- calibration rows completed by reusable human evidence: `{machine.get('calibration_rows_completed_reused', 0)}`",
            f"- calibration rows remaining: `{machine.get('calibration_rows_remaining', 0)}`",
            f"- calibration reuse evidence: `{machine.get('reuse_evidence_status', 'not_supplied')}`",
            f"- net row-by-row human decisions avoided: `{machine.get('net_row_by_row_human_decisions_avoided', 0)}`",
            f"- active Gold modified: `{machine.get('active_gold_modified', False)}`",
        ]
    )
    responsibility = status.get("machine_responsibility") or {}
    owned = responsibility.get("machine_owned") or {}
    lines.extend(
        [
            "",
            "## Machine-First Responsibility",
            "",
            f"- status: `{responsibility.get('status', 'not_supplied')}`",
            f"- historically calibrated rows: `{owned.get('historically_calibrated_rows_no_new_human_work', 0)}`",
            f"- non-pin-only calibration rows: `{owned.get('nonpin_calibration_sample_rows', 0)}`",
            f"- calibrated-lane row decisions avoided: `{owned.get('calibrated_lane_decisions_avoided_net', owned.get('human_row_by_row_decisions_avoided_net', 0))}`",
            f"- source-intake row decisions avoided: `{owned.get('source_intake_row_decisions_avoided', 0)}`",
            f"- total machine row decisions avoided: `{owned.get('total_machine_row_decisions_avoided', owned.get('human_row_by_row_decisions_avoided_net', 0))}`",
            f"- strict-unique balance-deferred rows: `{owned.get('strict_unique_balance_deferred_rows', 0)}`",
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "v2.0 is a scale, trust, and adoption gate. Machine certification can remove repetitive train-MicroText work, but it does not count before calibration and strict promotion. Evaluation labels, agreement, source/test scale, provenance, and semantic review remain human- or release-gated.",
            "",
        ]
    )
    return "\n".join(lines)


def default_output_paths(date_label: str) -> tuple[str, str]:
    stem = f"results/health/v2_0_gate_audit_{date_label}"
    return f"{stem}.json", f"{stem}.md"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Eng_Bench v2.0 Global release gate.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--date-label",
        default=date.today().isoformat(),
        help="Date label to embed in default output filenames.",
    )
    parser.add_argument(
        "--output",
        help="Backward-compatible alias for --output-md.",
    )
    parser.add_argument("--output-json", help="JSON report output path.")
    parser.add_argument("--output-md", help="Markdown report output path.")
    parser.add_argument(
        "--machine-certification-report",
        type=Path,
        help="Optional eligibility report to include as non-Gold machine-work evidence.",
    )
    parser.add_argument(
        "--machine-responsibility-report",
        type=Path,
        help="Optional verified machine/human responsibility ledger.",
    )
    parser.add_argument(
        "--machine-calibration-reuse-report",
        type=Path,
        help="Optional hash-bound report for completed human calibration decisions reused from prior review.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    default_json, default_md = default_output_paths(args.date_label)
    output_json = root / (args.output_json or default_json)
    output_md = root / (args.output_md or args.output or default_md)
    status = collect_status(
        root,
        args.machine_certification_report,
        args.machine_responsibility_report,
        args.machine_calibration_reuse_report,
    )
    status["date_label"] = args.date_label
    write_json(output_json, status)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(status), encoding="utf-8")
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
