#!/usr/bin/env python3
"""Extend the primary intern workbook with provenance-critical review rows."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .prepare_incremental_human_audit_round import (
        active_gold_ids,
        evidence_materializable,
        identifier,
        read_jsonl,
        render_micro_evidence,
        render_visual_evidence,
        source_group,
        sha256_file,
        split_name,
        task,
        write_jsonl,
    )
    from .prepare_primary_intern_catchup_packet import payload_record, round_robin
except ImportError:
    from prepare_incremental_human_audit_round import (
        active_gold_ids,
        evidence_materializable,
        identifier,
        read_jsonl,
        render_micro_evidence,
        render_visual_evidence,
        source_group,
        sha256_file,
        split_name,
        task,
        write_jsonl,
    )
    from prepare_primary_intern_catchup_packet import payload_record, round_robin


DEFAULT_MICRO_EXTENSION = 163
EXPECTED_PRIOR_ROWS = 1500
EXPECTED_PRIOR_ENGINEERING = 200
EXPECTED_MANDATORY_REWRITES = 337


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_micro_extension(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Keep all scarce non-pin rows, then source-balance the remaining pin rows."""
    scarce = [row for row in rows if str(row.get("category") or "") != "pin_label"]
    if len(scarce) > count:
        return round_robin(scarce, count)
    selected = list(scarce)
    selected_ids = {identifier(row) for row in selected}
    pin_rows = [row for row in rows if identifier(row) not in selected_ids]
    selected.extend(round_robin(pin_rows, count - len(selected)))
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--prior-payload", required=True)
    parser.add_argument("--micro-manifest", required=True)
    parser.add_argument("--visual-manifest", required=True)
    parser.add_argument("--micro-extension", type=int, default=DEFAULT_MICRO_EXTENSION)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--pool-jsonl", required=True)
    parser.add_argument("--payload-json", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args()


def resolved(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    prior_payload_path = resolved(root, args.prior_payload)
    micro_manifest_path = resolved(root, args.micro_manifest)
    visual_manifest_path = resolved(root, args.visual_manifest)
    evidence_dir = resolved(root, args.evidence_dir)
    pool_path = resolved(root, args.pool_jsonl)
    payload_path = resolved(root, args.payload_json)
    report_path = resolved(root, args.report_json)
    report_md_path = resolved(root, args.report_md)

    for output in (evidence_dir, pool_path, payload_path, report_path, report_md_path):
        if output.exists():
            raise FileExistsError(f"output already exists: {output}")

    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = sha256_file(gold_path)
    prior_payload = json.loads(prior_payload_path.read_text(encoding="utf-8"))
    prior_records = list(prior_payload.get("rows") or [])
    if len(prior_records) != EXPECTED_PRIOR_ROWS:
        raise ValueError(f"expected {EXPECTED_PRIOR_ROWS} prior payload rows, got {len(prior_records)}")
    if sum(bool(row.get("engineering_required")) for row in prior_records) != EXPECTED_PRIOR_ENGINEERING:
        raise ValueError("prior payload engineering count changed")

    prior_pool_path = Path(prior_payload["inputs"]["pool"])
    if not prior_pool_path.is_absolute():
        prior_pool_path = root / prior_pool_path
    if sha256_file(prior_pool_path) != prior_payload["inputs"]["pool_sha256"]:
        raise ValueError("prior pool hash does not match prior payload")
    prior_pool = read_jsonl(prior_pool_path)
    prior_by_id = {identifier(row): row for row in prior_pool}
    prior_ids = {str(row["record_id"]) for row in prior_records}
    if len(prior_ids) != EXPECTED_PRIOR_ROWS or set(prior_by_id) != prior_ids:
        raise ValueError("prior payload and source pool identities do not match")

    micro_rows = read_jsonl(micro_manifest_path)
    visual_rows = read_jsonl(visual_manifest_path)
    mandatory_visual = [row for row in visual_rows if bool(row.get("description_rewrite_required"))]
    if len(mandatory_visual) != EXPECTED_MANDATORY_REWRITES:
        raise ValueError(
            f"expected {EXPECTED_MANDATORY_REWRITES} mandatory rewrites, got {len(mandatory_visual)}"
        )
    selected_micro = select_micro_extension(micro_rows, args.micro_extension)
    extension = selected_micro + mandatory_visual
    extension_ids = [identifier(row) for row in extension]
    if len(extension_ids) != len(set(extension_ids)):
        raise ValueError("extension contains duplicate identities")
    overlap = sorted(prior_ids & set(extension_ids))
    if overlap:
        raise ValueError(f"extension overlaps prior primary assignment: {overlap[:5]}")
    active_overlap = sorted(set(extension_ids) & active_gold_ids(root))
    if active_overlap:
        raise ValueError(f"extension overlaps active Gold identities: {active_overlap[:5]}")
    blocked = [identifier(row) for row in extension if not evidence_materializable(root, row)]
    if blocked:
        raise ValueError(f"extension evidence cannot be materialized: {blocked[:5]}")

    prior_fingerprints = {
        str(row.get("replacement_evidence_fingerprint") or "")
        for row in prior_pool
        if row.get("replacement_evidence_fingerprint")
    }
    extension_fingerprints = [
        str(row.get("replacement_evidence_fingerprint") or "") for row in extension
    ]
    if any(not value for value in extension_fingerprints):
        raise ValueError("extension row is missing a replacement evidence fingerprint")
    if len(extension_fingerprints) != len(set(extension_fingerprints)):
        raise ValueError("extension contains duplicate evidence fingerprints")
    fingerprint_overlap = sorted(prior_fingerprints & set(extension_fingerprints))
    if fingerprint_overlap:
        raise ValueError(f"extension evidence overlaps prior assignment: {fingerprint_overlap[:3]}")

    combined_pool: list[dict[str, Any]] = []
    for index, record in enumerate(prior_records, 1):
        source = dict(prior_by_id[record["record_id"]])
        source["primary_pool_index"] = index
        source["primary_pool_status"] = "issued_primary_2000"
        source["safe_to_merge_gold"] = False
        combined_pool.append(source)
    for offset, row in enumerate(extension, EXPECTED_PRIOR_ROWS + 1):
        source = dict(row)
        source["primary_pool_index"] = offset
        source["primary_pool_status"] = "issued_primary_2000"
        source["primary_pool_target"] = "Gold v2.0 Global"
        source["primary_pool_capacity_cohort"] = str(
            row.get("canonical_capacity_origin_cohort") or "provenance_replacement_continuation"
        )
        source["safe_to_merge_gold"] = False
        combined_pool.append(source)
    write_jsonl(pool_path, combined_pool)

    payload_rows: list[dict[str, Any]] = []
    for record in prior_records:
        carried = dict(record)
        carried["carried_from_previous_workbook"] = True
        source = prior_by_id[record["record_id"]]
        carried["provenance_replacement"] = bool(source.get("provenance_replacement_candidate"))
        carried["mandatory_description_rewrite"] = False
        if not Path(str(carried.get("evidence_path") or "")).is_file():
            raise FileNotFoundError(f"prior embedded evidence source is missing: {carried['record_id']}")
        payload_rows.append(carried)

    evidence_dir.mkdir(parents=True)
    engineering_reason = (
        "【强制工程描述重写】判断红框内真实工程变化；必须选择2，填写规范变化类型、"
        "具体对象变化描述和可复查工程依据"
    )
    for position, row in enumerate(extension, EXPECTED_PRIOR_ROWS + 1):
        suffix = identifier(row).replace("/", "_")[-48:]
        output = evidence_dir / f"primary_{position:04d}_{task(row)}_{suffix}.png"
        if task(row) == "microtext":
            render_micro_evidence(root, row, output)
            reason = ""
        else:
            render_visual_evidence(root, row, output)
            reason = engineering_reason
        source = combined_pool[position - 1]
        record = payload_record(source, output, reason, False)
        record["carried_from_previous_workbook"] = False
        record["provenance_replacement"] = True
        record["mandatory_description_rewrite"] = task(row) == "visualdiff"
        payload_rows.append(record)
        if (position - EXPECTED_PRIOR_ROWS) % 100 == 0:
            print(f"rendered extension {position - EXPECTED_PRIOR_ROWS}/{len(extension)}")

    engineering_rows = [row for row in payload_rows if row.get("engineering_required")]
    for index, row in enumerate(engineering_rows, 1):
        row["engineering_index"] = index

    task_counts = Counter(row["task"] for row in payload_rows)
    split_counts = Counter(row["reserved_split"] for row in payload_rows)
    engineering_task_counts = Counter(row["task"] for row in engineering_rows)
    counts = {
        "total": len(payload_rows),
        "microtext": task_counts["microtext"],
        "visualdiff": task_counts["visualdiff"],
        "engineering_total": len(engineering_rows),
        "engineering_microtext": engineering_task_counts["microtext"],
        "engineering_visualdiff": engineering_task_counts["visualdiff"],
        "mandatory_description_rewrite": sum(
            bool(row.get("mandatory_description_rewrite")) for row in payload_rows
        ),
        "auditor_overlap": sum(bool(row.get("auditor_overlap")) for row in payload_rows),
        "carried_from_previous_workbook": EXPECTED_PRIOR_ROWS,
        "new_extension": len(extension),
        "provenance_replacement": sum(
            bool(row.get("provenance_replacement")) for row in payload_rows
        ),
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "superseding primary catch-up plus mandatory engineering adjudication",
        "rows": payload_rows,
        "counts": counts,
        "inputs": {
            "pool": str(pool_path),
            "pool_sha256": sha256_file(pool_path),
            "prior_payload": str(prior_payload_path),
            "prior_payload_sha256": sha256_file(prior_payload_path),
            "micro_manifest": str(micro_manifest_path),
            "micro_manifest_sha256": sha256_file(micro_manifest_path),
            "visual_manifest": str(visual_manifest_path),
            "visual_manifest_sha256": sha256_file(visual_manifest_path),
        },
        "safety": {
            "gold_rows_modified": 0,
            "all_rows_safe_to_merge_gold": False,
            "promotion_requires_completed_human_return_and_machine_gates": True,
        },
    }
    write_json(payload_path, payload)

    report = {
        "status": "PASS",
        "goal": "Gold v2.0 Global",
        "pool": str(pool_path),
        "pool_sha256": sha256_file(pool_path),
        "payload": str(payload_path),
        "payload_sha256": sha256_file(payload_path),
        "evidence_dir": str(evidence_dir),
        "new_evidence_pngs": len(list(evidence_dir.glob("*.png"))),
        "counts": counts,
        "split_counts": dict(sorted(split_counts.items())),
        "micro_extension_categories": dict(
            sorted(Counter(str(row.get("category") or "") for row in selected_micro).items())
        ),
        "micro_extension_source_units": len({source_group(row) for row in selected_micro}),
        "mandatory_visual_families": len({str(row.get("project_id") or "") for row in mandatory_visual}),
        "identity_overlap_with_prior": 0,
        "evidence_fingerprint_overlap_with_prior": 0,
        "active_gold_identity_overlap": 0,
        "eng_bench_sha256_before": gold_hash_before,
        "eng_bench_sha256_after": sha256_file(gold_path),
        "gold_rows_modified": 0,
    }
    write_json(report_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(
        "# Primary Intern 2,000-Row Catch-up Preparation\n\n"
        f"- Status: **{report['status']}**\n"
        f"- Rows: **{counts['total']}** (MicroText {counts['microtext']}, VisualDiff {counts['visualdiff']})\n"
        f"- Carried from superseded workbook: **{counts['carried_from_previous_workbook']}**\n"
        f"- New release-critical rows: **{counts['new_extension']}**\n"
        f"- Mandatory engineering rows: **{counts['engineering_total']}**\n"
        f"- Mandatory VisualDiff description rewrites: **{counts['mandatory_description_rewrite']}**\n"
        "- Active Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
