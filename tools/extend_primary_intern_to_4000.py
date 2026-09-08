#!/usr/bin/env python3
"""Extend the authoritative primary-intern assignment to 4,000 review rows.

The extension adds 600 scarce/non-pin MicroText rows and 400 VisualDiff rows.
Every added row is an engineering deep-review action. The existing 18
standalone specialist actions are carried forward unchanged. Nothing produced
by this tool is Gold-ready without a completed human return and machine gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.prepare_incremental_human_audit_round import (
    active_gold_ids,
    evidence_materializable,
    identifier,
    read_jsonl,
    render_micro_evidence,
    render_visual_evidence,
    sha256_file,
    source_group,
    split_name,
    task,
    write_jsonl,
)
from tools.prepare_primary_intern_catchup_packet import payload_record


TARGET_PRIMARY_ROWS = 4_000
TARGET_EXTENSION_ROWS = 1_000
TARGET_MICRO_ROWS = 600
TARGET_VISUAL_ROWS = 400
EXPECTED_PRIOR_ROWS = 3_000
EXPECTED_SPECIALIST_ROWS = 18

ENGINEERING_MICRO_CATEGORIES = (
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "component_value",
    "pipe_line_tag",
    "process_value",
    "process_label",
    "room_label",
    "tolerance_value",
)


def resolved(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def diverse_order(
    rows: Iterable[dict[str, Any]],
    group: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    """Round-robin rows across groups while preserving deterministic order."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[group(row)].append(row)
    buckets = {
        key: deque(sorted(values, key=lambda row: (source_group(row), split_name(row), identifier(row))))
        for key, values in grouped.items()
    }
    active = deque(sorted(buckets))
    ordered: list[dict[str, Any]] = []
    while active:
        key = active.popleft()
        ordered.append(buckets[key].popleft())
        if buckets[key]:
            active.append(key)
    return ordered


def micro_candidate_order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in rows
        if task(row) == "microtext"
        and str(row.get("category") or "") in ENGINEERING_MICRO_CATEGORIES
    ]
    per_category = {
        category: diverse_order(
            [row for row in eligible if str(row.get("category") or "") == category],
            source_group,
        )
        for category in ENGINEERING_MICRO_CATEGORIES
    }
    buckets = {key: deque(value) for key, value in per_category.items() if value}
    active = deque(key for key in ENGINEERING_MICRO_CATEGORIES if key in buckets)
    ordered: list[dict[str, Any]] = []
    while active:
        key = active.popleft()
        ordered.append(buckets[key].popleft())
        if buckets[key]:
            active.append(key)
    return ordered


def visual_candidate_order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return diverse_order(
        [row for row in rows if task(row) == "visualdiff"],
        lambda row: str(row.get("project_id") or source_group(row)),
    )


