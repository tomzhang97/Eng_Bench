#!/usr/bin/env python3
"""Build and load hash-bound active VisualDiff geometry/semantic holds."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


POINTER = Path("derived/quality/current_visualdiff_geometry_hold.json")
ACTIVE_PATHS = (
    "eng_bench.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def current_hold_ids(root: Path) -> set[str]:
    root = root.resolve()
    pointer_path = root / POINTER
    if not pointer_path.is_file():
        return set()
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    report_path = root / str(pointer.get("report_path") or "")
    if not report_path.is_file() or file_hash(report_path) != str(pointer.get("report_sha256") or ""):
        raise ValueError("visualdiff_geometry_hold_pointer_is_stale")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "HOLD_ACTIVE" or report.get("active_gold_modified") is not False:
        raise ValueError("visualdiff_geometry_hold_report_is_not_active")
    ledger_path = root / str(report.get("hold_ledger") or "")
    if not ledger_path.is_file() or file_hash(ledger_path) != str(report.get("hold_ledger_sha256") or ""):
        raise ValueError("visualdiff_geometry_hold_ledger_is_stale")
    expected_hashes = report.get("active_hashes") or {}
    actual_hashes = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    if expected_hashes != actual_hashes:
        raise ValueError("visualdiff_geometry_hold_active_hashes_are_stale")
    rows = read_jsonl(ledger_path)
    ids = {str(row.get("pair_id") or "") for row in rows}
    if "" in ids or len(ids) != len(rows) or len(ids) != int(report.get("hold_rows") or -1):
        raise ValueError("visualdiff_geometry_hold_ids_are_invalid")
    return ids


def build_holds(
    root: Path,
    transaction_report_path: Path,
    expected_transaction_sha256: str,
    output_dir: Path,
) -> dict[str, Any]:
    root = root.resolve()
    transaction_report_path = transaction_report_path.resolve()
    output_dir = output_dir.resolve()
    quality_root = (root / "derived/quality").resolve()
    if not transaction_report_path.is_relative_to(quality_root):
        raise ValueError("transaction report must be under derived/quality")
    if not output_dir.is_relative_to(quality_root) or output_dir.exists():
        raise ValueError("output must be a new directory under derived/quality")
    actual_transaction_hash = file_hash(transaction_report_path)
    if actual_transaction_hash != expected_transaction_sha256.strip().lower():
        raise ValueError("transaction_report_sha256_mismatch")
    transaction = json.loads(transaction_report_path.read_text(encoding="utf-8"))
    if transaction.get("status") != "APPLIED" or int(
        transaction.get("existing_gold_rows_geometrically_repaired") or 0
    ) <= 0:
        raise ValueError("transaction_is_not_applied_geometry_repair")
    preview_path = root / str(transaction.get("preview_report") or "")
    if not preview_path.is_file() or file_hash(preview_path) != str(
        transaction.get("preview_report_sha256") or ""
    ):
        raise ValueError("preview_report_missing_or_hash_mismatch")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    corrections_path = root / str((preview.get("artifacts") or {}).get("corrections") or "")
    if not corrections_path.is_file() or file_hash(corrections_path) != str(
        (preview.get("artifact_sha256") or {}).get("corrections") or ""
    ):
        raise ValueError("corrections_missing_or_hash_mismatch")
    corrections = read_jsonl(corrections_path)
    active_hashes = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    expected_after = transaction.get("active_hashes_after") or {}
    if any(active_hashes[path] != expected_after.get(path) for path in ACTIVE_PATHS):
        raise ValueError("active_files_changed_since_geometry_repair")

    rows = [{
        "pair_id": str(row.get("pair_id") or ""),
        "project_id": str(transaction.get("project_id") or ""),
        "reason": "bbox_repaired_description_semantics_unverified",
        "status": "hold_from_routine_audit_and_release_until_semantic_revalidation",
        "bbox_old_before": row.get("bbox_old_before"),
        "bbox_old_after": row.get("bbox_old_after"),
        "bbox_new": row.get("bbox_new_unchanged"),
        "repair_policy_version": str(transaction.get("policy_version") or ""),
        "safe_to_merge_gold": False,
    } for row in corrections]
    if any(not row["pair_id"] for row in rows) or len({row["pair_id"] for row in rows}) != len(rows):
        raise ValueError("correction_ids_missing_or_duplicated")

    output_dir.mkdir(parents=True)
    ledger_path = output_dir / "active_visualdiff_geometry_holds.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    report = {
        "goal": "Gold v2.0 Global",
        "status": "HOLD_ACTIVE",
        "project_id": transaction.get("project_id"),
        "hold_rows": len(rows),
        "reason": "bbox_repaired_description_semantics_unverified",
        "transaction_report": transaction_report_path.relative_to(root).as_posix(),
        "transaction_report_sha256": actual_transaction_hash,
        "hold_ledger": ledger_path.relative_to(root).as_posix(),
        "hold_ledger_sha256": file_hash(ledger_path),
        "active_hashes": active_hashes,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "next_action": (
            "Revalidate descriptions against corrected evidence; release individual IDs "
            "only through a hash-pinned semantic decision transaction."
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if before != {path: file_hash(root / path) for path in ACTIVE_PATHS}:
        raise ValueError("active_gold_changed_while_building_geometry_hold")
    pointer = {
        "goal": "Gold v2.0 Global",
        "report_path": report_path.relative_to(root).as_posix(),
        "report_sha256": file_hash(report_path),
        "hold_rows": len(rows),
        "active_gold_modified": False,
    }
    atomic_text(root / POINTER, json.dumps(pointer, indent=2) + "\n")
    if current_hold_ids(root) != {row["pair_id"] for row in rows}:
        raise ValueError("written_geometry_hold_pointer_failed_verification")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--transaction-report", type=Path, required=True)
    parser.add_argument("--transaction-report-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_holds(
        root,
        (root / args.transaction_report).resolve(),
        args.transaction_report_sha256,
        (root / args.output_dir).resolve(),
    )
    print(json.dumps({
        key: report[key] for key in (
            "status", "project_id", "hold_rows", "reason",
            "active_gold_modified", "next_action",
        )
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
