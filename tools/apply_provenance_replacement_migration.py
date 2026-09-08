#!/usr/bin/env python3
"""Atomically apply a pinned, green provenance-replacement preview.

The command is a dry run unless ``--apply`` is supplied. It accepts only a
cryptographically pinned report from ``preview_provenance_replacement_migration.py``.
Apply mode snapshots every active release file, stages split membership changes,
validates the complete replacement release, and rolls back on any post-write
failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
SPLITS_DIR = ROOT_DIR / "splits"
for module_dir in (TOOLS_DIR, SPLITS_DIR):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

import audit_question_leakage
import leakage_check
import preview_provenance_replacement_migration as migration
import preview_reviewed_gold_promotion as promotion
import unify_dataset
import validate_engbench_v2
from apply_reviewed_gold_promotion import validate_prepared_rows


ARTIFACT_TARGETS = {
    "combined_microtext_items": "microtext/annotations/microtext_items.jsonl",
    "combined_microtext_questions": "microtext/annotations/microtext_questions.jsonl",
    "combined_visualdiff_pairs": "visualdiff/annotations/visualdiff_pairs.jsonl",
    "combined_visualdiff_questions": "visualdiff/annotations/visualdiff_questions.jsonl",
    "unified": "eng_bench.jsonl",
}
REQUIRED_ARTIFACTS = set(ARTIFACT_TARGETS) | {
    "inserted_microtext_items",
    "inserted_visualdiff_pairs",
    "removed_active_rows",
    "eligibility_holds",
}
SPLITS = ("train", "dev", "test")
MUTABLE_PATHS = (
    *ARTIFACT_TARGETS.values(),
    "splits/microtext_train.txt",
    "splits/microtext_dev.txt",
    "splits/microtext_test.txt",
    "splits/visualdiff_train.txt",
    "splits/visualdiff_dev.txt",
    "splits/visualdiff_test.txt",
)


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def require_under(root: Path, path: Path, relative_parent: str, label: str) -> Path:
    resolved = resolve(root, path).resolve()
    allowed = (root / relative_parent).resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"{label} must be under {relative_parent}")
    return resolved


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def load_preview_report(
    root: Path,
    report_path: Path,
    expected_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    path = require_under(root, report_path, "derived/quality", "preview report")
    if not path.is_file():
        raise ValueError(f"preview report does not exist: {path}")
    actual = promotion.file_sha256(path).lower()
    expected = expected_sha256.strip().lower()
    if not expected or actual != expected:
        raise ValueError(f"preview_report_sha256_mismatch:{actual}:{expected or 'blank'}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("mode") != "read_only_atomic_migration_preview":
        raise ValueError("preview_report_mode_is_not_atomic_migration_preview")
    if report.get("ready_for_atomic_apply") is not True:
        raise ValueError("preview_report_not_ready_for_atomic_apply")
    if report.get("active_gold_modified") is not False:
        raise ValueError("preview_report_says_active_gold_was_modified")
    gates = report.get("gates") or {}
    if not gates or not all(value is True for value in gates.values()):
        raise ValueError("preview_report_has_nonpassing_gate")
    if report.get("active_file_hashes_before") != report.get("active_file_hashes_after"):
        raise ValueError("preview_active_before_after_hashes_differ")
    counts = report.get("counts") or {}
    if int(counts.get("affected_rows") or 0) <= 0:
        raise ValueError("preview_report_has_no_affected_rows")
    if int(counts.get("affected_rows") or 0) != int(counts.get("inserted_rows") or 0):
        raise ValueError("preview_report_is_not_one_for_one")
    return path, report


def verify_active_prestate(root: Path, report: dict[str, Any]) -> dict[str, str]:
    expected = report.get("active_file_hashes_before") or {}
    missing = sorted(set(promotion.ACTIVE_PATHS) - set(expected))
    if missing:
        raise ValueError("preview_missing_active_hashes:" + ",".join(missing))
    actual: dict[str, str] = {}
    for relative in promotion.ACTIVE_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"active_file_missing:{relative}")
        actual[relative] = promotion.file_sha256(path).lower()
        expected_hash = str(expected[relative]).lower()
        if actual[relative] != expected_hash:
            raise ValueError(
                f"active_file_changed_since_preview:{relative}:"
                f"{actual[relative]}:{expected_hash}"
            )
    return actual


def resolve_artifacts(root: Path, report: dict[str, Any]) -> dict[str, Path]:
    artifacts = report.get("artifacts") or {}
    hashes = report.get("artifact_sha256") or {}
    missing = sorted(REQUIRED_ARTIFACTS - set(artifacts))
    if missing:
        raise ValueError("preview_missing_artifacts:" + ",".join(missing))
    resolved: dict[str, Path] = {}
    for key in REQUIRED_ARTIFACTS:
        path = require_under(root, Path(str(artifacts[key])), "derived/quality", f"artifact {key}")
        if not path.is_file():
            raise ValueError(f"preview_artifact_missing:{key}:{path}")
        actual = promotion.file_sha256(path).lower()
        expected = str(hashes.get(key) or "").lower()
        if not expected or actual != expected:
            raise ValueError(f"preview_artifact_sha256_mismatch:{key}:{actual}:{expected or 'blank'}")
        resolved[key] = path
    if promotion.read_jsonl(resolved["eligibility_holds"]):
        raise ValueError("preview_eligibility_holds_artifact_is_not_empty")
    return resolved


def split_file(path: Path) -> tuple[bytes, list[str], str, bool]:
    data = path.read_bytes()
    text = data.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith("\n") or text.endswith("\r")
    lines = text.splitlines()
    entries = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if len(entries) != len(set(entries)):
        raise ValueError(f"duplicate_entries_in_active_split:{path.as_posix()}")
    return data, lines, newline, trailing


def represented_units(rows: list[dict[str, Any]], task: str) -> dict[str, str]:
    field = "doc_id" if task == "microtext" else "project_id"
    result: dict[str, str] = {}
    for row in rows:
        unit = str(row.get(field) or "").strip()
        split = str(row.get("split") or "").strip().lower()
        if not unit or split not in SPLITS:
            identity = row.get("item_id") or row.get("pair_id") or "blank"
            raise ValueError(f"invalid_{task}_split_unit:{identity}:{unit}:{split}")
        old = result.get(unit)
        if old and old != split:
            raise ValueError(f"combined_split_conflict:{task}:{unit}:{old}:{split}")
        result[unit] = split
    return result


def staged_split_bytes(
    root: Path,
    active_items: list[dict[str, Any]],
    active_pairs: list[dict[str, Any]],
    combined_items: list[dict[str, Any]],
    combined_pairs: list[dict[str, Any]],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    active_units = {
        "microtext": represented_units(active_items, "microtext"),
        "visualdiff": represented_units(active_pairs, "visualdiff"),
    }
    combined_units = {
        "microtext": represented_units(combined_items, "microtext"),
        "visualdiff": represented_units(combined_pairs, "visualdiff"),
    }
    assignments: dict[tuple[str, str], str] = {}
    source: dict[tuple[str, str], tuple[bytes, list[str], str, bool]] = {}
    for task in ("microtext", "visualdiff"):
        for split in SPLITS:
            relative = f"splits/{task}_{split}.txt"
            parsed = split_file(root / relative)
            source[(task, split)] = parsed
            for unit in [
                line.strip()
                for line in parsed[1]
                if line.strip() and not line.lstrip().startswith("#")
            ]:
                old = assignments.get((task, unit))
                if old and old != split:
                    raise ValueError(f"active_split_conflict:{task}:{unit}:{old}:{split}")
                assignments[(task, unit)] = split

    retired = {
        task: sorted(set(active_units[task]) - set(combined_units[task]))
        for task in ("microtext", "visualdiff")
    }
    added = {
        task: sorted(set(combined_units[task]) - set(active_units[task]))
        for task in ("microtext", "visualdiff")
    }
    staged: dict[str, bytes] = {}
    by_split: dict[str, dict[str, list[str]]] = {}
    for task in ("microtext", "visualdiff"):
        retired_set = set(retired[task])
        for unit, split in combined_units[task].items():
            old = assignments.get((task, unit))
            if old and old != split:
                raise ValueError(f"replacement_split_conflict:{task}:{unit}:{old}:{split}")
        for split in SPLITS:
            relative = f"splits/{task}_{split}.txt"
            raw, lines, newline, trailing = source[(task, split)]
            kept = [line for line in lines if line.strip() not in retired_set]
            additions = [
                unit
                for unit in added[task]
                if combined_units[task][unit] == split and (task, unit) not in assignments
            ]
            if len(kept) == len(lines) and not additions:
                staged[relative] = raw
            else:
                kept.extend(additions)
                text = newline.join(kept)
                if text and (trailing or additions):
                    text += newline
                staged[relative] = text.encode("utf-8")
            by_split[f"{task}:{split}"] = {
                "added": additions,
                "retired": [
                    unit
                    for unit in retired[task]
                    if assignments.get((task, unit)) == split
                ],
            }
    return staged, {
        "retired_units": retired,
        "added_units": added,
        "by_split": by_split,
    }


def source_state(
    root: Path,
    items: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> tuple[set[str], list[str], dict[str, list[str]]]:
    docs, manifest_pairs = promotion.manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or ""): row
        for row in promotion.read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    doc_ids, resolution_issues = migration.referenced_source_docs(
        items, pairs, docs, manifest_pairs
    )
    source_issues = {
        doc_id: promotion.source_audit(root, doc_id, docs, inventory)
        for doc_id in sorted(doc_ids)
    }
    return doc_ids, resolution_issues, {
        doc_id: issues for doc_id, issues in source_issues.items() if issues
    }


def write_validation_root(
    validation_root: Path,
    root: Path,
    split_bytes: dict[str, bytes],
) -> None:
    (validation_root / "splits").mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "manifest.jsonl", validation_root / "manifest.jsonl")
    for relative, data in split_bytes.items():
        path = validation_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def validate_split_membership(
    split_bytes: dict[str, bytes],
    items: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> None:
    maps: dict[str, dict[str, str]] = {"microtext": {}, "visualdiff": {}}
    for task in maps:
        for split in SPLITS:
            text = split_bytes[f"splits/{task}_{split}.txt"].decode("utf-8")
            for line in text.splitlines():
                unit = line.strip()
                if unit and not unit.startswith("#"):
                    old = maps[task].get(unit)
                    if old and old != split:
                        raise ValueError(f"staged_split_conflict:{task}:{unit}:{old}:{split}")
                    maps[task][unit] = split
    issues: list[str] = []
    for row in items:
        unit = str(row.get("doc_id") or "")
        if maps["microtext"].get(unit) != str(row.get("split") or ""):
            issues.append(f"microtext:{row.get('item_id')}:{unit}:{row.get('split')}")
    for row in pairs:
        unit = str(row.get("project_id") or "")
        if maps["visualdiff"].get(unit) != str(row.get("split") or ""):
            issues.append(f"visualdiff:{row.get('pair_id')}:{unit}:{row.get('split')}")
    if issues:
        raise ValueError("annotation_split_membership_failed:" + ";".join(issues[:10]))


def row_ids(rows: list[dict[str, Any]], field: str, label: str) -> set[str]:
    values = [str(row.get(field) or "").strip() for row in rows]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(f"{label}_missing_or_duplicate_id")
    return set(values)


def validate_staged_release(
    root: Path,
    preview: dict[str, Any],
    artifacts: dict[str, Path],
    split_bytes: dict[str, bytes],
) -> dict[str, Any]:
    active_items = promotion.read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_micro_questions = promotion.read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    active_pairs = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    active_visual_questions = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    active_unified = promotion.read_jsonl(root / "eng_bench.jsonl")
    items = promotion.read_jsonl(artifacts["combined_microtext_items"])
    micro_questions = promotion.read_jsonl(artifacts["combined_microtext_questions"])
    pairs = promotion.read_jsonl(artifacts["combined_visualdiff_pairs"])
    visual_questions = promotion.read_jsonl(artifacts["combined_visualdiff_questions"])
    unified = promotion.read_jsonl(artifacts["unified"])
    inserted_micro = promotion.read_jsonl(artifacts["inserted_microtext_items"])
    inserted_visual = promotion.read_jsonl(artifacts["inserted_visualdiff_pairs"])
    removed = promotion.read_jsonl(artifacts["removed_active_rows"])
    counts = preview.get("counts") or {}

    validate_prepared_rows(inserted_micro, inserted_visual)
    if len(removed) != int(counts.get("removed_rows") or 0):
        raise ValueError("removed_row_count_mismatch")
    if len(inserted_micro) != int(counts.get("inserted_microtext_rows") or 0):
        raise ValueError("inserted_microtext_count_mismatch")
    if len(inserted_visual) != int(counts.get("inserted_visualdiff_rows") or 0):
        raise ValueError("inserted_visualdiff_count_mismatch")
    if len(unified) != len(active_unified):
        raise ValueError("migration_does_not_preserve_unified_row_count")

    removed_ids = {str(row.get("active_id") or "").strip() for row in removed}
    if not removed_ids or "" in removed_ids or len(removed_ids) != len(removed):
        raise ValueError("removed_ledger_has_missing_or_duplicate_active_ids")
    active_ids = row_ids(active_unified, "id", "active_unified")
    combined_ids = row_ids(unified, "id", "combined_unified")
    if not removed_ids.issubset(active_ids):
        raise ValueError("removed_ledger_references_nonactive_ids")
    if removed_ids & combined_ids:
        raise ValueError("removed_ids_still_present_in_combined_unified")
    inserted_ids = combined_ids - active_ids
    if len(inserted_ids) != len(inserted_micro) + len(inserted_visual):
        raise ValueError("inserted_unified_id_count_mismatch")
    if combined_ids != (active_ids - removed_ids) | inserted_ids:
        raise ValueError("combined_unified_id_set_mismatch")

    rebuilt = unify_dataset.process_visualdiff(
        root, artifacts["combined_visualdiff_pairs"], artifacts["combined_visualdiff_questions"]
    ) + unify_dataset.process_microtext(
        root, artifacts["combined_microtext_items"], artifacts["combined_microtext_questions"]
    )
    if rebuilt != unified:
        raise ValueError("unified_artifact_does_not_match_combined_annotations")
    if migration.task_split_counts(active_unified) != migration.task_split_counts(unified):
        raise ValueError("task_split_counts_changed")

    annotation_issues = promotion.annotation_errors(
        pairs, visual_questions, items, micro_questions
    )
    if annotation_issues:
        raise ValueError("annotation_validation_failed:" + ";".join(annotation_issues[:10]))
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict, bad = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    if strict.errors or bad:
        raise ValueError("strict_unified_validation_failed:" + ";".join(strict.errors[:10]))
    question_leakage = audit_question_leakage.audit(unified)
    if question_leakage["critical_failures"]:
        raise ValueError(f"question_answer_leakage:{question_leakage['critical_failures']}")
    docs, manifest_pairs = promotion.manifest_maps(root)
    combined_leaks = promotion.preview_split_leakage(items, pairs, docs, manifest_pairs)
    if combined_leaks:
        raise ValueError("combined_split_leakage:" + ";".join(combined_leaks[:10]))
    validate_split_membership(split_bytes, items, pairs)
    with tempfile.TemporaryDirectory(dir=root / "derived" / "quality") as temp:
        validation_root = Path(temp)
        write_validation_root(validation_root, root, split_bytes)
        split_issues = leakage_check.check_leakage(validation_root)
    if split_issues:
        raise ValueError("staged_split_files_leak:" + ";".join(split_issues[:10]))

    before_docs, before_resolution, before_source_issues = source_state(root, active_items, active_pairs)
    after_docs, after_resolution, after_source_issues = source_state(root, items, pairs)
    delta = migration.source_provenance_delta(
        source_atomic=preview.get("source_atomic") is True,
        active_resolution_issues=before_resolution,
        preview_resolution_issues=after_resolution,
        active_source_issues=before_source_issues,
        preview_source_issues=after_source_issues,
    )
    if not delta["passes"]:
        raise ValueError("source_provenance_regression:" + json.dumps(delta, sort_keys=True))
    blocked_docs = {
        str(doc_id)
        for row in removed
        for doc_id in (row.get("blocked_source_doc_ids") or [])
        if doc_id
    }
    remaining_blocked = sorted(blocked_docs & after_docs)
    if remaining_blocked:
        raise ValueError("target_blocked_sources_still_active:" + ",".join(remaining_blocked))
    expected_retired = set((preview.get("source_provenance_delta") or {}).get("retired_source_issues") or {})
    if expected_retired and not expected_retired.issubset(set(delta["retired_source_issues"])):
        raise ValueError("preview_retired_source_delta_not_reproduced")

    return {
        "active_rows_before": len(active_unified),
        "active_rows_after": len(unified),
        "removed_rows": len(removed),
        "inserted_rows": len(inserted_micro) + len(inserted_visual),
        "strict_errors": 0,
        "split_leaks": 0,
        "question_leaks": 0,
        "source_provenance_regressions": 0,
        "retired_source_docs": sorted(before_docs - after_docs),
        "new_source_docs": sorted(after_docs - before_docs),
        "remaining_provenance_issue_docs": len(after_source_issues),
        "task_split_counts": migration.task_split_counts(unified),
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.provenance-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def create_snapshot(root: Path, snapshot_dir: Path, before_hashes: dict[str, str]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if any(snapshot_dir.iterdir()):
        raise ValueError(f"snapshot_directory_not_empty:{snapshot_dir}")
    for relative in promotion.ACTIVE_PATHS:
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
    (snapshot_dir / "snapshot_manifest.json").write_text(
        json.dumps(
            {
                "goal": "Gold v2.0 Global",
                "mode": "provenance_replacement_migration_snapshot",
                "root": root.as_posix(),
                "files": before_hashes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def restore_snapshot(root: Path, snapshot_dir: Path, before_hashes: dict[str, str]) -> None:
    for relative in promotion.ACTIVE_PATHS:
        source = snapshot_dir / relative
        if not source.is_file():
            raise RuntimeError(f"rollback_snapshot_file_missing:{relative}")
        atomic_write(root / relative, source.read_bytes())
    restored = {
        relative: promotion.file_sha256(root / relative).lower()
        for relative in promotion.ACTIVE_PATHS
    }
    if restored != before_hashes:
        raise RuntimeError("rollback_hash_verification_failed")


def post_apply_validation(
    root: Path,
    expected_hashes: dict[str, str],
    preview: dict[str, Any],
    split_bytes: dict[str, bytes],
) -> dict[str, Any]:
    actual = {
        relative: promotion.file_sha256(root / relative).lower()
        for relative in promotion.ACTIVE_PATHS
    }
    mismatches = [
        f"{relative}:{actual.get(relative)}:{expected}"
        for relative, expected in expected_hashes.items()
        if actual.get(relative) != expected
    ]
    if mismatches:
        raise ValueError("post_apply_hash_mismatch:" + ";".join(mismatches[:10]))

    items = promotion.read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    micro_questions = promotion.read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    pairs = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    visual_questions = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    unified = promotion.read_jsonl(root / "eng_bench.jsonl")
    counts = preview.get("counts") or {}
    if len(unified) != int(counts.get("preview_gold_rows") or -1):
        raise ValueError("post_apply_unified_row_count_mismatch")
    if migration.task_split_counts(unified) != (preview.get("preview_task_split_counts") or {}):
        raise ValueError("post_apply_task_split_counts_mismatch")

    annotation_issues = promotion.annotation_errors(
        pairs, visual_questions, items, micro_questions
    )
    if annotation_issues:
        raise ValueError(
            "post_apply_annotation_validation_failed:" + ";".join(annotation_issues[:10])
        )
    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict, bad = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    if strict.errors or bad:
        raise ValueError("post_apply_strict_validation_failed:" + ";".join(strict.errors[:10]))
    split_issues = leakage_check.check_leakage(root)
    if split_issues:
        raise ValueError("post_apply_split_leakage:" + ";".join(split_issues[:10]))
    validate_split_membership(split_bytes, items, pairs)
    question_leakage = audit_question_leakage.audit(unified)
    if question_leakage["critical_failures"]:
        raise ValueError(
            f"post_apply_question_answer_leakage:{question_leakage['critical_failures']}"
        )

    active_docs, resolution_issues, source_issues = source_state(root, items, pairs)
    expected_resolution = preview.get("source_resolution_issues") or []
    expected_source_issues = preview.get("source_provenance_issues") or {}
    if resolution_issues != expected_resolution:
        raise ValueError("post_apply_source_resolution_state_differs_from_preview")
    if source_issues != expected_source_issues:
        raise ValueError("post_apply_source_provenance_state_differs_from_preview")
    blocked_docs = {
        str(doc_id)
        for doc_id in (
            (preview.get("migration_readiness") or {}).get("blocked_active_source_docs")
            or []
        )
        if doc_id
    }
    if not blocked_docs:
        blocked_docs = set(
            (preview.get("source_provenance_delta") or {}).get("retired_source_issues") or {}
        )
    remaining_blocked = sorted(blocked_docs & active_docs)
    if remaining_blocked:
        raise ValueError(
            "post_apply_target_blocked_sources_still_active:" + ",".join(remaining_blocked)
        )
    return {
        "active_rows": len(unified),
        "strict_errors": 0,
        "split_leaks": 0,
        "question_leaks": 0,
        "source_provenance_regressions": 0,
        "retired_source_docs": sorted(blocked_docs),
        "remaining_provenance_issue_docs": len(source_issues),
        "task_split_counts": migration.task_split_counts(unified),
        "active_hashes_verified": len(actual),
    }


def build_transaction(
    root: Path,
    report_path: Path,
    expected_report_sha256: str,
) -> tuple[
    dict[str, Any],
    dict[str, bytes],
    dict[str, str],
    dict[str, Any],
    dict[str, Path],
    dict[str, bytes],
]:
    root = root.resolve()
    resolved_report, preview = load_preview_report(root, report_path, expected_report_sha256)
    before_hashes = verify_active_prestate(root, preview)
    artifacts = resolve_artifacts(root, preview)
    active_items = promotion.read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    active_pairs = promotion.read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    combined_items = promotion.read_jsonl(artifacts["combined_microtext_items"])
    combined_pairs = promotion.read_jsonl(artifacts["combined_visualdiff_pairs"])
    split_bytes, split_delta = staged_split_bytes(
        root, active_items, active_pairs, combined_items, combined_pairs
    )
    validation = validate_staged_release(root, preview, artifacts, split_bytes)
    staged = {
        relative: artifacts[key].read_bytes()
        for key, relative in ARTIFACT_TARGETS.items()
    }
    staged.update(split_bytes)
    expected_after = dict(before_hashes)
    for relative, data in staged.items():
        expected_after[relative] = sha256_bytes(data)
    transaction = {
        "goal": "Gold v2.0 Global",
        "mode": "provenance_replacement_migration_transaction",
        "preview_report": resolved_report.relative_to(root).as_posix(),
        "preview_report_sha256": promotion.file_sha256(resolved_report),
        "ready_to_apply": True,
        "applied": False,
        "rolled_back": False,
        "before_hashes": before_hashes,
        "expected_after_hashes": expected_after,
        "split_delta": split_delta,
        "validation": validation,
    }
    return transaction, staged, before_hashes, preview, artifacts, split_bytes


def apply_transaction(
    root: Path,
    report_path: Path,
    expected_report_sha256: str,
    *,
    snapshot_dir: Path | None = None,
    apply: bool = False,
    fail_after_replacements: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    transaction, staged, before_hashes, preview, artifacts, split_bytes = build_transaction(
        root, report_path, expected_report_sha256
    )
    if not apply:
        return transaction
    if snapshot_dir is None:
        raise ValueError("snapshot_dir_is_required_for_apply")
    snapshot = require_under(root, snapshot_dir, "derived/snapshots", "snapshot directory")
    create_snapshot(root, snapshot, before_hashes)
    transaction["snapshot_dir"] = snapshot.relative_to(root).as_posix()
    replacements = 0
    try:
        for relative in MUTABLE_PATHS:
            atomic_write(root / relative, staged[relative])
            replacements += 1
            if fail_after_replacements is not None and replacements >= fail_after_replacements:
                raise RuntimeError("injected_post_replace_failure")
        post = post_apply_validation(
            root,
            transaction["expected_after_hashes"],
            preview,
            split_bytes,
        )
        transaction.update(
            {
                "applied": True,
                "after_hashes": {
                    relative: promotion.file_sha256(root / relative).lower()
                    for relative in promotion.ACTIVE_PATHS
                },
                "post_apply_validation": post,
            }
        )
        return transaction
    except Exception as exc:
        try:
            restore_snapshot(root, snapshot, before_hashes)
            transaction.update(
                {
                    "applied": False,
                    "rolled_back": True,
                    "failure": f"{type(exc).__name__}:{exc}",
                    "restored_hashes": {
                        relative: promotion.file_sha256(root / relative).lower()
                        for relative in promotion.ACTIVE_PATHS
                    },
                }
            )
        except Exception as rollback_exc:
            transaction.update(
                {
                    "applied": False,
                    "rolled_back": False,
                    "failure": f"{type(exc).__name__}:{exc}",
                    "rollback_failure": f"{type(rollback_exc).__name__}:{rollback_exc}",
                }
            )
        return transaction


def write_report(root: Path, path: Path, report: dict[str, Any]) -> Path:
    output = require_under(root, path, "derived/quality", "transaction report")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preview-report", type=Path, required=True)
    parser.add_argument("--expected-preview-sha256", required=True)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    try:
        output = require_under(root, args.report, "derived/quality", "transaction report")
        output.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    try:
        report = apply_transaction(
            root,
            args.preview_report,
            args.expected_preview_sha256,
            snapshot_dir=args.snapshot_dir,
            apply=args.apply,
        )
        write_report(root, output, report)
        print(f"[OK] Wrote {output}")
        print(
            json.dumps(
                {
                    "ready_to_apply": report.get("ready_to_apply"),
                    "applied": report.get("applied"),
                    "rolled_back": report.get("rolled_back"),
                    "validation": report.get("post_apply_validation") or report.get("validation"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        refusal = {
            "goal": "Gold v2.0 Global",
            "mode": "provenance_replacement_migration_transaction_refusal",
            "preview_report": str(args.preview_report).replace("\\", "/"),
            "expected_preview_sha256": args.expected_preview_sha256,
            "ready_to_apply": False,
            "applied": False,
            "rolled_back": False,
            "error": f"{type(exc).__name__}:{exc}",
        }
        try:
            write_report(root, output, refusal)
            print(f"[OK] Wrote refusal report {output}")
        except OSError as report_exc:
            print(f"[ERROR] Could not write refusal report: {report_exc}")
        print(f"[ERROR] {exc}")
        return 1
    return 0 if not args.apply or report.get("applied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