def prioritize_provenance_replacements(
    priority_rows: Iterable[dict[str, Any]],
    balanced_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Put exact release-critical replacements before the balance reservoir."""
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in list(priority_rows) + list(balanced_rows):
        identity = identifier(row)
        if identity and identity not in seen:
            seen.add(identity)
            ordered.append(row)
    return ordered


def classify_priority_coverage(
    priority_ids: set[str],
    primary_ids: set[str],
    specialist_ids: set[str],
    evidence_aliases: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    """Classify frozen replacements without creating duplicate review work."""
    direct_primary = priority_ids & primary_ids
    specialist = (priority_ids - direct_primary) & specialist_ids
    alias_ids = {
        str(row.get("record_id") or "")
        for row in evidence_aliases
        if str(row.get("record_id") or "")
    }
    exact_evidence_alias = (priority_ids - direct_primary - specialist) & alias_ids
    missing = priority_ids - direct_primary - specialist - exact_evidence_alias
    return {
        "direct_primary": sorted(direct_primary),
        "specialist": sorted(specialist),
        "exact_evidence_alias": sorted(exact_evidence_alias),
        "missing": sorted(missing),
    }


def primary_micro_candidate_order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in rows if task(row) == "microtext"]
    replacements = diverse_order(
        [row for row in rows if row.get("provenance_replacement_candidate")],
        source_group,
    )
    return prioritize_provenance_replacements(replacements, micro_candidate_order(rows))


def primary_visual_candidate_order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in rows if task(row) == "visualdiff"]
    replacements = visual_candidate_order(
        [row for row in rows if row.get("provenance_replacement_candidate")]
    )
    return prioritize_provenance_replacements(replacements, visual_candidate_order(rows))


def engineering_reason(row: dict[str, Any]) -> str:
    if task(row) == "visualdiff":
        return (
            "Gold v2.0 工程专项：判断 OLD/NEW 红框内是否存在真实工程变化；"
            "核对规范变化类型和完整描述，并写出尺寸、连接、元件、工艺或版式依据"
        )
    category = str(row.get("category") or "unknown_microtext")
    return (
        f"Gold v2.0 类别平衡工程专项：核对截图边界、逐字文字、{category} 类别和"
        "图纸语义，并写出可复查工程依据"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--prior-payload", required=True)
    parser.add_argument("--future-capacity", required=True)
    parser.add_argument(
        "--priority-candidates",
        help="Frozen JSONL contract whose remaining identities must be selected first",
    )
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--pool-jsonl", required=True)
    parser.add_argument("--duplicate-holds-jsonl", required=True)
    parser.add_argument("--payload-json", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    paths = {
        key: resolved(root, value)
        for key, value in {
            "prior_payload": args.prior_payload,
            "future_capacity": args.future_capacity,
            "evidence_dir": args.evidence_dir,
            "pool_jsonl": args.pool_jsonl,
            "duplicate_holds_jsonl": args.duplicate_holds_jsonl,
            "payload_json": args.payload_json,
            "report_json": args.report_json,
            "report_md": args.report_md,
        }.items()
    }
    for key in (
        "evidence_dir",
        "pool_jsonl",
        "duplicate_holds_jsonl",
        "payload_json",
        "report_json",
        "report_md",
    ):
        if paths[key].exists():
            raise FileExistsError(f"output already exists: {paths[key]}")

    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = sha256_file(gold_path)
    prior_payload = json.loads(paths["prior_payload"].read_text(encoding="utf-8"))
    prior_records = list(prior_payload.get("rows") or [])
    specialist = dict(prior_payload.get("specialist") or {})
    specialist_rows = list(specialist.get("visualdiff_english") or []) + list(
        specialist.get("microtext_balance") or []
    )
    if len(prior_records) != EXPECTED_PRIOR_ROWS:
        raise ValueError(f"expected {EXPECTED_PRIOR_ROWS} prior primary rows, got {len(prior_records)}")
    if len(specialist_rows) != EXPECTED_SPECIALIST_ROWS:
        raise ValueError(f"expected {EXPECTED_SPECIALIST_ROWS} specialist rows, got {len(specialist_rows)}")

    inputs = dict(prior_payload.get("inputs") or {})
    prior_pool_path = resolved(root, inputs["pool"])
    if sha256_file(prior_pool_path) != inputs["pool_sha256"]:
        raise ValueError("prior primary pool hash does not match payload")
    prior_pool = read_jsonl(prior_pool_path)
    prior_by_id = {identifier(row): row for row in prior_pool}
    prior_ids = {str(record.get("record_id") or "") for record in prior_records}
    if len(prior_ids) != EXPECTED_PRIOR_ROWS or set(prior_by_id) != prior_ids:
        raise ValueError("prior primary payload and pool identities do not match")

    specialist_ids = {str(row.get("record_id") or "") for row in specialist_rows}
    if len(specialist_ids) != EXPECTED_SPECIALIST_ROWS:
        raise ValueError("specialist payload contains duplicate or missing identities")
    occupied_ids = prior_ids | specialist_ids | active_gold_ids(root)

    seen_evidence_owners: dict[str, str] = {}
    for record in prior_records + specialist_rows:
        evidence_path = Path(str(record.get("evidence_path") or ""))
        if not evidence_path.is_file():
            raise FileNotFoundError(f"assigned evidence is missing: {record.get('record_id')}")
        digest = file_sha256(evidence_path)
        if digest in seen_evidence_owners:
            raise ValueError(f"prior assignment contains duplicate evidence: {record.get('record_id')}")
        seen_evidence_owners[digest] = str(record.get("record_id") or "")

    future_rows = read_jsonl(paths["future_capacity"])
    future_rows = [row for row in future_rows if identifier(row) and identifier(row) not in occupied_ids]
    future_ids = [identifier(row) for row in future_rows]
    if len(future_ids) != len(set(future_ids)):
        raise ValueError("future capacity contains duplicate identities")

    priority_path = (
        resolved(root, args.priority_candidates) if args.priority_candidates else None
    )
    priority_by_id: dict[str, dict[str, Any]] = {}
    priority_pending_ids: set[str] = set()
    if priority_path:
        priority_rows = read_jsonl(priority_path)
        priority_by_id = {
            identifier(row): row for row in priority_rows if identifier(row)
        }
        if len(priority_by_id) != len(priority_rows):
            raise ValueError("priority candidate contract contains duplicate or blank identities")
        priority_pending_ids = set(priority_by_id) - occupied_ids
        future_by_id = {identifier(row): row for row in future_rows}
        missing_priority = sorted(priority_pending_ids - set(future_by_id))
        if missing_priority:
            raise ValueError(
                f"{len(missing_priority)} pending priority candidates are missing from future capacity"
            )
        enriched_future: list[dict[str, Any]] = []
        for row in future_rows:
            identity = identifier(row)
            if identity in priority_by_id:
                merged = dict(row)
                merged.update(priority_by_id[identity])
                row = merged
            enriched_future.append(row)
        future_rows = enriched_future

    candidate_groups = [
        ("microtext", primary_micro_candidate_order(future_rows), TARGET_MICRO_ROWS),
        ("visualdiff", primary_visual_candidate_order(future_rows), TARGET_VISUAL_ROWS),
    ]
    combined_pool: list[dict[str, Any]] = []
    payload_rows: list[dict[str, Any]] = []
    for index, record in enumerate(prior_records, 1):
        source = dict(prior_by_id[str(record["record_id"])])
        source.update(
            {
                "primary_pool_index": index,
                "primary_pool_status": "issued_primary_4000",
                "safe_to_merge_gold": False,
            }
        )
        combined_pool.append(source)
        carried = dict(record)
        carried["primary_index"] = index
        carried["carried_from_previous_workbook"] = True
        payload_rows.append(carried)

    paths["evidence_dir"].mkdir(parents=True)
    selected: list[dict[str, Any]] = []
    evidence_aliases: list[dict[str, Any]] = []
    duplicate_holds: list[dict[str, Any]] = []
    selected_task_counts: Counter[str] = Counter()
    for expected_task, ordered_rows, target in candidate_groups:
        for row in ordered_rows:
            if selected_task_counts[expected_task] >= target:
                break
            identity = identifier(row)
            if not evidence_materializable(root, row):
                duplicate_holds.append(
                    {
                        "record_id": identity,
                        "task": expected_task,
                        "hold_reason": "evidence_not_materializable",
                        "safe_to_merge_gold": False,
                    }
                )
                continue
            position = len(payload_rows) + 1
            suffix = identity.replace("/", "_")[-48:]
            output = paths["evidence_dir"] / f"primary_{position:04d}_{expected_task}_{suffix}.png"
            if expected_task == "microtext":
                render_micro_evidence(root, row, output)
            else:
                render_visual_evidence(root, row, output)
            digest = file_sha256(output)
            if digest in seen_evidence_owners:
                owner_id = seen_evidence_owners[digest]
                is_priority_alias = identity in priority_pending_ids
                duplicate_holds.append(
                    {
                        "record_id": identity,
                        "task": expected_task,
                        "hold_reason": (
                            "duplicate_rendered_evidence_covered_by_assigned_alias"
                            if is_priority_alias
                            else "duplicate_rendered_evidence_against_assignment"
                        ),
                        "evidence_sha256": digest,
                        "review_via_record_id": owner_id if is_priority_alias else "",
                        "safe_to_merge_gold": False,
                    }
                )
                output.unlink()
                if is_priority_alias:
                    evidence_aliases.append(
                        {
                            "record_id": identity,
                            "task": expected_task,
                            "review_via_record_id": owner_id,
                            "evidence_sha256": digest,
                            "coverage_basis": "exact_rendered_evidence_sha256",
                            "safe_to_merge_gold": False,
                        }
                    )
                    alias_source = dict(row)
                    alias_source.update(
                        {
                            "primary_pool_status": "review_via_exact_evidence_alias",
                            "primary_pool_target": "Gold v2.0 Global",
                            "review_via_record_id": owner_id,
                            "evidence_sha256": digest,
                            "safe_to_merge_gold": False,
                        }
                    )
                    combined_pool.append(alias_source)
                continue
            seen_evidence_owners[digest] = identity
            selected_task_counts[expected_task] += 1
            selected.append(row)
            source = dict(row)
            source.update(
                {
                    "primary_pool_index": position,
                    "primary_pool_status": "issued_primary_4000",
                    "primary_pool_target": "Gold v2.0 Global",
                    "primary_pool_capacity_cohort": str(
                        row.get("canonical_capacity_origin_cohort") or "canonical_future_wave259"
                    ),
                    "primary_pool_capacity_path": str(paths["future_capacity"]),
                    "safe_to_merge_gold": False,
                }
            )
            combined_pool.append(source)
            record = payload_record(source, output, engineering_reason(row), False)
            record.update(
                {
                    "carried_from_previous_workbook": False,
                    "provenance_replacement": bool(row.get("provenance_replacement_candidate")),
                    "mandatory_description_rewrite": False,
                    "evidence_sha256": digest,
                }
            )
            payload_rows.append(record)
            if len(selected) % 100 == 0:
                print(f"rendered extension {len(selected)}/{TARGET_EXTENSION_ROWS}")
        if selected_task_counts[expected_task] != target:
            raise ValueError(
                f"only selected {selected_task_counts[expected_task]} of {target} {expected_task} rows"
            )

    if len(payload_rows) != TARGET_PRIMARY_ROWS or len(selected) != TARGET_EXTENSION_ROWS:
        raise ValueError(f"expected {TARGET_PRIMARY_ROWS} primary rows, got {len(payload_rows)}")
    all_primary_ids = [str(row["record_id"]) for row in payload_rows]
    if len(all_primary_ids) != len(set(all_primary_ids)):
        raise ValueError("extended primary payload contains duplicate identities")
    priority_coverage = classify_priority_coverage(
        set(priority_by_id),
        set(all_primary_ids),
        specialist_ids,
        evidence_aliases,
    )
    if priority_coverage["missing"]:
        raise ValueError(
            f"{len(priority_coverage['missing'])} priority candidates lack review coverage"
        )

    engineering_rows = [row for row in payload_rows if row.get("engineering_required")]
    for engineering_index, row in enumerate(engineering_rows, 1):
        row["engineering_index"] = engineering_index

    write_jsonl(paths["pool_jsonl"], combined_pool)
    write_jsonl(paths["duplicate_holds_jsonl"], duplicate_holds)
    task_counts = Counter(str(row.get("task") or "") for row in payload_rows)
    engineering_task_counts = Counter(str(row.get("task") or "") for row in engineering_rows)
    specialist_total = len(specialist_rows)
    counts = {
        "total": len(payload_rows),
        "total_human_actions": len(payload_rows) + specialist_total,
        "microtext": task_counts["microtext"],
        "visualdiff": task_counts["visualdiff"],
        "engineering_total": len(engineering_rows),
        "engineering_microtext": engineering_task_counts["microtext"],
        "engineering_visualdiff": engineering_task_counts["visualdiff"],
        "engineering_actions_total": len(engineering_rows) + specialist_total,
        "mandatory_description_rewrite": sum(
            bool(row.get("mandatory_description_rewrite")) for row in payload_rows
        ),
        "carried_from_previous_workbook": EXPECTED_PRIOR_ROWS,
        "prior_duplicate_rows_removed": 0,
        "new_extension": len(selected),
        "new_extension_microtext": selected_task_counts["microtext"],
        "new_extension_visualdiff": selected_task_counts["visualdiff"],
        "specialist_total": specialist_total,
        "specialist_visualdiff_english": len(specialist.get("visualdiff_english") or []),
        "specialist_microtext_balance": len(specialist.get("microtext_balance") or []),
        "provenance_replacement_direct_primary": len(priority_coverage["direct_primary"]),
        "provenance_replacement_specialist": len(priority_coverage["specialist"]),
        "provenance_replacement_exact_evidence_alias": len(
            priority_coverage["exact_evidence_alias"]
        ),
        "provenance_replacement_coverage_total": sum(
            len(priority_coverage[key])
            for key in ("direct_primary", "specialist", "exact_evidence_alias")
        ),
    }
    new_inputs = dict(inputs)
    new_inputs.update(
        {
            "pool": str(paths["pool_jsonl"]),
            "pool_sha256": sha256_file(paths["pool_jsonl"]),
            "prior_payload": {
                "path": str(paths["prior_payload"]),
                "sha256": sha256_file(paths["prior_payload"]),
            },
            "future_capacity": {
                "path": str(paths["future_capacity"]),
                "sha256": sha256_file(paths["future_capacity"]),
            },
            "priority_candidates": {
                "path": str(priority_path) if priority_path else "",
                "sha256": sha256_file(priority_path) if priority_path else "",
            },
        }
    )
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "superseding 4,000-row primary catch-up plus mandatory engineering adjudication",
        "rows": payload_rows,
        "specialist": specialist,
        "provenance_replacement_aliases": evidence_aliases,
        "provenance_replacement_coverage": priority_coverage,
        "counts": counts,
        "inputs": new_inputs,
        "safety": {
            "gold_rows_modified": 0,
            "all_rows_safe_to_merge_gold": False,
            "promotion_requires_completed_human_return_and_machine_gates": True,
            "specialist_rows_are_distinct_from_primary_rows": True,
            "supersedes_prior_primary_workbook": True,
        },
    }
    write_json(paths["payload_json"], payload)

    extension_categories = Counter(str(row.get("category") or "visualdiff") for row in selected)
    report = {
        "status": "PASS",
        "goal": "Gold v2.0 Global",
        "pool": str(paths["pool_jsonl"]),
        "pool_sha256": sha256_file(paths["pool_jsonl"]),
        "payload": str(paths["payload_json"]),
        "payload_sha256": sha256_file(paths["payload_json"]),
        "future_capacity": str(paths["future_capacity"]),
        "counts": counts,
        "extension_category_counts": dict(sorted(extension_categories.items())),
        "extension_source_groups": len({source_group(row) for row in selected}),
        "extension_split_counts": dict(sorted(Counter(split_name(row) for row in selected).items())),
        "duplicate_or_missing_evidence_holds": len(duplicate_holds),
        "duplicate_holds": str(paths["duplicate_holds_jsonl"]),
        "priority_contract": str(priority_path) if priority_path else "",
        "priority_contract_rows": len(priority_by_id),
        "priority_pending_rows": len(priority_pending_ids),
        "priority_direct_primary_rows": len(priority_coverage["direct_primary"]),
        "priority_specialist_rows": len(priority_coverage["specialist"]),
        "priority_exact_evidence_alias_rows": len(priority_coverage["exact_evidence_alias"]),
        "priority_covered_rows": counts["provenance_replacement_coverage_total"],
        "priority_uncovered_rows": len(priority_coverage["missing"]),
        "identity_overlap_with_prior": 0,
        "identity_overlap_with_specialist": 0,
        "identity_overlap_with_active_gold": 0,
        "distinct_assigned_record_ids": len(set(all_primary_ids) | specialist_ids),
        "eng_bench_sha256_before": gold_hash_before,
        "eng_bench_sha256_after": sha256_file(gold_path),
        "gold_rows_modified": 0,
    }
    if report["eng_bench_sha256_before"] != report["eng_bench_sha256_after"]:
        raise ValueError("active Gold changed while preparing the human handoff")
    write_json(paths["report_json"], report)
    paths["report_md"].parent.mkdir(parents=True, exist_ok=True)
    paths["report_md"].write_text(
        "# Primary Intern 4,000-Row Expansion\n\n"
        f"- Status: **{report['status']}**\n"
        f"- Primary rows: **{counts['total']}**\n"
        f"- Total human actions: **{counts['total_human_actions']}**\n"
        f"- New primary rows: **{counts['new_extension']}** "
        f"(MicroText {counts['new_extension_microtext']}, VisualDiff {counts['new_extension_visualdiff']})\n"
        f"- Mandatory engineering actions: **{counts['engineering_actions_total']}**\n"
        f"- New engineering deep-review actions: **{counts['new_extension']}**\n"
        "- Active Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
