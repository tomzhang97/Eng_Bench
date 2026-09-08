#!/usr/bin/env python3
"""Build a hash-pinned preview for active VisualDiff homography box repairs."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from audit_visualdiff_homography_boxes import ACTIVE_PATHS, read_jsonl, valid_bbox
except ModuleNotFoundError:
    from tools.audit_visualdiff_homography_boxes import ACTIVE_PATHS, read_jsonl, valid_bbox


POLICY_VERSION = "visualdiff_old_bbox_inverse_homography_v1"


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def index_unique(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, row in enumerate(rows):
        value = str(row.get(key) or "")
        if not value or value in result:
            raise ValueError(f"missing_or_duplicate_{key}:{value or 'blank'}")
        result[value] = index
    return result


def unified_pair_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    return str(metadata.get("pair_id") or "") if isinstance(metadata, dict) else ""


def require_quality_path(root: Path, value: Path, label: str) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.resolve()
    quality = (root / "derived/quality").resolve()
    if not resolved.is_relative_to(quality):
        raise ValueError(f"{label} must be under root/derived/quality")
    return resolved


def build_preview(
    root: Path,
    audit_report_path: Path,
    expected_audit_sha256: str,
    output_dir: Path,
) -> dict[str, Any]:
    root = root.resolve()
    report_path = require_quality_path(root, audit_report_path, "audit report")
    output_dir = require_quality_path(root, output_dir, "output directory")
    if output_dir.exists():
        raise ValueError("output directory must not already exist")
    expected = expected_audit_sha256.strip().lower()
    actual_report_hash = file_hash(report_path).lower()
    if not expected or expected != actual_report_hash:
        raise ValueError(f"audit_report_sha256_mismatch:{actual_report_hash}:{expected or 'blank'}")
    audit = json.loads(report_path.read_text(encoding="utf-8"))
    if audit.get("active_gold_modified") is not False or audit.get("safe_to_apply") is not False:
        raise ValueError("audit_report_not_read_only")
    proposal_path = root / str(audit.get("proposal_jsonl") or "")
    if not proposal_path.is_file() or file_hash(proposal_path) != str(audit.get("proposal_sha256") or ""):
        raise ValueError("proposal_missing_or_hash_mismatch")

    active_hashes = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    if active_hashes != audit.get("active_hashes_after"):
        raise ValueError("active_files_changed_since_audit")

    proposals = read_jsonl(proposal_path)
    corrections = [row for row in proposals if row.get("repair_candidate") is True]
    if not corrections:
        raise ValueError("no_repair_candidates")
    if len(corrections) != int(audit.get("repair_candidate_rows") or -1):
        raise ValueError("repair_candidate_count_mismatch")

    pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    unified = read_jsonl(root / "eng_bench.jsonl")
    new_pairs, new_unified = copy.deepcopy(pairs), copy.deepcopy(unified)
    pair_index = index_unique(new_pairs, "pair_id")
    unified_index: dict[str, int] = {}
    for index, row in enumerate(new_unified):
        if row.get("task") != "visualdiff":
            continue
        identity = unified_pair_id(row)
        if not identity or identity in unified_index:
            raise ValueError(f"missing_or_duplicate_unified_pair_id:{identity or 'blank'}")
        unified_index[identity] = index

    applied = []
    for correction in corrections:
        pair_id = str(correction.get("pair_id") or "")
        if pair_id not in pair_index or pair_id not in unified_index:
            raise ValueError(f"correction_not_active:{pair_id}")
        pair = new_pairs[pair_index[pair_id]]
        item = new_unified[unified_index[pair_id]]
        current = correction.get("bbox_old_current")
        proposed = correction.get("bbox_old_projected")
        if pair.get("bbox_old") != current or pair.get("bbox_new") != correction.get("bbox_new_current"):
            raise ValueError(f"pair_bbox_prestate_mismatch:{pair_id}")
        if valid_bbox(proposed) is None or not correction.get("projected_old_within_image"):
            raise ValueError(f"invalid_proposed_bbox:{pair_id}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or len(evidence) < 2:
            raise ValueError(f"missing_unified_evidence:{pair_id}")
        old_evidence = evidence[0]
        new_evidence = evidence[1]
        if not isinstance(old_evidence, dict) or not isinstance(new_evidence, dict):
            raise ValueError(f"malformed_unified_evidence:{pair_id}")
        if old_evidence.get("bbox") != current or new_evidence.get("bbox") != correction.get("bbox_new_current"):
            raise ValueError(f"unified_bbox_prestate_mismatch:{pair_id}")
        pair["bbox_old"] = proposed
        old_evidence["bbox"] = proposed
        applied.append({
            "pair_id": pair_id,
            "bbox_old_before": current,
            "bbox_old_after": proposed,
            "bbox_new_unchanged": correction.get("bbox_new_current"),
            "homography_path": correction.get("homography_path"),
            "policy_version": POLICY_VERSION,
        })

    changed_pair_ids = {
        str(before.get("pair_id") or "")
        for before, after in zip(pairs, new_pairs) if before != after
    }
    changed_unified_ids = {
        unified_pair_id(before)
        for before, after in zip(unified, new_unified) if before != after
    }
    expected_ids = {row["pair_id"] for row in applied}
    if changed_pair_ids != expected_ids or changed_unified_ids != expected_ids:
        raise ValueError("preview_changed_unexpected_rows")

    output_dir.mkdir(parents=True)
    pair_preview = output_dir / "visualdiff_pairs_preview.jsonl"
    unified_preview = output_dir / "eng_bench_preview.jsonl"
    corrections_path = output_dir / "bbox_corrections.jsonl"
    write_jsonl(pair_preview, new_pairs)
    write_jsonl(unified_preview, new_unified)
    write_jsonl(corrections_path, applied)

    active_after = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "read_only_visualdiff_homography_bbox_repair_preview",
        "policy_version": POLICY_VERSION,
        "status": "PASS",
        "ready_for_apply": True,
        "project_id": audit.get("project_id"),
        "correction_rows": len(applied),
        "pair_rows": len(new_pairs),
        "unified_rows": len(new_unified),
        "changed_pair_rows": len(changed_pair_ids),
        "changed_unified_rows": len(changed_unified_ids),
        "audit_report": report_path.relative_to(root).as_posix(),
        "audit_report_sha256": actual_report_hash,
        "audit_proposal_sha256": file_hash(proposal_path),
        "artifacts": {
            "pairs_preview": pair_preview.relative_to(root).as_posix(),
            "unified_preview": unified_preview.relative_to(root).as_posix(),
            "corrections": corrections_path.relative_to(root).as_posix(),
        },
        "artifact_sha256": {
            "pairs_preview": file_hash(pair_preview),
            "unified_preview": file_hash(unified_preview),
            "corrections": file_hash(corrections_path),
        },
        "active_hashes_before": active_hashes,
        "active_hashes_after": active_after,
        "active_gold_modified": active_hashes != active_after,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "limitation": (
            "This preview repairs geometric evidence coordinates only. It does not "
            "certify or rewrite any VisualDiff description."
        ),
    }
    if report["active_gold_modified"]:
        report["status"] = "FAIL"
        report["ready_for_apply"] = False
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--audit-report-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_preview(
        root, args.audit_report, args.audit_report_sha256, root / args.output_dir,
    )
    print(json.dumps({
        key: report[key] for key in (
            "status", "ready_for_apply", "project_id", "correction_rows",
            "pair_rows", "unified_rows", "changed_pair_rows",
            "changed_unified_rows", "active_gold_modified",
        )
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
