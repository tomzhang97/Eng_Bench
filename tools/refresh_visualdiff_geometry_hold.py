#!/usr/bin/env python3
"""Re-pin an active VisualDiff geometry hold after a disjoint Gold transaction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import visualdiff_geometry_holds as holds
except ModuleNotFoundError:
    from tools import visualdiff_geometry_holds as holds


def _load_pinned(path: Path, expected_hash: str, label: str) -> dict[str, Any]:
    if not path.is_file() or holds.file_hash(path) != expected_hash:
        raise ValueError(f"{label}_missing_or_hash_mismatch")
    return json.loads(path.read_text(encoding="utf-8"))


def refresh(
    root: Path,
    transaction_report: Path,
    expected_transaction_sha256: str,
    output_dir: Path,
) -> dict[str, Any]:
    root = root.resolve()
    quality_root = (root / "derived/quality").resolve()
    transaction_report = transaction_report.resolve()
    output_dir = output_dir.resolve()
    if not transaction_report.is_relative_to(quality_root):
        raise ValueError("transaction report must be under derived/quality")
    if not output_dir.is_relative_to(quality_root) or output_dir.exists():
        raise ValueError("output must be a new directory under derived/quality")

    pointer_path = root / holds.POINTER
    if not pointer_path.is_file():
        raise ValueError("visualdiff geometry hold pointer is missing")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    prior_report_path = (root / str(pointer.get("report_path") or "")).resolve()
    prior_report = _load_pinned(
        prior_report_path,
        str(pointer.get("report_sha256") or ""),
        "prior_hold_report",
    )
    if prior_report.get("status") != "HOLD_ACTIVE" or prior_report.get("active_gold_modified") is not False:
        raise ValueError("prior hold report is not active")
    prior_ledger_path = (root / str(prior_report.get("hold_ledger") or "")).resolve()
    if (
        not prior_ledger_path.is_file()
        or holds.file_hash(prior_ledger_path)
        != str(prior_report.get("hold_ledger_sha256") or "")
    ):
        raise ValueError("prior_hold_ledger_missing_or_hash_mismatch")
    ledger_rows = holds.read_jsonl(prior_ledger_path)
    hold_ids = {str(row.get("pair_id") or "") for row in ledger_rows}
    if "" in hold_ids or len(hold_ids) != len(ledger_rows):
        raise ValueError("prior hold ledger identities are invalid")

    transaction = _load_pinned(
        transaction_report,
        expected_transaction_sha256.strip().lower(),
        "description_transaction",
    )
    if transaction.get("applied") is not True or transaction.get("rolled_back") is not False:
        raise ValueError("description transaction is not applied")
    before_hashes = transaction.get("before_hashes") or {}
    after_hashes = transaction.get("after_hashes") or {}
    prior_hashes = prior_report.get("active_hashes") or {}
    current_hashes = {path: holds.file_hash(root / path) for path in holds.ACTIVE_PATHS}
    for path in holds.ACTIVE_PATHS:
        if prior_hashes.get(path) != before_hashes.get(path):
            raise ValueError(f"transaction does not start at prior hold state: {path}")
        if current_hashes[path] != after_hashes.get(path):
            raise ValueError(f"active file does not match transaction output: {path}")

    correction_ids: set[str] = set()
    transaction_kind = "visualdiff_description_correction"
    corrections_text = str(transaction.get("corrections_artifact") or "").strip()
    if corrections_text:
        corrections_path = (root / corrections_text).resolve()
        if not corrections_path.is_file() or holds.file_hash(corrections_path) != str(
            transaction.get("corrections_artifact_sha256") or ""
        ):
            raise ValueError("transaction corrections are missing or stale")
        correction_ids = {
            str(row.get("pair_id") or "") for row in holds.read_jsonl(corrections_path)
        }
        if "" in correction_ids or correction_ids & hold_ids:
            raise ValueError("description transaction overlaps active geometry holds")
    else:
        transaction_kind = "non_visualdiff_gold_transaction"
        if transaction.get("mode") != "reviewed_gold_promotion_transaction":
            raise ValueError("transaction corrections are missing or stale")
        validation = transaction.get("validation") or {}
        if int(validation.get("promoted_visualdiff_rows") or 0) != 0:
            raise ValueError("gold transaction includes VisualDiff rows")
        for path in (
            "visualdiff/annotations/visualdiff_pairs.jsonl",
            "visualdiff/annotations/visualdiff_questions.jsonl",
        ):
            if before_hashes.get(path) != after_hashes.get(path):
                raise ValueError(f"gold transaction changed VisualDiff content: {path}")

    active_pairs = {
        str(row.get("pair_id") or ""): row
        for row in holds.read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    }
    for held in ledger_rows:
        pair_id = str(held["pair_id"])
        active = active_pairs.get(pair_id)
        if active is None:
            raise ValueError(f"held VisualDiff row is no longer active: {pair_id}")
        if active.get("bbox_old") != held.get("bbox_old_after"):
            raise ValueError(f"held OLD geometry changed: {pair_id}")
        if active.get("bbox_new") != held.get("bbox_new"):
            raise ValueError(f"held NEW geometry changed: {pair_id}")

    before_refresh = dict(current_hashes)
    output_dir.mkdir(parents=True)
    ledger_path = output_dir / "active_visualdiff_geometry_holds.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger_rows),
        encoding="utf-8",
    )
    report = {
        "goal": "Gold v2.0 Global",
        "status": "HOLD_ACTIVE",
        "project_id": prior_report.get("project_id"),
        "hold_rows": len(ledger_rows),
        "reason": prior_report.get("reason"),
        "hold_ledger": ledger_path.relative_to(root).as_posix(),
        "hold_ledger_sha256": holds.file_hash(ledger_path),
        "active_hashes": current_hashes,
        "prior_hold_report": prior_report_path.relative_to(root).as_posix(),
        "prior_hold_report_sha256": holds.file_hash(prior_report_path),
        "description_transaction": transaction_report.relative_to(root).as_posix(),
        "description_transaction_sha256": holds.file_hash(transaction_report),
        "transaction_kind": transaction_kind,
        "description_correction_rows": len(correction_ids),
        "description_correction_overlap_with_holds": 0,
        "geometry_rows_verified": len(ledger_rows),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "next_action": prior_report.get("next_action"),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if before_refresh != {path: holds.file_hash(root / path) for path in holds.ACTIVE_PATHS}:
        raise ValueError("active Gold changed while refreshing geometry holds")
    pointer_updated = {
        "goal": "Gold v2.0 Global",
        "report_path": report_path.relative_to(root).as_posix(),
        "report_sha256": holds.file_hash(report_path),
        "hold_rows": len(ledger_rows),
        "active_gold_modified": False,
    }
    holds.atomic_text(root / holds.POINTER, json.dumps(pointer_updated, indent=2) + "\n")
    if holds.current_hold_ids(root) != hold_ids:
        raise ValueError("refreshed geometry hold pointer failed verification")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--transaction-report", type=Path, required=True)
    parser.add_argument("--transaction-report-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = refresh(
        root,
        root / args.transaction_report,
        args.transaction_report_sha256,
        root / args.output_dir,
    )
    print(json.dumps({
        "status": report["status"],
        "hold_rows": report["hold_rows"],
        "geometry_rows_verified": report["geometry_rows_verified"],
        "description_correction_rows": report["description_correction_rows"],
        "active_gold_modified": report["active_gold_modified"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
