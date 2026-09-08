#!/usr/bin/env python3
"""Expand the authoritative primary-intern workbook to 3,000 review rows.

The resulting payload also carries 18 distinct engineering-specialist actions:
15 VisualDiff English-description confirmations and three scarce-category
MicroText adjudications. Nothing produced by this tool is Gold-ready without a
completed human return and the normal machine promotion gates.
"""
from __future__ import annotations

import argparse
import hashlib
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
        sha256_file,
        source_group,
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
        sha256_file,
        source_group,
        split_name,
        task,
        write_jsonl,
    )
    from prepare_primary_intern_catchup_packet import payload_record, round_robin


EXPECTED_PRIOR_ROWS = 2_000
EXPECTED_PRIOR_ENGINEERING = 537
TARGET_PRIMARY_ROWS = 3_000
TARGET_NEW_PRIMARY_ROWS = TARGET_PRIMARY_ROWS - EXPECTED_PRIOR_ROWS
EXPECTED_SPECIALIST_VISUAL = 15
EXPECTED_SPECIALIST_MICRO = 3

SPECIALIST_ENGLISH_DRAFTS = {
    "vdiff__adafruit_feather_esp32_s2__original__to__rev_c__p0000__txt000":
        "The resistor label and value changed, with the previous 5.1K label replaced by 10K.",
    "vdiff__adafruit_feather_esp32_s2__original__to__rev_c__p0000__txt001":
        "The resistor reference and value annotation changed from R6 5.1K to R3G$2 10K.",
    "vdiff__adafruit_feather_esp32_s2__original__to__rev_c__p0000__txt002":
        "The schematic title changed from adafruit_feather_esp32_s2_original_sch to adafruit_feather_esp32_s2_rev_c_sch.",
    "vdiff__adafruit_feather_esp32_s2__original__to__rev_c__p0000__txt003":
        "The schematic title changed from adafruit_feather_esp32_s2_original_sch to adafruit_feather_esp32_s2_rev_c_sch.",
    "vdiff__adafruit_feather_esp32_s2__original__to__rev_c__p0000__txt004":
        "A resistor-network reference and a 10K value label were added in this schematic region.",
    "vdiff__adafruit_feather_rp2040__original__to__rev_b__p0000__txt000":
        "The TP3 TESTPOINT label near TR2 was removed.",
    "vdiff__adafruit_feather_rp2040__original__to__rev_b__p0000__txt001":
        "The TP1 and TP2 TESTPOINT labels were removed.",
    "vdiff__adafruit_feather_rp2040__original__to__rev_b__p0000__txt002":
        "The NEO_PWR net label was added near the +3V3 connection.",
    "vdiff__adafruit_feather_rp2040__original__to__rev_b__p0000__txt003":
        "Capacitor C17 with a value of 1 nF was added.",
    "vdiff__adafruit_feather_rp2040__original__to__rev_b__p0000__txt004":
        "The NEO_PWR net label was added between NEOPIX and SCK.",
    "vdiff__olimex_esp32_evb__rev_j__to__rev_k__p0000__txt000":
        "The GPIO label row was rearranged, moving GPIO2/GPIO5 and MTDO/GPIO15 to different label positions.",
    "vdiff__pixhawk_fmuv1__1_7__to__1_7_1__p0000__000":
        "The microSD connector reference and part label changed from U13/AMP 114-00841-68 to U$5/SF-MICROSD, and its pin numbering also changed.",
    "vdiff__pixhawk_fmuv1__1_7__to__1_7_1__p0002__000":
        "A BMA280 accelerometer circuit block and its connected net labels were added.",
    "vdiff__pixhawk_fmuv2__2_4_5__to__2_4_6__p0005__000":
        "The R620/Q601 transistor circuit was removed, leaving only the VDD_3V3_SENSORS net line.",
    "vdiff__pixhawk_fmuv2__2_4_5__to__2_4_6__p0005__001":
        "The resistor value annotation changed from 10R to 220R.",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolved(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def evidence_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flatten_specialist_visual(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wrapper in read_jsonl(path):
        row = dict(wrapper.get("row") or {})
        row["specialist_hold_reasons"] = list(wrapper.get("reasons") or [])
        row["specialist_source_path"] = str(wrapper.get("source_path") or "")
        rows.append(row)
    return rows


def choose_primary_extension(
    post_primary: list[dict[str, Any]],
    component_rows: list[dict[str, Any]],
    exact_rows: list[dict[str, Any]],
    substring_rows: list[dict[str, Any]],
    specialist_visual_ids: set[str],
    target_count: int = TARGET_NEW_PRIMARY_ROWS,
) -> tuple[list[dict[str, Any]], int]:
    post_primary = [row for row in post_primary if identifier(row) not in specialist_visual_ids]
    fixed = post_primary + exact_rows + substring_rows
    component_count = target_count - len(fixed)
    if component_count <= 0:
        raise ValueError("fixed extension rows leave no room for balanced component-value rows")
    selected_components = round_robin(component_rows, component_count)
    extension = fixed + selected_components
    if len(extension) != target_count:
        raise ValueError(f"expected {target_count} extension rows, got {len(extension)}")
    return extension, component_count


def deduplicate_prior_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Keep the first row for each rendered-evidence payload."""
    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for record in records:
        digest = evidence_sha256(Path(str(record["evidence_path"])))
        if digest in seen_hashes:
            held.append(
                {
                    "record_id": str(record["record_id"]),
                    "task": str(record["task"]),
                    "hold_reason": "duplicate_rendered_evidence_in_prior_primary",
                    "evidence_sha256": digest,
                    "safe_to_merge_gold": False,
                }
            )
            continue
        seen_hashes.add(digest)
        kept.append(record)
    return kept, held, seen_hashes


def primary_engineering_reason(row: dict[str, Any], exact_ids: set[str], substring_ids: set[str]) -> str:
    identity = identifier(row)
    if task(row) == "visualdiff":
        return (
            "判断红框内是否存在真实工程变化；核对类型和描述，并说明尺寸、连接、"
            "元件、工艺或版式层面的可复查依据"
        )
    if identity in exact_ids or identity in substring_ids:
        category = str(row.get("category") or "unknown_microtext")
        return f"新增平衡化候选：核对文字边界、{category} 类别和图纸语义，写出可复查依据"
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--prior-payload", required=True)
    parser.add_argument("--post-primary", required=True)
    parser.add_argument("--component-manifest", required=True)
    parser.add_argument("--exact-manifest", required=True)
    parser.add_argument("--substring-manifest", required=True)
    parser.add_argument("--specialist-visual", required=True)
    parser.add_argument("--specialist-micro", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--specialist-evidence-dir", required=True)
    parser.add_argument("--pool-jsonl", required=True)
    parser.add_argument("--duplicate-holds-jsonl", required=True)
    parser.add_argument("--payload-json", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    paths = {
        key: resolved(root, value)
        for key, value in {
            "prior_payload": args.prior_payload,
            "post_primary": args.post_primary,
            "component_manifest": args.component_manifest,
            "exact_manifest": args.exact_manifest,
            "substring_manifest": args.substring_manifest,
            "specialist_visual": args.specialist_visual,
            "specialist_micro": args.specialist_micro,
            "evidence_dir": args.evidence_dir,
            "specialist_evidence_dir": args.specialist_evidence_dir,
            "pool_jsonl": args.pool_jsonl,
            "duplicate_holds_jsonl": args.duplicate_holds_jsonl,
            "payload_json": args.payload_json,
            "report_json": args.report_json,
            "report_md": args.report_md,
        }.items()
    }
    for output_key in (
        "evidence_dir", "specialist_evidence_dir", "pool_jsonl", "duplicate_holds_jsonl", "payload_json",
        "report_json", "report_md",
    ):
        if paths[output_key].exists():
            raise FileExistsError(f"output already exists: {paths[output_key]}")

    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = sha256_file(gold_path)
    prior_payload = json.loads(paths["prior_payload"].read_text(encoding="utf-8"))
    prior_records = list(prior_payload.get("rows") or [])
    if len(prior_records) != EXPECTED_PRIOR_ROWS:
        raise ValueError(f"expected {EXPECTED_PRIOR_ROWS} prior rows, got {len(prior_records)}")
    if sum(bool(row.get("engineering_required")) for row in prior_records) != EXPECTED_PRIOR_ENGINEERING:
        raise ValueError("prior engineering count changed")
    prior_pool_path = Path(prior_payload["inputs"]["pool"])
    if not prior_pool_path.is_absolute():
        prior_pool_path = root / prior_pool_path
    if sha256_file(prior_pool_path) != prior_payload["inputs"]["pool_sha256"]:
        raise ValueError("prior pool hash does not match prior payload")
    prior_pool = read_jsonl(prior_pool_path)
    prior_by_id = {identifier(row): row for row in prior_pool}
    prior_ids = {str(row["record_id"]) for row in prior_records}
    if len(prior_ids) != EXPECTED_PRIOR_ROWS or set(prior_by_id) != prior_ids:
        raise ValueError("prior payload and prior pool identities do not match")
    prior_records, duplicate_holds, seen_evidence_hashes = deduplicate_prior_records(prior_records)
    carried_ids = {str(row["record_id"]) for row in prior_records}

    specialist_visual_rows = flatten_specialist_visual(paths["specialist_visual"])
    specialist_micro_rows = read_jsonl(paths["specialist_micro"])
    if len(specialist_visual_rows) != EXPECTED_SPECIALIST_VISUAL:
        raise ValueError("specialist VisualDiff input must contain exactly 15 rows")
    if len(specialist_micro_rows) != EXPECTED_SPECIALIST_MICRO:
        raise ValueError("specialist MicroText input must contain exactly 3 rows")
    specialist_visual_ids = {identifier(row) for row in specialist_visual_rows}
    if specialist_visual_ids != set(SPECIALIST_ENGLISH_DRAFTS):
        raise ValueError("specialist VisualDiff identities no longer match the reviewed English task")

    post_primary = read_jsonl(paths["post_primary"])
    component_rows = read_jsonl(paths["component_manifest"])
    exact_rows = read_jsonl(paths["exact_manifest"])
    substring_rows = read_jsonl(paths["substring_manifest"])
    post_primary = [row for row in post_primary if identifier(row) not in specialist_visual_ids]
    candidate_pool = post_primary + exact_rows + substring_rows + round_robin(component_rows, len(component_rows))
    candidate_ids = [identifier(row) for row in candidate_pool]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("primary extension candidate pool contains duplicate identities")
    specialist_rows = specialist_visual_rows + specialist_micro_rows
    specialist_ids = [identifier(row) for row in specialist_rows]
    if set(candidate_ids) & set(specialist_ids):
        raise ValueError("primary candidates overlap the specialist assignment")
    if prior_ids & (set(candidate_ids) | set(specialist_ids)):
        raise ValueError("new assignments overlap the previous 2,000-row primary workbook")
    active_overlap = sorted((set(candidate_ids) | set(specialist_ids)) & active_gold_ids(root))
    if active_overlap:
        raise ValueError(f"new assignments overlap active Gold: {active_overlap[:5]}")
    missing = [identifier(row) for row in candidate_pool + specialist_rows if not evidence_materializable(root, row)]
    if missing:
        raise ValueError(f"evidence cannot be materialized for {len(missing)} rows: {missing[:5]}")

    combined_pool: list[dict[str, Any]] = []
    payload_rows: list[dict[str, Any]] = []
    for new_index, record in enumerate(prior_records, 1):
        source = dict(prior_by_id[record["record_id"]])
        source["primary_pool_index"] = new_index
        source["primary_pool_status"] = "issued_primary_3000"
        source["safe_to_merge_gold"] = False
        combined_pool.append(source)
        carried = dict(record)
        carried["primary_index"] = new_index
        carried["carried_from_previous_workbook"] = True
        payload_rows.append(carried)

    exact_ids = {identifier(row) for row in exact_rows}
    substring_ids = {identifier(row) for row in substring_rows}
    paths["evidence_dir"].mkdir(parents=True)
    target_extension_count = TARGET_PRIMARY_ROWS - len(payload_rows)
    selected_extension: list[dict[str, Any]] = []
    for attempt, row in enumerate(candidate_pool, 1):
        if len(selected_extension) >= target_extension_count:
            break
        position = len(payload_rows) + 1
        suffix = identifier(row).replace("/", "_")[-48:]
        output = paths["evidence_dir"] / f"primary_{position:04d}_{task(row)}_{suffix}.png"
        if task(row) == "microtext":
            render_micro_evidence(root, row, output)
        else:
            render_visual_evidence(root, row, output)
        digest = evidence_sha256(output)
        if digest in seen_evidence_hashes:
            duplicate_holds.append(
                {
                    "record_id": identifier(row),
                    "task": task(row),
                    "hold_reason": "duplicate_rendered_evidence_against_primary_assignment",
                    "evidence_sha256": digest,
                    "safe_to_merge_gold": False,
                }
            )
            output.unlink()
            continue
        seen_evidence_hashes.add(digest)
        source = dict(row)
        source["primary_pool_index"] = position
        source["primary_pool_status"] = "issued_primary_3000"
        source["primary_pool_target"] = "Gold v2.0 Global"
        source["primary_pool_capacity_cohort"] = str(
            row.get("canonical_capacity_origin_cohort")
            or row.get("capacity_cohort")
            or "wave250_primary_expansion"
        )
        source["safe_to_merge_gold"] = False
        combined_pool.append(source)
        selected_extension.append(row)
        reason = primary_engineering_reason(row, exact_ids, substring_ids)
        record = payload_record(source, output, reason, False)
        record["carried_from_previous_workbook"] = False
        record["provenance_replacement"] = bool(row.get("provenance_replacement_candidate"))
        record["mandatory_description_rewrite"] = False
        record["evidence_sha256"] = digest
        payload_rows.append(record)
        if len(selected_extension) % 100 == 0:
            print(f"rendered primary extension {len(selected_extension)}/{target_extension_count}")
    if len(selected_extension) != target_extension_count:
        raise ValueError(
            f"only selected {len(selected_extension)} of {target_extension_count} globally unique extension rows"
        )
    write_jsonl(paths["pool_jsonl"], combined_pool)
    write_jsonl(paths["duplicate_holds_jsonl"], duplicate_holds)

    engineering_rows = [row for row in payload_rows if row.get("engineering_required")]
    for engineering_index, row in enumerate(engineering_rows, 1):
        row["engineering_index"] = engineering_index

    paths["specialist_evidence_dir"].mkdir(parents=True)
    specialist_visual_payload: list[dict[str, Any]] = []
    for index, row in enumerate(specialist_visual_rows, 1):
        identity = identifier(row)
        output = paths["specialist_evidence_dir"] / f"specialist_visual_{index:02d}_{identity[-44:]}.png"
        render_visual_evidence(root, row, output)
        digest = evidence_sha256(output)
        if digest in seen_evidence_hashes:
            raise ValueError(f"specialist evidence duplicates a primary assignment: {identity}")
        seen_evidence_hashes.add(digest)
        reasons = list(row.get("specialist_hold_reasons") or [])
        specialist_visual_payload.append(
            {
                "specialist_index": index,
                "task": "visualdiff_english",
                "record_id": identity,
                "pair_id": identity,
                "source_group": source_group(row),
                "reserved_split": split_name(row),
                "evidence_path": str(output.resolve()),
                "evidence_sha256": digest,
                "chinese_description": str(row.get("human_description") or row.get("description") or ""),
                "proposed_english_description": SPECIALIST_ENGLISH_DRAFTS[identity],
                "current_change_type": str(row.get("change_type") or ""),
                "type_required": "unresolved_visualdiff_change_type" in reasons,
                "old_text": str(row.get("old_text") or ""),
                "new_text": str(row.get("new_text") or ""),
                "safe_to_merge_gold": False,
            }
        )

    specialist_micro_payload: list[dict[str, Any]] = []
    for index, row in enumerate(specialist_micro_rows, 1):
        identity = identifier(row)
        output = paths["specialist_evidence_dir"] / f"specialist_micro_{index:02d}_{identity[-44:]}.png"
        render_micro_evidence(root, row, output)
        digest = evidence_sha256(output)
        if digest in seen_evidence_hashes:
            raise ValueError(f"specialist evidence duplicates a primary assignment: {identity}")
        seen_evidence_hashes.add(digest)
        specialist_micro_payload.append(
            {
                "specialist_index": index,
                "task": "microtext_balance",
                "record_id": identity,
                "candidate_id": identity,
                "source_group": source_group(row),
                "reserved_split": split_name(row),
                "evidence_path": str(output.resolve()),
                "evidence_sha256": digest,
                "proposed_text": str(row.get("proposed_text") or row.get("target_text") or ""),
                "category": str(row.get("category") or "unknown_microtext"),
                "safe_to_merge_gold": False,
            }
        )

    primary_task_counts = Counter(row["task"] for row in payload_rows)
    primary_engineering_counts = Counter(row["task"] for row in engineering_rows)
    counts = {
        "total": len(payload_rows),
        "total_human_actions": len(payload_rows) + len(specialist_rows),
        "microtext": primary_task_counts["microtext"],
        "visualdiff": primary_task_counts["visualdiff"],
        "engineering_total": len(engineering_rows),
        "engineering_microtext": primary_engineering_counts["microtext"],
        "engineering_visualdiff": primary_engineering_counts["visualdiff"],
        "engineering_actions_total": len(engineering_rows) + len(specialist_rows),
        "mandatory_description_rewrite": sum(
            bool(row.get("mandatory_description_rewrite")) for row in payload_rows
        ),
        "carried_from_previous_workbook": len(prior_records),
        "prior_duplicate_rows_removed": EXPECTED_PRIOR_ROWS - len(prior_records),
        "new_extension": len(selected_extension),
        "new_component_values": sum(
            str(row.get("category") or "") == "component_value" for row in selected_extension
        ),
        "specialist_total": len(specialist_rows),
        "specialist_visualdiff_english": len(specialist_visual_payload),
        "specialist_microtext_balance": len(specialist_micro_payload),
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "superseding 3,000-row primary catch-up plus 18 engineering-specialist actions",
        "rows": payload_rows,
        "specialist": {
            "visualdiff_english": specialist_visual_payload,
            "microtext_balance": specialist_micro_payload,
        },
        "counts": counts,
        "inputs": {
            key: {"path": str(paths[key]), "sha256": sha256_file(paths[key])}
            for key in (
                "prior_payload", "post_primary", "component_manifest", "exact_manifest",
                "substring_manifest", "specialist_visual", "specialist_micro",
            )
        } | {"pool": str(paths["pool_jsonl"]), "pool_sha256": sha256_file(paths["pool_jsonl"])},
        "safety": {
            "gold_rows_modified": 0,
            "all_rows_safe_to_merge_gold": False,
            "promotion_requires_completed_human_return_and_machine_gates": True,
            "specialist_rows_are_distinct_from_primary_rows": True,
        },
    }
    write_json(paths["payload_json"], payload)

    all_new_ids = [identifier(row) for row in selected_extension] + specialist_ids
    all_evidence_hashes = [
        str(row.get("evidence_sha256") or evidence_sha256(Path(row["evidence_path"])))
        for row in payload_rows + specialist_visual_payload + specialist_micro_payload
    ]
    duplicate_evidence_hashes = [
        value for value, count in Counter(all_evidence_hashes).items() if value and count > 1
    ]
    report = {
        "status": "PASS" if not duplicate_evidence_hashes else "FAIL",
        "goal": "Gold v2.0 Global",
        "pool": str(paths["pool_jsonl"]),
        "pool_sha256": sha256_file(paths["pool_jsonl"]),
        "payload": str(paths["payload_json"]),
        "payload_sha256": sha256_file(paths["payload_json"]),
        "counts": counts,
        "primary_split_counts": dict(sorted(Counter(row["reserved_split"] for row in payload_rows).items())),
        "new_primary_category_counts": dict(
            sorted(Counter(str(row.get("category") or "visualdiff") for row in payload_rows[EXPECTED_PRIOR_ROWS:]).items())
        ),
        "new_primary_source_groups": len({row["source_group"] for row in payload_rows[EXPECTED_PRIOR_ROWS:]}),
        "distinct_assigned_record_ids": len(carried_ids | set(all_new_ids)),
        "identity_overlap_with_prior": 0,
        "identity_overlap_with_active_gold": 0,
        "specialist_overlap_with_primary": 0,
        "duplicate_rows_held": len(duplicate_holds),
        "duplicate_holds": str(paths["duplicate_holds_jsonl"]),
        "duplicate_holds_sha256": sha256_file(paths["duplicate_holds_jsonl"]),
        "duplicate_evidence_hashes": duplicate_evidence_hashes,
        "eng_bench_sha256_before": gold_hash_before,
        "eng_bench_sha256_after": sha256_file(gold_path),
        "gold_rows_modified": 0,
    }
    write_json(paths["report_json"], report)
    paths["report_md"].parent.mkdir(parents=True, exist_ok=True)
    paths["report_md"].write_text(
        "# Primary Intern 3,000-Row Expansion\n\n"
        f"- Status: **{report['status']}**\n"
        f"- Primary rows: **{counts['total']}** (MicroText {counts['microtext']}, VisualDiff {counts['visualdiff']})\n"
        f"- Carried prior rows after evidence dedup: **{counts['carried_from_previous_workbook']}**\n"
        f"- Prior duplicate rows removed: **{counts['prior_duplicate_rows_removed']}**\n"
        f"- New primary rows: **{counts['new_extension']}**\n"
        f"- Primary engineering deep-review rows: **{counts['engineering_total']}**\n"
        f"- Separate specialist actions: **{counts['specialist_total']}**\n"
        f"- Total human actions: **{counts['total_human_actions']}**\n"
        "- Active Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
