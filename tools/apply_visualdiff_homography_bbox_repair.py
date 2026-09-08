#!/usr/bin/env python3
"""Atomically apply a pinned VisualDiff homography bbox repair preview."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

try:
    import validate_engbench
    import validate_engbench_v2
    from audit_visualdiff_homography_boxes import ACTIVE_PATHS, read_jsonl, valid_bbox
    from preview_visualdiff_homography_bbox_repair import POLICY_VERSION
except ModuleNotFoundError:
    from tools import validate_engbench, validate_engbench_v2
    from tools.audit_visualdiff_homography_boxes import ACTIVE_PATHS, read_jsonl, valid_bbox
    from tools.preview_visualdiff_homography_bbox_repair import POLICY_VERSION


MUTABLE_PATHS = (
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "eng_bench.jsonl",
)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def require_under(root: Path, value: Path, parent: str, label: str) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.resolve()
    allowed = (root / parent).resolve()
    if not resolved.is_relative_to(allowed):
        raise ValueError(f"{label} must be under root/{parent}")
    return resolved


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def unified_pair_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    return str(metadata.get("pair_id") or "") if isinstance(metadata, dict) else ""


def validate_active(root: Path, expected_ids: set[str]) -> dict[str, Any]:
    pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    questions = read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    legacy_errors = validate_engbench.validate_visualdiff_pairs(pairs)
    legacy_errors.extend(
        validate_engbench.validate_visualdiff_questions(
            questions, {str(row.get("pair_id") or "") for row in pairs},
        )
    )
    if legacy_errors:
        raise ValueError(f"legacy_validation_failed:{legacy_errors[:3]}")

    items = validate_engbench_v2.load_jsonl(str(root / "eng_bench.jsonl"))
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict_report, bad = validate_engbench_v2.validate_all(
        items, manifest, str(root), strict=False, skip_textlayer=True,
    )
    if strict_report.errors or bad:
        raise ValueError(f"strict_validation_failed:{strict_report.errors[:3]}")

    pair_map = {str(row.get("pair_id") or ""): row for row in pairs}
    unified_map = {
        unified_pair_id(row): row for row in items
        if row.get("task") == "visualdiff" and unified_pair_id(row)
    }
    for pair_id in expected_ids:
        pair = pair_map.get(pair_id)
        item = unified_map.get(pair_id)
        if not pair or not item:
            raise ValueError(f"repaired_identity_missing:{pair_id}")
        evidence = item.get("evidence")
        images = item.get("images")
        if not isinstance(evidence, list) or len(evidence) < 2:
            raise ValueError(f"repaired_evidence_missing:{pair_id}")
        if not isinstance(images, list) or len(images) < 2:
            raise ValueError(f"repaired_images_missing:{pair_id}")
        if evidence[0].get("bbox") != pair.get("bbox_old"):
            raise ValueError(f"repaired_old_bbox_crosslink_mismatch:{pair_id}")
        if evidence[1].get("bbox") != pair.get("bbox_new"):
            raise ValueError(f"repaired_new_bbox_crosslink_mismatch:{pair_id}")
        if valid_bbox(pair.get("bbox_old")) is None:
            raise ValueError(f"repaired_old_bbox_invalid:{pair_id}")
        with __import__("PIL.Image", fromlist=["Image"]).open(root / images[0]) as image:
            width, height = image.size
        x0, y0, x1, y1 = pair["bbox_old"]
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            raise ValueError(f"repaired_old_bbox_out_of_bounds:{pair_id}")
    return {
        "legacy_visualdiff_errors": 0,
        "strict_unified_errors": 0,
        "strict_bad_rows": 0,
        "repaired_crosslinks_verified": len(expected_ids),
    }


def apply_preview(
    root: Path,
    preview_report_path: Path,
    expected_preview_sha256: str,
    snapshot_dir: Path,
    output_report_path: Path,
) -> dict[str, Any]:
    root = root.resolve()
    preview_path = require_under(root, preview_report_path, "derived/quality", "preview report")
    snapshot_dir = require_under(root, snapshot_dir, "derived/snapshots", "snapshot directory")
    output_report_path = require_under(root, output_report_path, "derived/quality", "output report")
    if snapshot_dir.exists() or output_report_path.exists():
        raise ValueError("snapshot directory and output report must be new")
    expected = expected_preview_sha256.strip().lower()
    actual_preview_hash = file_hash(preview_path).lower()
    if not expected or expected != actual_preview_hash:
        raise ValueError(f"preview_report_sha256_mismatch:{actual_preview_hash}:{expected or 'blank'}")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    if preview.get("policy_version") != POLICY_VERSION:
        raise ValueError("preview_policy_mismatch")
    if preview.get("status") != "PASS" or preview.get("ready_for_apply") is not True:
        raise ValueError("preview_not_ready")
    if preview.get("active_gold_modified") is not False:
        raise ValueError("preview_modified_active_gold")

    active_before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    if active_before != preview.get("active_hashes_before"):
        raise ValueError("active_files_changed_since_preview")
    artifacts = preview.get("artifacts") or {}
    artifact_hashes = preview.get("artifact_sha256") or {}
    resolved_artifacts: dict[str, Path] = {}
    for name in ("pairs_preview", "unified_preview", "corrections"):
        path = require_under(root, Path(str(artifacts.get(name) or "")), "derived/quality", name)
        if not path.is_file() or file_hash(path) != str(artifact_hashes.get(name) or ""):
            raise ValueError(f"preview_artifact_missing_or_hash_mismatch:{name}")
        resolved_artifacts[name] = path
    corrections = read_jsonl(resolved_artifacts["corrections"])
    expected_ids = {str(row.get("pair_id") or "") for row in corrections}
    if not expected_ids or len(expected_ids) != len(corrections):
        raise ValueError("correction_ids_missing_or_duplicated")
    if len(expected_ids) != int(preview.get("correction_rows") or -1):
        raise ValueError("correction_count_mismatch")

    snapshot_dir.mkdir(parents=True)
    for relative in MUTABLE_PATHS:
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
    snapshot_manifest = {
        "goal": "Gold v2.0 Global",
        "policy_version": POLICY_VERSION,
        "preview_report": preview_path.relative_to(root).as_posix(),
        "preview_report_sha256": actual_preview_hash,
        "files": {
            relative: file_hash(snapshot_dir / relative) for relative in MUTABLE_PATHS
        },
    }
    (snapshot_dir / "snapshot_manifest.json").write_text(
        json.dumps(snapshot_manifest, indent=2) + "\n", encoding="utf-8",
    )

    applied = False
    try:
        atomic_write(
            root / "visualdiff/annotations/visualdiff_pairs.jsonl",
            resolved_artifacts["pairs_preview"].read_bytes(),
        )
        atomic_write(root / "eng_bench.jsonl", resolved_artifacts["unified_preview"].read_bytes())
        applied = True
        validation = validate_active(root, expected_ids)
    except Exception:
        if applied:
            for relative in MUTABLE_PATHS:
                atomic_write(root / relative, (snapshot_dir / relative).read_bytes())
        raise

    active_after = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    unchanged_paths = set(ACTIVE_PATHS) - set(MUTABLE_PATHS)
    if any(active_before[path] != active_after[path] for path in unchanged_paths):
        for relative in MUTABLE_PATHS:
            atomic_write(root / relative, (snapshot_dir / relative).read_bytes())
        raise ValueError("unexpected_active_file_changed")
    report = {
        "goal": "Gold v2.0 Global",
        "status": "APPLIED",
        "policy_version": POLICY_VERSION,
        "project_id": preview.get("project_id"),
        "preview_report": preview_path.relative_to(root).as_posix(),
        "preview_report_sha256": actual_preview_hash,
        "snapshot_dir": snapshot_dir.relative_to(root).as_posix(),
        "existing_gold_rows_geometrically_repaired": len(expected_ids),
        "gold_rows_added": 0,
        "gold_rows_removed": 0,
        "descriptions_modified": 0,
        "split_membership_modified": False,
        "validation": validation,
        "active_hashes_before": active_before,
        "active_hashes_after": active_after,
        "changed_active_files": sorted(
            path for path in ACTIVE_PATHS if active_before[path] != active_after[path]
        ),
        "rollback_ready": True,
    }
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preview-report", type=Path, required=True)
    parser.add_argument("--preview-report-sha256", required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = apply_preview(
        root, args.preview_report, args.preview_report_sha256,
        root / args.snapshot_dir, root / args.output_report,
    )
    print(json.dumps({
        key: report[key] for key in (
            "status", "project_id", "existing_gold_rows_geometrically_repaired",
            "gold_rows_added", "gold_rows_removed", "descriptions_modified",
            "changed_active_files", "validation", "rollback_ready",
        )
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
