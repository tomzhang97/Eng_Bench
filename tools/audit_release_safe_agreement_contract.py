#!/usr/bin/env python3
"""Independently audit a frozen machine-first agreement contract."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_provenance_replacement_plan import candidate_evidence_fingerprint
from build_release_safe_agreement_contract import file_sha256, identity, read_jsonl, write_json
from prepare_incremental_human_audit_round import active_gold_ids


ALLOWED_CHANNELS = {
    "completed_independent_screen",
    "pending_issued_independent_screen",
    "new_independent_screen_required",
}


def local_path(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else root / path


def audit_contract(
    *,
    root: Path,
    rows: list[dict[str, Any]],
    new_auditor_rows: list[dict[str, Any]],
    gold_ids: set[str],
    expected_rows: int,
    task_targets: dict[str, int],
) -> dict[str, Any]:
    issues: list[str] = []
    ids = [identity(row) for row in rows]
    fingerprints = [str(row.get("agreement_evidence_fingerprint") or "") for row in rows]
    if len(rows) != expected_rows:
        issues.append(f"contract_row_count:{len(rows)}:{expected_rows}")
    if any(not value for value in ids):
        issues.append("blank_contract_identity")
    if len(ids) != len(set(ids)):
        issues.append("duplicate_contract_identity")
    if any(not value for value in fingerprints):
        issues.append("blank_evidence_fingerprint")
    if len(fingerprints) != len(set(fingerprints)):
        issues.append("duplicate_evidence_fingerprint")

    task_counts = Counter(str(row.get("task") or "") for row in rows)
    for task, target in task_targets.items():
        if task_counts[task] != target:
            issues.append(f"task_quota:{task}:{task_counts[task]}:{target}")
    channel_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    source_docs: set[str] = set()
    source_hash_cache: dict[str, str] = {}
    fingerprint_rechecks = 0
    primary_evidence_rechecks = 0
    source_payload_rechecks = 0

    for row in rows:
        row_id = identity(row) or "<blank>"
        task = str(row.get("task") or "")
        split = str(row.get("reserved_split") or "")
        channel = str(row.get("agreement_channel") or "")
        split_counts[split] += 1
        channel_counts[channel] += 1
        if task not in task_targets:
            issues.append(f"unsupported_task:{row_id}:{task}")
        if split not in {"dev", "test"}:
            issues.append(f"invalid_split:{row_id}:{split}")
        if channel not in ALLOWED_CHANNELS:
            issues.append(f"invalid_channel:{row_id}:{channel}")
        if row_id in gold_ids:
            issues.append(f"already_active_gold:{row_id}")
        if row.get("agreement_source_rights_check") != "release_safe_status":
            issues.append(f"rights_not_release_safe:{row_id}")
        if row.get("safe_to_merge_gold") is not False:
            issues.append(f"safe_to_merge_not_false:{row_id}")
        if row.get("agreement_human_complete") is not False:
            issues.append(f"human_complete_not_false:{row_id}")
        if row.get("agreement_machine_contract_ready") is not True:
            issues.append(f"machine_contract_not_ready:{row_id}")

        evidence_path = local_path(root, row.get("primary_evidence_path"))
        expected_evidence_hash = str(row.get("primary_evidence_sha256") or "").lower()
        if not evidence_path.is_file():
            issues.append(f"primary_evidence_missing:{row_id}")
        elif not expected_evidence_hash:
            issues.append(f"primary_evidence_hash_missing:{row_id}")
        elif file_sha256(evidence_path).lower() != expected_evidence_hash:
            issues.append(f"primary_evidence_hash_mismatch:{row_id}")
        else:
            primary_evidence_rechecks += 1

        actual_fingerprint, status = candidate_evidence_fingerprint(root, row)
        if status != "pixel_crop_sha256":
            issues.append(f"source_evidence_unavailable:{row_id}")
        elif actual_fingerprint != row.get("agreement_evidence_fingerprint"):
            issues.append(f"evidence_fingerprint_mismatch:{row_id}")
        else:
            fingerprint_rechecks += 1

        docs = row.get("agreement_source_documents")
        payloads = row.get("agreement_source_payloads")
        if not isinstance(docs, list) or not docs:
            issues.append(f"source_documents_missing:{row_id}")
            docs = []
        if not isinstance(payloads, dict):
            issues.append(f"source_payloads_missing:{row_id}")
            payloads = {}
        for doc_id in docs:
            source_docs.add(str(doc_id))
            payload = payloads.get(doc_id)
            if not isinstance(payload, dict):
                issues.append(f"source_payload_entry_missing:{row_id}:{doc_id}")
                continue
            source_path = local_path(root, payload.get("path"))
            expected_hash = str(payload.get("recorded_sha256") or "").lower()
            if not source_path.is_file():
                issues.append(f"source_payload_missing:{row_id}:{doc_id}")
                continue
            cache_key = str(source_path.resolve())
            actual_hash = source_hash_cache.get(cache_key)
            if actual_hash is None:
                actual_hash = file_sha256(source_path).lower()
                source_hash_cache[cache_key] = actual_hash
            if not expected_hash or actual_hash != expected_hash:
                issues.append(f"source_payload_hash_mismatch:{row_id}:{doc_id}")
            else:
                source_payload_rechecks += 1

        reviewer = str(row.get("agreement_auditor_reviewer_id") or "")
        decision_code = str(row.get("agreement_auditor_decision_code") or "")
        if channel == "completed_independent_screen":
            if not reviewer or decision_code not in {"1", "2", "3"}:
                issues.append(f"completed_audit_metadata_invalid:{row_id}")
        elif reviewer or decision_code:
            issues.append(f"unfinished_audit_has_decision:{row_id}")

    new_ids = [identity(row) for row in new_auditor_rows]
    expected_new_ids = {
        identity(row)
        for row in rows
        if row.get("agreement_channel") == "new_independent_screen_required"
    }
    if any(not value for value in new_ids):
        issues.append("blank_new_auditor_identity")
    if len(new_ids) != len(set(new_ids)):
        issues.append("duplicate_new_auditor_identity")
    if set(new_ids) != expected_new_ids:
        issues.append("new_auditor_output_identity_mismatch")

    return {
        "status": "PASS" if not issues else "FAIL",
        "goal": "Gold v2.0 Global",
        "contract_rows": len(rows),
        "expected_rows": expected_rows,
        "task_counts": dict(sorted(task_counts.items())),
        "task_targets": task_targets,
        "split_counts": dict(sorted(split_counts.items())),
        "channel_counts": dict(sorted(channel_counts.items())),
        "unique_identities": len(set(ids)),
        "unique_evidence_fingerprints": len(set(fingerprints)),
        "release_safe_source_documents": len(source_docs),
        "primary_evidence_hashes_rechecked": primary_evidence_rechecks,
        "source_evidence_fingerprints_rechecked": fingerprint_rechecks,
        "source_payload_references_rechecked": source_payload_rechecks,
        "unique_source_payloads_rehashed": len(source_hash_cache),
        "new_independent_screen_rows": len(new_auditor_rows),
        "formal_agreement_detailed_reviews_required": len(rows) * 2,
        "formal_agreement_reviewer_actions_reused": 0,
        "issues": issues,
        "machine_contract_ready": not issues,
        "human_review_complete": False,
        "ready_for_final_agreement_sample": False,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--new-screen", type=Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=185)
    parser.add_argument("--microtext-target", type=int, default=95)
    parser.add_argument("--visualdiff-target", type=int, default=90)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    contract_path = local_path(root, args.contract)
    new_auditor_path = local_path(root, args.new_screen)
    output_json = local_path(root, args.output_json)
    output_md = local_path(root, args.output_md)
    for path in (output_json, output_md):
        if path.exists():
            raise FileExistsError(f"output already exists: {path}")
    gold_path = root / "eng_bench.jsonl"
    gold_hash_before = file_sha256(gold_path)
    report = audit_contract(
        root=root,
        rows=read_jsonl(contract_path),
        new_auditor_rows=read_jsonl(new_auditor_path),
        gold_ids=active_gold_ids(root),
        expected_rows=args.expected_rows,
        task_targets={"microtext": args.microtext_target, "visualdiff": args.visualdiff_target},
    )
    report.update(
        {
            "contract": str(contract_path),
            "contract_sha256": file_sha256(contract_path),
            "new_screen": str(new_auditor_path),
            "new_screen_sha256": file_sha256(new_auditor_path),
            "eng_bench_sha256_before": gold_hash_before,
            "eng_bench_sha256_after": file_sha256(gold_path),
        }
    )
    if report["eng_bench_sha256_before"] != report["eng_bench_sha256_after"]:
        report["issues"].append("active_gold_changed_during_audit")
        report["status"] = "FAIL"
        report["machine_contract_ready"] = False
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(
        "# Agreement Contract Audit\n\n"
        f"- Goal: **{report['goal']}**\n"
        f"- Status: **{report['status']}**\n"
        f"- Contract rows: **{report['contract_rows']}/{report['expected_rows']}**\n"
        f"- Unique evidence fingerprints: **{report['unique_evidence_fingerprints']}**\n"
        f"- Source payloads rehashed: **{report['unique_source_payloads_rehashed']}**\n"
        f"- New independent screen rows: **{report['new_independent_screen_rows']}**\n"
        f"- Detailed formal agreement reviews still required: **{report['formal_agreement_detailed_reviews_required']}**\n"
        f"- Issues: **{len(report['issues'])}**\n"
        "- Human agreement complete: **false**\n"
        "- Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
