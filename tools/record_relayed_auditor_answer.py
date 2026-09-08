#!/usr/bin/env python3
"""Record a user-relayed answer to a verified blank without altering the XLSX."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import ingest_auditor_return_batch as ingestion
except ImportError:
    import ingest_auditor_return_batch as ingestion

p = ingestion.parser


def bind_pending_answer(pending: list[dict[str, Any]], auditor: dict[str, Any],
                        display_index: int, code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    reviewer = f"auditor_{int(auditor['number']):02d}"
    selected = [r for r in pending if r["reviewer_id"] == reviewer
                and int(r["display_index"]) == display_index]
    if len(selected) != 1:
        raise ValueError("answer must identify exactly one verified pending blank")
    assigned = [r for r in auditor["rows"] if int(r["display_index"]) == display_index]
    if len(assigned) != 1 or p.row_identifier(assigned[0]) != selected[0]["record_id"]:
        raise ValueError("pending answer does not match frozen assignment identity")
    p.normalize_decision(str(assigned[0]["task"]), code)
    return selected[0], assigned[0]


def record(root: Path, source_dir: Path, number: int, display_index: int,
           code: str, statement: str, output: Path) -> dict[str, Any]:
    if not statement.strip():
        raise ValueError("the exact user statement is required for provenance")
    processed_root = (root / "derived/human_adjudication/processed_returns").resolve()
    if processed_root not in output.resolve().parents or output.exists():
        raise ValueError("output must be a new directory below processed_returns")
    report_path = source_dir / "processing_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS":
        raise ValueError("original workbook batch must have passed structural validation")
    for name, digest in report["output_hashes"].items():
        if p.sha256_file(source_dir / name) != digest:
            raise ValueError(f"original processing artifact changed: {name}")
    receipt_path = source_dir / "receipt.json"
    if p.sha256_file(receipt_path) != report["receipt_sha256"]:
        raise ValueError("original receipt hash mismatch")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload_path = Path(receipt["payload"])
    if p.sha256_file(payload_path) != receipt["payload_sha256"]:
        raise ValueError("frozen assignment payload hash mismatch")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    auditor = p.expected_by_number(payload)[number]
    pending = ingestion.read_jsonl(source_dir / "pending_answers.jsonl")
    blank, assigned = bind_pending_answer(pending, auditor, display_index, code)
    workbook_info = next(r for r in receipt["workbooks"] if r["auditor"] == number)
    workbook_path = source_dir / workbook_info["archived_file"]
    if p.sha256_file(workbook_path) != workbook_info["sha256"]:
        raise ValueError("original returned workbook changed")
    original_workbook_hash = p.sha256_file(workbook_path)
    _, workbook_report = p.validate_workbook(
        workbook_path, auditor, allow_incomplete_answers=True,
        canonical_workbook=Path(receipt["issued_dir"]) / auditor["workbook"],
    )
    if blank not in workbook_report["incomplete_rows"]:
        raise ValueError("the specified answer is not blank in the original XLSX")
    before = p.active_gold_hashes(root)
    if len(before) != len(p.ACTIVE_GOLD_PATHS):
        raise ValueError("active release hash coverage incomplete")
    reviewer = f"auditor_{number:02d}"
    history_paths = sorted(processed_root.glob("**/*auditor_decisions*.jsonl"))
    history = [r for path in history_paths for r in ingestion.read_jsonl(path)]
    if any(ingestion.decision_key(r) == (reviewer, blank["record_id"]) for r in history):
        raise ValueError("this auditor/record already has a recorded answer; no overwrite or replay allowed")
    relayed = {
        "goal": "Gold v2.0 Global", "reviewer_id": reviewer,
        "reviewer_role": "independent_auditor", "task_type": assigned["task"],
        "primary_index": int(assigned["primary_index"]), "record_id": blank["record_id"],
        "candidate_id": blank["record_id"] if assigned["task"] == "microtext" else "",
        "pair_id": blank["record_id"] if assigned["task"] == "visualdiff" else "",
        "decision_code": code, "decision": p.normalize_decision(assigned["task"], code),
        "display_index": display_index, "sheet_name": blank["sheet_name"],
        "answer_cell": blank["answer_cell"], "source_workbook": workbook_path.name,
        "source_workbook_sha256": original_workbook_hash,
        "assignment_payload": payload_path.as_posix(), "assignment_payload_sha256": receipt["payload_sha256"],
        "evidence_sha256": assigned["evidence_sha256"],
        "assignment_origin": assigned.get("assignment_origin", ""),
        "return_source_sha256": receipt["source_sha256"], "preserved_answer_code": "",
        "carried_forward_answer": False,
        "decision_source": "user_relay_on_behalf_of_auditor",
        "user_statement": statement, "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "direct_reviewer_workbook_confirmation": False,
        "original_workbook_answer_remains_blank": True,
        "safe_to_merge_gold": False, "gold_rows_modified": 0,
    }
    observations = ingestion.read_jsonl(source_dir / "observations.jsonl") + [relayed]
    observations.sort(key=lambda r: (r["reviewer_id"], r["display_index"]))
    remaining = [r for r in pending if r != blank]
    primary = {p.row_identifier(r): {**r, "reconciliation_source": item["path"]}
               for item in report["primary_inputs"]
               for r in ingestion.read_jsonl(Path(item["path"]))}
    actions = ingestion.summarize_actions(observations, ingestion.active_identity_index(root), primary)
    after = p.active_gold_hashes(root)
    if before != after or p.sha256_file(workbook_path) != original_workbook_hash:
        raise ValueError("active Gold or original workbook changed during relay recording")
    output.mkdir(parents=True)
    artifacts = {"normalized_valid_auditor_decisions.jsonl": [relayed],
                 "effective_observations.jsonl": observations,
                 "remaining_pending_answers.jsonl": remaining, "candidate_actions.jsonl": actions}
    for name, rows in artifacts.items():
        p.write_jsonl(output / name, rows)
    result = {
        "goal": "Gold v2.0 Global", "status": "PASS", "recorded_answer": relayed,
        "source_batch_report": report_path.as_posix(), "source_batch_report_sha256": p.sha256_file(report_path),
        "source_batch_raw_workbook_answers": report["validated_answer_count"],
        "effective_batch_answers": len(observations), "user_relayed_answers": 1,
        "effective_decision_counts": dict(Counter(r["decision_code"] for r in observations)),
        "total_registered_decisions": len(history) + 1,
        "pending_returned_batch_answers": remaining, "outstanding_auditors": report["missing_auditors"],
        "formal_agreement_gate_credit": 0, "active_gold_modified": False,
        "original_workbook_modified": False, "safe_to_merge_gold": False,
        "active_gold_hashes_before": before, "active_gold_hashes_after": after,
        "history_inputs": [{"path": path.as_posix(), "sha256": p.sha256_file(path)} for path in history_paths],
        "output_hashes": {name: p.sha256_file(output / name) for name in artifacts},
    }
    ingestion.write_json(output / "processing_report.json", result)
    return result


def main() -> int:
    args_parser = argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument("--root", type=Path, default=Path("."))
    args_parser.add_argument("--source-dir", type=Path, required=True)
    args_parser.add_argument("--auditor", type=int, required=True)
    args_parser.add_argument("--sample", type=int, required=True)
    args_parser.add_argument("--decision", choices=("1", "2", "3"), required=True)
    args_parser.add_argument("--user-statement", required=True)
    args_parser.add_argument("--output-dir", type=Path, required=True)
    args = args_parser.parse_args()
    root = args.root.resolve()
    resolve = lambda path: path.resolve() if path.is_absolute() else (root / path).resolve()
    result = record(root, resolve(args.source_dir), args.auditor, args.sample,
                    args.decision, args.user_statement, resolve(args.output_dir))
    print(json.dumps({k: result[k] for k in ("status", "effective_batch_answers", "user_relayed_answers",
        "effective_decision_counts", "total_registered_decisions", "pending_returned_batch_answers",
        "outstanding_auditors", "active_gold_modified", "original_workbook_modified")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
