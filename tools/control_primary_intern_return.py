#!/usr/bin/env python3
"""Process a primary-review return and build fail-closed release previews.

This is the maintainer entry point for the current primary workbook. It never
applies rows to active Gold. It validates and stages the workbook, attaches
independent-auditor overlap registries, partitions ordinary reviewed rows from
provenance replacements, audits ordinary promotion eligibility, audits the
complete provenance migration contract, and emits only read-only previews plus
an explicit next-action report.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_provenance_migration_readiness as migration_readiness
import audit_staged_promotion_contract as staged_contract
import preview_provenance_replacement_migration as migration_preview
import preview_reviewed_gold_promotion as promotion_preview
import process_primary_intern_catchup_return as primary_return


DEFAULT_REPLACEMENT_PLAN = (
    "derived/quality/"
    "v2_0_provenance_replacement_plan_2026-08-29-wave993-current-rights.json"
)
DEFAULT_AFFECTED_ROWS = (
    "derived/quality/"
    "v2_0_provenance_blocked_active_rows_2026-08-29-wave993-current-rights.jsonl"
)
DEFAULT_REPLACEMENT_CANDIDATES = (
    "derived/review_queues/"
    "v2_0_provenance_replacement_candidates_2026-08-29-wave993-current-rights.jsonl"
)
DEFAULT_SPLIT_PLAN = "derived/quality/v2_0_staged_split_plan_2026-08-17-wave214.json"
DEFAULT_MIGRATION_SPLIT_PLANS = (
    "derived/quality/v2_0_staged_split_plan_2026-08-21-wave335.json",
    "derived/quality/v2_0_staged_split_plan_2026-08-22-wave472-arduino-leonardo-complete.json",
)


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def row_identity(row: dict[str, Any], task: str) -> str:
    fields = ("candidate_id", "record_id", "item_id") if task == "microtext" else (
        "pair_id",
        "record_id",
    )
    return next(
        (str(row.get(field) or "").strip() for field in fields if str(row.get(field) or "").strip()),
        "",
    )


def partition_reviewed_rows(
    micro_rows: list[dict[str, Any]],
    visual_rows: list[dict[str, Any]],
    replacement_ids: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """Separate standard promotion rows from atomic provenance replacements."""
    partitions = {
        "standard_microtext": [],
        "standard_visualdiff": [],
        "provenance_replacements": [],
    }
    issues: list[dict[str, str]] = []
    seen: set[str] = set()
    for task, rows in (("microtext", micro_rows), ("visualdiff", visual_rows)):
        for row in rows:
            identity = row_identity(row, task)
            scoped = f"{task}:{identity}"
            if not identity:
                issues.append({"task": task, "identity": "", "issue": "missing_identity"})
                continue
            if scoped in seen:
                issues.append(
                    {"task": task, "identity": identity, "issue": "duplicate_reviewed_identity"}
                )
                continue
            seen.add(scoped)
            if row.get("safe_to_merge_gold") is not False:
                issues.append(
                    {"task": task, "identity": identity, "issue": "unsafe_merge_flag"}
                )
            marker_replacement = row.get("provenance_replacement_candidate") is True
            replacement = (
                identity in replacement_ids
                if replacement_ids is not None
                else marker_replacement
            )
            if replacement_ids is not None and marker_replacement and not replacement:
                row = dict(row)
                for field in list(row):
                    if field == "provenance_replacement_candidate" or field.startswith(
                        "replacement_"
                    ) or field.startswith("provenance_replacement_"):
                        row.pop(field, None)
                row["stale_provenance_marker_normalized"] = True
            if replacement:
                if not marker_replacement:
                    issues.append(
                        {
                            "task": task,
                            "identity": identity,
                            "issue": "replacement_marker_missing",
                        }
                    )
                replacement_task = str(row.get("replacement_for_task") or "").strip().lower()
                if replacement_task != task:
                    issues.append(
                        {
                            "task": task,
                            "identity": identity,
                            "issue": "replacement_task_mismatch",
                        }
                    )
                required = (
                    "replacement_for_split",
                    "replacement_rights_check",
                    "replacement_evidence_fingerprint",
                    "replacement_evidence_fingerprint_status",
                )
                for field in required:
                    if row.get(field) in (None, "", []):
                        issues.append(
                            {
                                "task": task,
                                "identity": identity,
                                "issue": f"missing_{field}",
                            }
                        )
                partitions["provenance_replacements"].append(row)
            else:
                partitions[f"standard_{task}"].append(row)
    return partitions, issues


def next_actions(
    processing: dict[str, Any],
    standard_state: str,
    migration: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    totals = processing.get("totals") or {}
    if not processing.get("complete"):
        actions.append(
            "Return resume_rework.csv to the primary reviewer and finish all "
            f"{totals.get('rework_rows', 0)} incomplete or invalid rows."
        )
        return actions
    if standard_state == "blocked":
        actions.append(
            "Resolve every ordinary-row promotion hold, then rerun this control command in a fresh output directory."
        )
    elif standard_state == "ready":
        actions.append(
            "Inspect the ordinary reviewed-Gold preview and pin its report SHA-256 before any separate apply transaction."
        )
    counts = migration.get("counts") or {}
    outstanding = int(counts.get("outstanding_review_rows") or 0)
    if outstanding:
        actions.append(
            f"Complete or replace the remaining {outstanding} provenance-contract rows; active rights-blocked rows stay in Gold meanwhile."
        )
    elif migration.get("ready_for_atomic_migration"):
        actions.append(
            "Inspect the atomic provenance migration preview and require every gate before a separately authorized apply transaction."
        )
    return actions or ["No human or machine follow-up was identified."]


def render_markdown(report: dict[str, Any]) -> str:
    processing = report.get("primary_processing") or {}
    totals = processing.get("totals") or {}
    provenance = report.get("provenance_migration") or {}
    counts = provenance.get("counts") or {}
    lines = [
        "# Primary Return Release Control",
        "",
        "- Goal: **Gold v2.0 Global**",
        f"- Primary return complete: `{str(bool(processing.get('complete'))).lower()}`",
        f"- Ready human decisions: `{totals.get('ready_decisions', 0)}/{totals.get('expected_rows', 0)}`",
        f"- Engineering decisions complete: `{totals.get('engineering_ready', 0)}/{totals.get('engineering_expected', 0)}`",
        f"- Ordinary promotion state: `{report.get('ordinary_promotion', {}).get('state', 'not_run')}`",
        f"- Provenance replacements reviewed: `{counts.get('accepted_or_edited_reviewed_rows', 0)}/{counts.get('replacement_candidates', 0)}`",
        f"- Atomic migration ready: `{str(bool(provenance.get('ready_for_atomic_migration'))).lower()}`",
        f"- Active Gold modified: `{str(bool(report.get('active_gold_modified'))).lower()}`",
        "- Safe to merge Gold directly: `false`",
        "",
        "## Next Actions",
        "",
    ]
    lines.extend(f"{index}. {action}" for index, action in enumerate(report["next_actions"], 1))
    lines.extend(
        [
            "",
            "This control output is read-only. A green preview is evidence for a separate hash-locked apply transaction, not permission to edit active annotations manually.",
            "",
        ]
    )
    return "\n".join(lines)


def run_control(
    *,
    root: Path,
    workbook: Path,
    payload: Path,
    output_dir: Path,
    date_label: str,
    replacement_plan: Path,
    affected_rows: Path,
    replacement_candidates: Path,
    split_plan: Path,
    migration_split_plans: list[Path],
    additional_provenance_reviewed: list[Path],
    additional_overlap_ids: list[Path],
    snapshot_workbook: bool,
) -> dict[str, Any]:
    root = root.resolve()
    allowed_root = (root / "derived" / "quality").resolve()
    output_dir = output_dir.resolve()
    if output_dir != allowed_root and allowed_root not in output_dir.parents:
        raise ValueError("output-dir must be under derived/quality")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    active_before = promotion_preview.active_hashes(root)
    control_issues: list[dict[str, str]] = []

    processed_dir = output_dir / "processed_primary"
    processor_args = [
        "--root",
        str(root),
        "--workbook",
        str(workbook),
        "--payload",
        str(payload),
        "--provenance-replacement-candidates",
        str(replacement_candidates),
        "--output-dir",
        str(processed_dir),
        "--date-label",
        date_label,
    ]
    for path in additional_overlap_ids:
        processor_args.extend(["--additional-overlap-ids", str(path)])
    if not snapshot_workbook:
        processor_args.append("--no-snapshot")
    processor_stdout = io.StringIO()
    processor_error = ""
    try:
        with redirect_stdout(processor_stdout):
            processor_exit = primary_return.main(processor_args)
    except Exception as error:  # Fail closed and preserve the active-hash evidence.
        processor_exit = 1
        processor_error = f"{type(error).__name__}:{error}"
        control_issues.append({"stage": "primary_processor", "issue": processor_error})
    (output_dir / "primary_processor_stdout.log").write_text(
        processor_stdout.getvalue(), encoding="utf-8"
    )

    processing_path = processed_dir / "processing_summary.json"
    if processing_path.is_file():
        processing = read_json(processing_path)
    else:
        processing = {
            "complete": False,
            "totals": {},
            "processor_exit": processor_exit,
            "processor_error": processor_error or "processing_summary_missing",
        }
        control_issues.append(
            {"stage": "primary_processor", "issue": "processing_summary_missing"}
        )

    micro_rows = read_jsonl(processed_dir / "microtext_reviewed_pending_gates.jsonl")
    visual_rows = read_jsonl(processed_dir / "visualdiff_reviewed_pending_gates.jsonl")
    replacement_contract_ids = {
        row_identity(row, str(row.get("replacement_for_task") or "").strip().lower())
        for row in read_jsonl(replacement_candidates)
    }
    replacement_contract_ids.discard("")
    partitions, partition_issues = partition_reviewed_rows(
        micro_rows,
        visual_rows,
        replacement_contract_ids,
    )
    control_issues.extend(
        {"stage": "partition", **issue} for issue in partition_issues
    )
    partition_paths = {
        "standard_microtext": output_dir / "standard_microtext_reviewed.jsonl",
        "standard_visualdiff": output_dir / "standard_visualdiff_reviewed.jsonl",
        "provenance_replacements": output_dir / "provenance_replacements_reviewed.jsonl",
    }
    for name, path in partition_paths.items():
        write_jsonl(path, partitions[name])

    standard_count = len(partitions["standard_microtext"]) + len(
        partitions["standard_visualdiff"]
    )
    standard_report: dict[str, Any] = {
        "state": "not_run_primary_incomplete",
        "reviewed_rows": standard_count,
        "ready_for_apply": False,
    }
    if processing.get("complete") and not partition_issues:
        if standard_count == 0:
            standard_report.update(state="not_applicable_no_rows", ready_for_apply=True)
        else:
            cohorts = [
                (name, path)
                for name, path in (
                    ("primary_standard_microtext", partition_paths["standard_microtext"]),
                    ("primary_standard_visualdiff", partition_paths["standard_visualdiff"]),
                )
                if read_jsonl(path)
            ]
            contract_report, contract_issues = staged_contract.build_report(
                root, cohorts, split_plan, date_label=date_label
            )
            contract_json = output_dir / "ordinary_promotion_contract.json"
            contract_md = output_dir / "ordinary_promotion_contract.md"
            contract_csv = output_dir / "ordinary_promotion_contract_issues.csv"
            staged_contract.write_json(contract_json, contract_report)
            staged_contract.write_markdown(contract_md, contract_report)
            staged_contract.write_csv(contract_csv, contract_issues)
            standard_report.update(
                state="blocked",
                contract=contract_report,
                contract_report=display(root, contract_json),
            )
            if (
                contract_report["structurally_ready_for_human_return_promotion"]
                and contract_report["human_review_complete"]
            ):
                preview_dir = output_dir / "ordinary_promotion_preview"
                preview = promotion_preview.build_preview(
                    root,
                    [partition_paths["standard_microtext"]]
                    if partitions["standard_microtext"]
                    else [],
                    [partition_paths["standard_visualdiff"]]
                    if partitions["standard_visualdiff"]
                    else [],
                    split_plan,
                    preview_dir,
                    date_label,
                )
                write_json(preview_dir / "promotion_preview_report.json", preview)
                (preview_dir / "promotion_preview_report.md").write_text(
                    promotion_preview.render_markdown(preview), encoding="utf-8"
                )
                standard_report.update(
                    state="ready" if preview["ready_for_apply"] else "blocked",
                    ready_for_apply=bool(preview["ready_for_apply"]),
                    preview=preview,
                    preview_report=display(
                        root, preview_dir / "promotion_preview_report.json"
                    ),
                )

    reviewed_paths = [partition_paths["provenance_replacements"]] + [
        resolve(root, path) for path in additional_provenance_reviewed
    ]
    readiness = migration_readiness.build_report(
        root=root,
        plan_path=replacement_plan,
        affected_path=affected_rows,
        candidates_path=replacement_candidates,
        reviewed_paths=reviewed_paths,
        date_label=date_label,
    )
    readiness_json = output_dir / "provenance_migration_readiness.json"
    readiness_md = output_dir / "provenance_migration_readiness.md"
    migration_readiness.write_json(readiness_json, readiness)
    readiness_md.write_text(
        migration_readiness.render_markdown(readiness), encoding="utf-8"
    )
    migration_dir = output_dir / "provenance_migration_preview"
    migration = migration_preview.build_preview(
        root=root,
        plan_path=replacement_plan,
        affected_path=affected_rows,
        candidates_path=replacement_candidates,
        reviewed_paths=reviewed_paths,
        split_plan_paths=migration_split_plans,
        output_dir=migration_dir,
        date_label=date_label,
    )
    write_json(migration_dir / "migration_preview_report.json", migration)
    (migration_dir / "migration_preview_report.md").write_text(
        migration_preview.render_markdown(migration), encoding="utf-8"
    )

    active_after = promotion_preview.active_hashes(root)
    active_modified = active_before != active_after
    if active_modified:
        control_issues.append(
            {"stage": "active_hash_guard", "issue": "active_files_changed"}
        )
    all_previews_ready = bool(
        processing.get("complete")
        and standard_report.get("ready_for_apply")
        and migration.get("ready_for_atomic_apply")
        and not control_issues
        and not active_modified
    )
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_primary_return_control",
        "safe_to_merge_gold": False,
        "active_gold_modified": active_modified,
        "all_previews_ready": all_previews_ready,
        "processor_exit": processor_exit,
        "primary_processing": processing,
        "agreement_overlap": {
            "inputs": processing.get("additional_overlap_inputs", []),
            "unique_ids": processing.get("additional_overlap_unique_ids", 0),
            "ids_in_primary_payload": processing.get(
                "additional_overlap_ids_in_payload", 0
            ),
            "ids_outside_primary_payload": processing.get(
                "additional_overlap_ids_outside_payload", 0
            ),
            "effective_overlap_ids": processing.get("effective_overlap_ids", 0),
        },
        "partition": {
            "standard_microtext": len(partitions["standard_microtext"]),
            "standard_visualdiff": len(partitions["standard_visualdiff"]),
            "provenance_replacements": len(partitions["provenance_replacements"]),
            "issues": partition_issues,
            "artifacts": {name: display(root, path) for name, path in partition_paths.items()},
        },
        "ordinary_promotion": standard_report,
        "provenance_migration": readiness,
        "provenance_migration_preview": {
            "preview_built": migration["preview_built"],
            "ready_for_atomic_apply": migration["ready_for_atomic_apply"],
            "report": display(root, migration_dir / "migration_preview_report.json"),
        },
        "control_issues": control_issues,
        "active_file_hashes_before": active_before,
        "active_file_hashes_after": active_after,
    }
    report["next_actions"] = next_actions(
        processing,
        "ready" if standard_report.get("ready_for_apply") else (
            "blocked" if processing.get("complete") and standard_count else "not_run"
        ),
        readiness,
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--replacement-plan", type=Path, default=Path(DEFAULT_REPLACEMENT_PLAN))
    parser.add_argument("--affected-rows", type=Path, default=Path(DEFAULT_AFFECTED_ROWS))
    parser.add_argument(
        "--replacement-candidates",
        type=Path,
        default=Path(DEFAULT_REPLACEMENT_CANDIDATES),
    )
    parser.add_argument("--split-plan", type=Path, default=Path(DEFAULT_SPLIT_PLAN))
    parser.add_argument(
        "--migration-split-plan",
        type=Path,
        action="append",
        default=None,
        help=(
            "Split reservation plan for the provenance migration contract; "
            "repeat for contracts spanning multiple plans"
        ),
    )
    parser.add_argument(
        "--additional-provenance-reviewed", type=Path, action="append", default=[]
    )
    parser.add_argument(
        "--additional-overlap-ids",
        type=Path,
        action="append",
        default=[],
        help=(
            "JSONL identities with completed or reserved independent-auditor "
            "coverage; repeat for multiple registries"
        ),
    )
    parser.add_argument("--no-workbook-snapshot", action="store_true")
    parser.add_argument("--require-primary-complete", action="store_true")
    parser.add_argument("--require-all-previews-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    output_dir = resolve(root, args.output_dir)
    report = run_control(
        root=root,
        workbook=resolve(root, args.workbook),
        payload=resolve(root, args.payload),
        output_dir=output_dir,
        date_label=args.date_label,
        replacement_plan=resolve(root, args.replacement_plan),
        affected_rows=resolve(root, args.affected_rows),
        replacement_candidates=resolve(root, args.replacement_candidates),
        split_plan=resolve(root, args.split_plan),
        migration_split_plans=[
            resolve(root, path)
            for path in (
                args.migration_split_plan
                if args.migration_split_plan is not None
                else [Path(value) for value in DEFAULT_MIGRATION_SPLIT_PLANS]
            )
        ],
        additional_provenance_reviewed=args.additional_provenance_reviewed,
        additional_overlap_ids=[
            resolve(root, path) for path in args.additional_overlap_ids
        ],
        snapshot_workbook=not args.no_workbook_snapshot,
    )
    write_json(output_dir / "primary_return_control_report.json", report)
    (output_dir / "primary_return_control_report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "primary_complete": bool(report["primary_processing"].get("complete")),
                "ordinary_promotion_state": report["ordinary_promotion"]["state"],
                "provenance_reviewed": report["provenance_migration"]["counts"][
                    "accepted_or_edited_reviewed_rows"
                ],
                "provenance_outstanding": report["provenance_migration"]["counts"][
                    "outstanding_review_rows"
                ],
                "all_previews_ready": report["all_previews_ready"],
                "active_gold_modified": report["active_gold_modified"],
                "control_issues": report["control_issues"],
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    if report["active_gold_modified"] or report["control_issues"]:
        return 1
    if args.require_primary_complete and not report["primary_processing"].get("complete"):
        return 2
    if args.require_all_previews_ready and not report["all_previews_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
