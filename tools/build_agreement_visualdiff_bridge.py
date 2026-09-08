#!/usr/bin/env python3
"""Reserve release-safe VisualDiff rows that close the v2 agreement shortfall.

This command builds a machine plan only. It never issues auditor workbooks and
never mutates Gold. Rows already covered by completed auditor decisions or by
the pending auditor assignment embedded in the primary payload are excluded.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.prepare_incremental_human_audit_round import (
    active_gold_ids,
    identifier,
    read_jsonl,
    sha256_file,
    write_jsonl,
)


def resolved(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def decision_ids(paths: Iterable[Path]) -> set[str]:
    identities: set[str] = set()
    for path in paths:
        for row in read_jsonl(path):
            identity = str(
                row.get("record_id") or row.get("candidate_id") or row.get("pair_id") or ""
            )
            if identity:
                identities.add(identity)
    return identities


def diverse_visual_order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        family = str(row.get("project_id") or "")
        if family:
            grouped[family].append(row)
    buckets = {
        family: deque(sorted(values, key=identifier))
        for family, values in sorted(grouped.items())
    }
    active = deque(buckets)
    ordered: list[dict[str, Any]] = []
    while active:
        family = active.popleft()
        ordered.append(buckets[family].popleft())
        if buckets[family]:
            active.append(family)
    return ordered


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--auditor-decisions", action="append", default=[])
    parser.add_argument("--visual-target", type=int, default=90)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    payload_path = resolved(root, args.payload)
    pool_path = resolved(root, args.pool)
    decision_paths = [resolved(root, value) for value in args.auditor_decisions]
    output_jsonl = resolved(root, args.output_jsonl)
    report_json = resolved(root, args.report_json)
    report_md = resolved(root, args.report_md)
    for path in (output_jsonl, report_json, report_md):
        if path.exists():
            raise FileExistsError(f"output already exists: {path}")
    if args.visual_target <= 0:
        raise ValueError("visual target must be positive")

    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = sha256_file(gold_path)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload_rows = list(payload.get("rows") or [])
    payload_by_id = {str(row.get("record_id") or ""): row for row in payload_rows}
    if len(payload_by_id) != len(payload_rows) or "" in payload_by_id:
        raise ValueError("primary payload contains duplicate or blank record IDs")
    pool_rows = read_jsonl(pool_path)
    pool_by_id = {identifier(row): row for row in pool_rows if identifier(row)}
    if len(pool_by_id) != len(pool_rows):
        raise ValueError("primary pool contains duplicate or blank identities")

    completed_ids = decision_ids(decision_paths)
    pending_ids = {
        identity for identity, row in payload_by_id.items() if row.get("auditor_overlap")
    }
    occupied_ids = completed_ids | pending_ids
    explicitly_release_safe_overlap = {
        identity
        for identity in occupied_ids
        if identity in payload_by_id
        and payload_by_id[identity].get("task") == "visualdiff"
        and payload_by_id[identity].get("reserved_split") in {"dev", "test"}
        and identity in pool_by_id
        and pool_by_id[identity].get("source_rights_check") == "release_safe_status"
    }
    shortfall = max(0, args.visual_target - len(explicitly_release_safe_overlap))

    gold_ids = active_gold_ids(root)
    eligible: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()
    for identity, payload_row in payload_by_id.items():
        pool_row = pool_by_id.get(identity)
        if payload_row.get("task") != "visualdiff":
            exclusion_counts["not_visualdiff"] += 1
        elif payload_row.get("reserved_split") not in {"dev", "test"}:
            exclusion_counts["not_dev_or_test"] += 1
        elif identity in occupied_ids:
            exclusion_counts["already_completed_or_pending_audit"] += 1
        elif identity in gold_ids:
            exclusion_counts["already_active_gold"] += 1
        elif pool_row is None:
            exclusion_counts["missing_pool_row"] += 1
        elif pool_row.get("source_rights_check") != "release_safe_status":
            exclusion_counts["rights_not_explicitly_release_safe"] += 1
        elif not Path(str(payload_row.get("evidence_path") or "")).is_file():
            exclusion_counts["primary_evidence_missing"] += 1
        elif not all(
            (root / str(pool_row.get(field) or "")).is_file()
            for field in ("image_old", "image_new")
        ):
            exclusion_counts["source_pair_image_missing"] += 1
        else:
            eligible.append(pool_row)

    ordered = diverse_visual_order(eligible)
    if len(ordered) < shortfall:
        raise ValueError(f"only {len(ordered)} eligible VisualDiff rows for shortfall {shortfall}")
    selected = ordered[:shortfall]
    selected_ids = {identifier(row) for row in selected}
    if len(selected_ids) != len(selected):
        raise ValueError("selected bridge contains duplicate identities")
    if selected_ids & occupied_ids or selected_ids & gold_ids:
        raise ValueError("selected bridge overlaps existing audit or active Gold")

    bridge_rows: list[dict[str, Any]] = []
    for index, row in enumerate(selected, 1):
        identity = identifier(row)
        payload_row = payload_by_id[identity]
        bridge = dict(row)
        bridge.update(
            {
                "agreement_bridge_index": index,
                "agreement_bridge_status": "reserved_machine_plan_not_issued",
                "agreement_bridge_target": "Gold v2.0 independent agreement VisualDiff 90",
                "primary_index": payload_row.get("primary_index"),
                "primary_evidence_path": payload_row.get("evidence_path"),
                "primary_workbook_assigned": True,
                "independent_auditor_review_required": True,
                "safe_to_merge_gold": False,
            }
        )
        bridge_rows.append(bridge)
    write_jsonl(output_jsonl, bridge_rows)

    completed_in_payload = completed_ids & set(payload_by_id)
    pending_in_payload = pending_ids & set(payload_by_id)
    report = {
        "status": "PASS",
        "goal": "Gold v2.0 Global",
        "workflow": "machine-only agreement VisualDiff bridge reservation",
        "issuance_state": "not_issued_wait_for_current_auditor_round",
        "visual_target": args.visual_target,
        "explicit_release_safe_visual_overlap_before_bridge": len(
            explicitly_release_safe_overlap
        ),
        "visual_shortfall": shortfall,
        "bridge_rows": len(bridge_rows),
        "bridge_families": len({str(row.get("project_id") or "") for row in bridge_rows}),
        "bridge_source_candidates": dict(
            sorted(Counter(str(row.get("source_candidate_id") or "") for row in bridge_rows).items())
        ),
        "bridge_split_counts": dict(
            sorted(Counter(str(row.get("reserved_split") or "") for row in bridge_rows).items())
        ),
        "eligible_rows": len(eligible),
        "eligible_families": len({str(row.get("project_id") or "") for row in eligible}),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "completed_auditor_decision_files": [str(path) for path in decision_paths],
        "completed_unique_ids": len(completed_ids),
        "completed_ids_in_primary_payload": len(completed_in_payload),
        "pending_auditor_ids_in_primary_payload": len(pending_in_payload),
        "output_jsonl": str(output_jsonl),
        "output_sha256": sha256_file(output_jsonl),
        "eng_bench_sha256_before": gold_hash_before,
        "eng_bench_sha256_after": sha256_file(gold_path),
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "next_action": (
            "After the current auditor round returns, issue these rows to independent auditors; "
            "after primary completion, join decisions and run provenance/promotion gates before "
            "building the frozen 95 MicroText / 90 VisualDiff agreement sample."
        ),
    }
    if report["eng_bench_sha256_before"] != report["eng_bench_sha256_after"]:
        raise ValueError("active Gold changed while reserving agreement bridge rows")
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(
        "# Agreement VisualDiff Bridge\n\n"
        f"- Goal: **{report['goal']}**\n"
        f"- Status: **{report['status']}**\n"
        f"- Issuance: **{report['issuance_state']}**\n"
        f"- Explicit release-safe VisualDiff overlap before bridge: "
        f"**{report['explicit_release_safe_visual_overlap_before_bridge']}/{args.visual_target}**\n"
        f"- Reserved bridge rows: **{report['bridge_rows']}** across "
        f"**{report['bridge_families']}** families\n"
        "- Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
