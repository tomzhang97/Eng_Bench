#!/usr/bin/env python3
"""Verify a standalone VisualDiff human-review pack before handoff."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image


EVIDENCE_DIRS = {
    "old_crop_path": "old",
    "new_crop_path": "new",
    "panel_path": "panels",
    "old_page_path": "pages_old",
    "new_page_path": "pages_new",
}
VALID_SPLITS = {"train", "dev", "test"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def row_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or "").strip()


def reserved_split(row: dict[str, Any]) -> str:
    return str(row.get("reserved_split") or row.get("split") or "").strip().lower()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def evidence_path(pack_dir: Path, field: str, value: Any) -> Path:
    return pack_dir / EVIDENCE_DIRS[field] / Path(str(value or "")).name


def audit_pack(root: Path, pack_dir: Path, queue_path: Path) -> dict[str, Any]:
    root = root.resolve()
    pack_dir = pack_dir.resolve()
    queue_path = queue_path.resolve()
    issues: list[str] = []
    required = {
        "manifest": pack_dir / "manifest.jsonl",
        "index": pack_dir / "index.html",
        "readme": pack_dir / "README.md",
        "instructions_zh": pack_dir / "INTERN_REVIEW_STEPS_ZH.md",
        "checklist": pack_dir / "validation_checklist.csv",
    }
    for label, path in required.items():
        if not path.is_file():
            issues.append(f"missing {label}: {path}")
    if not queue_path.is_file():
        issues.append(f"missing queue: {queue_path}")
    if issues:
        return {
            "goal": "Gold v2.0 Global",
            "valid": False,
            "pack_dir": pack_dir.as_posix(),
            "queue": queue_path.as_posix(),
            "issues": issues,
        }

    manifest_rows = read_jsonl(required["manifest"])
    queue_rows = read_jsonl(queue_path)
    checklist_rows = read_csv(required["checklist"])
    manifest_ids = [row_id(row) for row in manifest_rows]
    queue_ids = [row_id(row) for row in queue_rows]
    checklist_ids = [str(row.get("pair_id") or "").strip() for row in checklist_rows]

    for label, values in (
        ("manifest", manifest_ids),
        ("queue", queue_ids),
        ("checklist", checklist_ids),
    ):
        blanks = sum(not value for value in values)
        duplicates = [value for value, count in Counter(values).items() if value and count > 1]
        if blanks:
            issues.append(f"{label} has {blanks} blank pair_id values")
        if duplicates:
            issues.append(f"{label} has {len(duplicates)} duplicate pair_id values")
    if manifest_ids != queue_ids:
        issues.append("manifest pair order/identity differs from queue")
    if checklist_ids != queue_ids:
        issues.append("checklist pair order/identity differs from queue")

    queue_splits = [reserved_split(row) for row in queue_rows]
    manifest_splits = [reserved_split(row) for row in manifest_rows]
    checklist_splits = [str(row.get("split") or "").strip().lower() for row in checklist_rows]
    if queue_splits != manifest_splits:
        issues.append("manifest split reservations differ from queue")
    if queue_splits != checklist_splits:
        issues.append("checklist split reservations differ from queue")
    invalid_splits = sorted({split for split in queue_splits if split not in VALID_SPLITS})
    if invalid_splits:
        issues.append(f"invalid or provisional split reservations: {invalid_splits}")

    unsafe_rows = [row_id(row) for row in queue_rows if row.get("safe_to_merge_gold") is not False]
    if unsafe_rows:
        issues.append(f"queue has {len(unsafe_rows)} rows not explicitly Gold-safe=false")

    gold_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    gold_ids = {row_id(row) for row in read_jsonl(gold_path)} if gold_path.is_file() else set()
    gold_collisions = sorted(set(queue_ids) & gold_ids)
    if gold_collisions:
        issues.append(f"queue has {len(gold_collisions)} pair IDs already in active Gold")

    index_text = required["index"].read_text(encoding="utf-8")
    missing_evidence: list[str] = []
    invalid_images: list[str] = []
    evidence_counts: Counter[str] = Counter()
    for row in manifest_rows:
        identifier = row_id(row)
        for field in EVIDENCE_DIRS:
            value = row.get(field)
            if not str(value or "").strip():
                missing_evidence.append(f"{identifier}:{field}:blank")
                continue
            path = evidence_path(pack_dir, field, value)
            if not path.is_file():
                missing_evidence.append(f"{identifier}:{field}:{path.name}")
                continue
            evidence_counts[field] += 1
            try:
                with Image.open(path) as image:
                    image.verify()
            except Exception as exc:  # pragma: no cover - Pillow details vary
                invalid_images.append(f"{identifier}:{field}:{exc}")
            if field == "panel_path" and path.name not in index_text:
                issues.append(f"index.html does not reference panel for {identifier}")
    if missing_evidence:
        issues.append(f"missing {len(missing_evidence)} evidence paths")
    if invalid_images:
        issues.append(f"found {len(invalid_images)} invalid evidence images")

    human_columns = ("human_status", "human_description", "human_notes")
    prefilled_cells = sum(
        bool(str(row.get(field) or "").strip())
        for row in checklist_rows
        for field in human_columns
    )

    return {
        "goal": "Gold v2.0 Global",
        "valid": not issues,
        "pack_dir": pack_dir.as_posix(),
        "queue": queue_path.as_posix(),
        "counts": {
            "queue_rows": len(queue_rows),
            "manifest_rows": len(manifest_rows),
            "checklist_rows": len(checklist_rows),
            "unique_pair_ids": len(set(queue_ids)),
            "gold_id_collisions": len(gold_collisions),
            "missing_evidence_paths": len(missing_evidence),
            "invalid_images": len(invalid_images),
            "prefilled_human_cells": prefilled_cells,
            "evidence_by_field": dict(sorted(evidence_counts.items())),
            "rows_by_reserved_split": dict(sorted(Counter(queue_splits).items())),
        },
        "active_gold_modified": False,
        "issues": issues,
        "missing_evidence": missing_evidence,
        "invalid_images": invalid_images,
        "gold_collisions": gold_collisions,
    }


def zip_issues(archive: zipfile.ZipFile) -> tuple[list[str], str]:
    issues: list[str] = []
    names = archive.namelist()
    bad_member = archive.testzip()
    if bad_member:
        issues.append(f"CRC failure: {bad_member}")
    if len(names) != len(set(names)):
        issues.append("duplicate ZIP entries")
    if any(name.lower().endswith(".zip") for name in names):
        issues.append("nested ZIP entries")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            issues.append(f"unsafe ZIP entry: {name}")
            break
    roots = sorted({PurePosixPath(name).parts[0] for name in names if name})
    if len(roots) != 1:
        issues.append(f"expected one ZIP root, found {roots}")
    return issues, roots[0] if len(roots) == 1 else ""


def audit_zip(root: Path, zip_path: Path, queue_path: Path) -> dict[str, Any]:
    zip_path = zip_path.resolve()
    with tempfile.TemporaryDirectory() as directory:
        extract_root = Path(directory)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                issues, single_root = zip_issues(archive)
                if not issues:
                    archive.extractall(extract_root)
        except zipfile.BadZipFile:
            issues, single_root = ["invalid ZIP archive"], ""
        if issues:
            return {
                "goal": "Gold v2.0 Global",
                "valid": False,
                "zip_path": zip_path.as_posix(),
                "issues": issues,
            }
        report = audit_pack(root, extract_root / single_root, queue_path)
    report["zip_path"] = zip_path.as_posix()
    report["zip_sha256"] = file_sha256(zip_path)
    report["zip_single_root"] = single_root
    return report


def write_report(report: dict[str, Any], output_json: Path | None, output_md: Path | None) -> None:
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if output_md:
        counts = report.get("counts", {})
        lines = [
            "# VisualDiff Review Pack Verification",
            "",
            f"- Result: **{'PASS' if report.get('valid') else 'FAIL'}**",
            f"- Queue rows: `{counts.get('queue_rows', 0)}`",
            f"- Manifest rows: `{counts.get('manifest_rows', 0)}`",
            f"- Checklist rows: `{counts.get('checklist_rows', 0)}`",
            f"- Unique pair IDs: `{counts.get('unique_pair_ids', 0)}`",
            f"- Gold ID collisions: `{counts.get('gold_id_collisions', 0)}`",
            f"- Missing evidence paths: `{counts.get('missing_evidence_paths', 0)}`",
            f"- Invalid images: `{counts.get('invalid_images', 0)}`",
            f"- Active Gold modified: `{str(report.get('active_gold_modified', False)).lower()}`",
            "",
            "## Issues",
            "",
        ]
        lines.extend(f"- {issue}" for issue in report.get("issues", []))
        if not report.get("issues"):
            lines.append("- None.")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pack-dir", type=Path)
    target.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    queue = args.queue if args.queue.is_absolute() else root / args.queue
    if args.pack_dir:
        pack_dir = args.pack_dir if args.pack_dir.is_absolute() else root / args.pack_dir
        report = audit_pack(root, pack_dir, queue)
    else:
        zip_path = args.zip_path if args.zip_path.is_absolute() else root / args.zip_path
        report = audit_zip(root, zip_path, queue)
    write_report(report, args.output_json, args.output_md)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
