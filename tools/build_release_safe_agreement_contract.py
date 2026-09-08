#!/usr/bin/env python3
"""Freeze a release-safe agreement contract while minimizing new human work.

The contract reuses completed independent-auditor decisions, retains already
issued auditor-overlap work, and adds bridge rows only for the remaining task
quota. It never treats machine checks as human review and never mutates Gold.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_provenance_replacement_plan import (
    candidate_evidence_fingerprint,
    candidate_is_release_safe,
    candidate_source_docs,
    manifest_pair_docs,
)
from prepare_incremental_human_audit_round import active_gold_ids


CHANNEL_PRIORITY = {
    "completed_independent_screen": 0,
    "pending_issued_independent_screen": 1,
    "new_independent_screen_required": 2,
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected JSON object")
        rows.append(value)
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def identity(row: dict[str, Any]) -> str:
    return str(
        row.get("record_id") or row.get("candidate_id") or row.get("pair_id") or ""
    ).strip()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def stable_key(row: dict[str, Any], seed: str) -> str:
    value = f"{seed}:{identity(row)}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def diverse_order(rows: Iterable[dict[str, Any]], task: str, seed: str) -> list[dict[str, Any]]:
    """Round-robin task strata, then source families, deterministically."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if task == "visualdiff":
            group = str(row.get("project_id") or row.get("source_candidate_id") or "")
        else:
            group = "|".join(
                [
                    str(row.get("reserved_split") or ""),
                    str(row.get("category") or "unknown"),
                    str(row.get("doc_id") or row.get("source_candidate_id") or ""),
                ]
            )
        if group:
            groups[group].append(row)
    buckets = {
        group: deque(sorted(values, key=lambda value: stable_key(value, seed)))
        for group, values in sorted(groups.items())
    }
    active = deque(buckets)
    ordered: list[dict[str, Any]] = []
    while active:
        group = active.popleft()
        ordered.append(buckets[group].popleft())
        if buckets[group]:
            active.append(group)
    return ordered


def unique_mapping(rows: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = identity(row)
        if not row_id:
            raise ValueError(f"{label} contains blank identity")
        if row_id in result:
            raise ValueError(f"{label} contains duplicate identity: {row_id}")
        result[row_id] = row
    return result


def source_payloads(
    root: Path,
    docs: set[str],
    manifest_docs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for doc_id in sorted(docs):
        if doc_id in cache:
            output[doc_id] = cache[doc_id]
            continue
        manifest = manifest_docs.get(doc_id)
        if manifest is None:
            raise ValueError(f"manifest document missing: {doc_id}")
        relative_path = str(manifest.get("path") or "").strip()
        recorded = str(manifest.get("sha256") or "").strip().lower()
        path = root / relative_path
        if not relative_path or not recorded or not path.is_file():
            raise ValueError(f"source payload incomplete: {doc_id}")
        actual = file_sha256(path)
        if actual.lower() != recorded:
            raise ValueError(f"source payload hash mismatch: {doc_id}")
        value = {
            "path": relative_path,
            "recorded_sha256": recorded,
            "actual_sha256": actual,
        }
        cache[doc_id] = value
        output[doc_id] = value
    return output


def build_contract(
    *,
    root: Path,
    payload_rows: list[dict[str, Any]],
    pool_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
    bridge_rows: list[dict[str, Any]],
    inventory_rows: list[dict[str, str]],
    manifest_rows: list[dict[str, Any]],
    blocked_docs: set[str],
    gold_ids: set[str],
    task_targets: dict[str, int],
    seed: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    payload = unique_mapping(payload_rows, "primary payload")
    pool = unique_mapping(pool_rows, "primary pool")
    decisions = unique_mapping(decision_rows, "auditor decisions")
    bridge = unique_mapping(bridge_rows, "agreement bridge")
    inventory_status = {
        str(row.get("doc_id") or "").strip(): str(row.get("public_status") or "").strip()
        for row in inventory_rows
        if str(row.get("doc_id") or "").strip()
    }
    pair_docs = manifest_pair_docs(manifest_rows)
    manifest_docs = {
        str(row.get("doc_id") or "").strip(): row
        for row in manifest_rows
        if row.get("type") == "doc" and str(row.get("doc_id") or "").strip()
    }
    pending_ids = {row_id for row_id, row in payload.items() if row.get("auditor_overlap")}
    candidate_ids = set(decisions) | pending_ids | set(bridge)
    exclusions: Counter[str] = Counter()
    prepared: list[dict[str, Any]] = []
    payload_cache: dict[str, dict[str, str]] = {}

    for row_id in sorted(candidate_ids):
        primary = payload.get(row_id)
        source = pool.get(row_id)
        if primary is None:
            exclusions["missing_primary_payload"] += 1
            continue
        if source is None:
            exclusions["missing_primary_pool"] += 1
            continue
        task = str(primary.get("task") or "").strip()
        split = str(primary.get("reserved_split") or source.get("reserved_split") or "").strip()
        if task not in task_targets:
            exclusions["unsupported_task"] += 1
            continue
        if split not in {"dev", "test"}:
            exclusions["not_dev_or_test"] += 1
            continue
        if row_id in gold_ids:
            exclusions["already_active_gold"] += 1
            continue
        release_safe, rights_reason = candidate_is_release_safe(
            source, inventory_status, blocked_docs, pair_docs
        )
        if not release_safe:
            exclusions[f"rights:{rights_reason}"] += 1
            continue
        evidence_path = Path(str(primary.get("evidence_path") or ""))
        if not evidence_path.is_file():
            exclusions["primary_evidence_missing"] += 1
            continue
        evidence_sha = file_sha256(evidence_path)
        recorded_evidence_sha = str(primary.get("evidence_sha256") or "").strip().lower()
        if recorded_evidence_sha and evidence_sha.lower() != recorded_evidence_sha:
            exclusions["primary_evidence_hash_mismatch"] += 1
            continue
        fingerprint, fingerprint_status = candidate_evidence_fingerprint(root, source)
        if fingerprint_status != "pixel_crop_sha256":
            exclusions["source_evidence_unavailable"] += 1
            continue
        docs = candidate_source_docs(source, pair_docs)
        if not docs:
            exclusions["source_documents_missing"] += 1
            continue
        try:
            payloads = source_payloads(root, docs, manifest_docs, payload_cache)
        except ValueError as exc:
            exclusions[str(exc)] += 1
            continue

        if row_id in decisions:
            channel = "completed_independent_screen"
        elif row_id in pending_ids:
            channel = "pending_issued_independent_screen"
        else:
            channel = "new_independent_screen_required"
        output = dict(source)
        output.update(
            {
                "record_id": row_id,
                "task": task,
                "reserved_split": split,
                "agreement_channel": channel,
                "agreement_contract_status": "machine_frozen_pending_human_and_promotion",
                "agreement_primary_review_status": "pending_existing_primary_workbook",
                "agreement_auditor_review_status": (
                    "complete"
                    if channel == "completed_independent_screen"
                    else "pending_existing_assignment"
                    if channel == "pending_issued_independent_screen"
                    else "new_assignment_not_issued"
                ),
                "primary_index": primary.get("primary_index"),
                "primary_evidence_path": str(evidence_path),
                "primary_evidence_sha256": evidence_sha,
                "agreement_evidence_fingerprint": fingerprint,
                "agreement_evidence_fingerprint_status": fingerprint_status,
                "agreement_source_documents": sorted(docs),
                "agreement_source_payloads": payloads,
                "agreement_source_rights_check": "release_safe_status",
                "agreement_auditor_reviewer_id": str(
                    decisions.get(row_id, {}).get("reviewer_id") or ""
                ),
                "agreement_auditor_decision": str(
                    decisions.get(row_id, {}).get("decision") or ""
                ),
                "agreement_auditor_decision_code": str(
                    decisions.get(row_id, {}).get("decision_code") or ""
                ),
                "agreement_auditor_source_workbook": str(
                    decisions.get(row_id, {}).get("source_workbook") or ""
                ),
                "agreement_machine_contract_ready": True,
                "agreement_human_complete": False,
                "human_review_status": "unassigned_primary_return_pending",
                "safe_to_merge_gold": False,
            }
        )
        prepared.append(output)

    selected: list[dict[str, Any]] = []
    used_fingerprints: set[str] = set()
    channel_counts_available: Counter[tuple[str, str]] = Counter(
        (str(row["task"]), str(row["agreement_channel"])) for row in prepared
    )
    for task, target in task_targets.items():
        task_selected: list[dict[str, Any]] = []
        for channel in sorted(CHANNEL_PRIORITY, key=CHANNEL_PRIORITY.get):
            candidates = [
                row
                for row in prepared
                if row["task"] == task and row["agreement_channel"] == channel
            ]
            for row in diverse_order(candidates, task, seed):
                fingerprint = str(row["agreement_evidence_fingerprint"])
                if fingerprint in used_fingerprints:
                    exclusions["duplicate_evidence_fingerprint"] += 1
                    continue
                task_selected.append(row)
                used_fingerprints.add(fingerprint)
                if len(task_selected) == target:
                    break
            if len(task_selected) == target:
                break
        if len(task_selected) != target:
            raise ValueError(f"insufficient {task} rows: {len(task_selected)}/{target}")
        selected.extend(task_selected)

    for index, row in enumerate(selected, 1):
        row["agreement_contract_index"] = index
    new_auditor_rows = [
        dict(row)
        for row in selected
        if row["agreement_channel"] == "new_independent_screen_required"
    ]
    selected_channels = Counter(str(row["agreement_channel"]) for row in selected)
    selected_tasks = Counter(str(row["task"]) for row in selected)
    selected_splits = Counter(str(row["reserved_split"]) for row in selected)
    selected_sources = {
        doc_id for row in selected for doc_id in row["agreement_source_documents"]
    }
    report = {
        "status": "PASS",
        "goal": "Gold v2.0 Global",
        "workflow": "machine-first release-safe agreement contract",
        "contract_rows": len(selected),
        "task_targets": task_targets,
        "task_counts": dict(sorted(selected_tasks.items())),
        "split_counts": dict(sorted(selected_splits.items())),
        "channel_counts": dict(sorted(selected_channels.items())),
        "available_channel_counts": {
            f"{task}:{channel}": count
            for (task, channel), count in sorted(channel_counts_available.items())
        },
        "release_safe_source_documents": len(selected_sources),
        "unique_evidence_fingerprints": len(used_fingerprints),
        "completed_independent_screen_actions_reused": selected_channels[
            "completed_independent_screen"
        ],
        "pending_independent_screen_actions_already_issued": selected_channels[
            "pending_issued_independent_screen"
        ],
        "new_independent_screen_actions_not_issued": len(new_auditor_rows),
        "primary_promotion_actions_already_issued": len(selected),
        "formal_agreement_detailed_reviews_required": len(selected) * 2,
        "formal_agreement_reviewer_actions_reused": 0,
        "pending_candidate_screen_judgments": selected_channels[
            "pending_issued_independent_screen"
        ] + len(new_auditor_rows),
        "incremental_screen_actions_not_already_issued": len(new_auditor_rows),
        "exclusions": dict(sorted(exclusions.items())),
        "machine_candidate_contract_ready": True,
        "human_review_complete": False,
        "formal_agreement_complete_rows": 0,
        "ready_for_final_agreement_sample": False,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "interpretation": (
            "Machine checks freeze candidate identity, evidence, source hashes, rights, splits, "
            "quotas, and deduplication. Prior 1/2/3 auditor decisions are independent screens, "
            "not the detailed two-reviewer agreement audit required by the release gate."
        ),
    }
    return selected, new_auditor_rows, report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--auditor-decisions", type=Path, action="append", default=[], required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, default=Path("SOURCE_INVENTORY.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("manifest.jsonl"))
    parser.add_argument("--provenance-report", type=Path, required=True)
    parser.add_argument("--microtext-target", type=int, default=95)
    parser.add_argument("--visualdiff-target", type=int, default=90)
    parser.add_argument("--seed", default="engbench-v2-release-safe-agreement")
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--new-screen-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    paths = {
        name: resolve(root, value)
        for name, value in {
            "payload": args.payload,
            "pool": args.pool,
            "bridge": args.bridge,
            "source_inventory": args.source_inventory,
            "manifest": args.manifest,
            "provenance_report": args.provenance_report,
            "output_jsonl": args.output_jsonl,
            "new_screen_output": args.new_screen_output,
            "report_json": args.report_json,
            "report_md": args.report_md,
        }.items()
    }
    for output_name in ("output_jsonl", "new_screen_output", "report_json", "report_md"):
        if paths[output_name].exists():
            raise FileExistsError(f"output already exists: {paths[output_name]}")
    decision_paths = [resolve(root, value) for value in args.auditor_decisions]
    decision_rows = [row for path in decision_paths for row in read_jsonl(path)]
    payload_value = json.loads(paths["payload"].read_text(encoding="utf-8"))
    payload_rows = list(payload_value.get("rows") or [])
    provenance = json.loads(paths["provenance_report"].read_text(encoding="utf-8"))
    blocked_docs = {
        str(row.get("doc_id") or "").strip()
        for row in provenance.get("documents", [])
        if str(row.get("doc_id") or "").strip() and not bool(row.get("paper_ready"))
    }
    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = file_sha256(gold_path)
    contract, new_auditor_rows, report = build_contract(
        root=root,
        payload_rows=payload_rows,
        pool_rows=read_jsonl(paths["pool"]),
        decision_rows=decision_rows,
        bridge_rows=read_jsonl(paths["bridge"]),
        inventory_rows=read_csv(paths["source_inventory"]),
        manifest_rows=read_jsonl(paths["manifest"]),
        blocked_docs=blocked_docs,
        gold_ids=active_gold_ids(root),
        task_targets={"microtext": args.microtext_target, "visualdiff": args.visualdiff_target},
        seed=args.seed,
    )
    write_jsonl(paths["output_jsonl"], contract)
    write_jsonl(paths["new_screen_output"], new_auditor_rows)
    report.update(
        {
            "inputs": {
                "payload": str(paths["payload"]),
                "payload_sha256": file_sha256(paths["payload"]),
                "pool": str(paths["pool"]),
                "pool_sha256": file_sha256(paths["pool"]),
                "auditor_decisions": [str(path) for path in decision_paths],
                "auditor_decision_sha256": [file_sha256(path) for path in decision_paths],
                "bridge": str(paths["bridge"]),
                "bridge_sha256": file_sha256(paths["bridge"]),
                "source_inventory": str(paths["source_inventory"]),
                "source_inventory_sha256": file_sha256(paths["source_inventory"]),
                "manifest": str(paths["manifest"]),
                "manifest_sha256": file_sha256(paths["manifest"]),
                "provenance_report": str(paths["provenance_report"]),
                "provenance_report_sha256": file_sha256(paths["provenance_report"]),
            },
            "contract_output": str(paths["output_jsonl"]),
            "contract_output_sha256": file_sha256(paths["output_jsonl"]),
            "new_screen_output": str(paths["new_screen_output"]),
            "new_screen_output_sha256": file_sha256(paths["new_screen_output"]),
            "eng_bench_sha256_before": gold_hash_before,
            "eng_bench_sha256_after": file_sha256(gold_path),
        }
    )
    if report["eng_bench_sha256_before"] != report["eng_bench_sha256_after"]:
        raise ValueError("active Gold changed while building agreement contract")
    write_json(paths["report_json"], report)
    paths["report_md"].parent.mkdir(parents=True, exist_ok=True)
    paths["report_md"].write_text(
        "# Release-Safe Agreement Contract\n\n"
        f"- Goal: **{report['goal']}**\n"
        f"- Status: **{report['status']}**\n"
        f"- Contract: **{report['contract_rows']}** rows "
        f"({report['task_counts']['microtext']} MicroText / "
        f"{report['task_counts']['visualdiff']} VisualDiff)\n"
        f"- Completed independent screens reused: **{report['completed_independent_screen_actions_reused']}**\n"
        f"- Pending screen work already issued: **{report['pending_independent_screen_actions_already_issued']}**\n"
        f"- New screen rows not yet issued: **{report['new_independent_screen_actions_not_issued']}**\n"
        f"- Primary promotion rows already inside the current workbook: **{report['primary_promotion_actions_already_issued']}**\n"
        f"- Detailed formal agreement reviews still required: **{report['formal_agreement_detailed_reviews_required']}**\n"
        "- Prior simplified screens counted as formal agreement reviews: **0**\n"
        "- Human agreement complete: **false**\n"
        "- Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
