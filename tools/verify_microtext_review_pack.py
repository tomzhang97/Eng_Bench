#!/usr/bin/env python3
"""Verify a standalone microtext human-review pack before handoff."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_source_conversion_readiness import microtext_region_identity
from verify_human_audit_embedded_evidence import (
    embedded_picture_rows,
    sha256_file,
)


FINAL_STATUSES = {"accepted", "edited", "rejected"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def root_path(root: Path, pack_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    pack_candidate = pack_dir / path
    if pack_candidate.exists():
        return pack_candidate
    return root / path


def audit_pack(
    root: Path,
    pack_dir: Path,
    queue_path: Path,
    max_image_pixels: int | None = None,
    require_embedded_evidence: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    pack_dir = pack_dir.resolve()
    queue_path = queue_path.resolve()
    issues: list[str] = []
    warnings: list[str] = []

    required = {
        "manifest": pack_dir / "manifest.jsonl",
        "index": pack_dir / "index.html",
        "readme": pack_dir / "README.md",
        "instructions_zh": pack_dir / "INTERN_INSTRUCTIONS_ZH.md",
    }
    for label, path in required.items():
        if not path.is_file():
            issues.append(f"missing {label}: {path}")

    csv_paths = sorted(pack_dir.glob("*checklist.csv"))
    xlsx_paths = sorted(pack_dir.glob("*checklist.xlsx"))
    if len(csv_paths) != 1:
        issues.append(f"expected 1 checklist CSV, found {len(csv_paths)}")
    if len(xlsx_paths) != 1:
        issues.append(f"expected 1 checklist XLSX, found {len(xlsx_paths)}")
    if not queue_path.is_file():
        issues.append(f"missing queue: {queue_path}")

    if issues:
        return {
            "valid": False,
            "root": str(root),
            "pack_dir": str(pack_dir),
            "queue": str(queue_path),
            "issues": issues,
            "warnings": warnings,
        }

    manifest_rows = read_jsonl(required["manifest"])
    queue_rows = read_jsonl(queue_path)
    checklist_rows = read_csv(csv_paths[0])
    gold_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    gold_rows = read_jsonl(gold_path) if gold_path.is_file() else []

    def ids(rows: list[dict[str, Any]], field: str) -> list[str]:
        return [str(row.get(field) or "").strip() for row in rows]

    manifest_ids = ids(manifest_rows, "candidate_id")
    queue_ids = ids(queue_rows, "candidate_id")
    checklist_ids = ids(checklist_rows, "candidate_id")
    gold_ids = {
        str(row.get("item_id") or row.get("candidate_id") or "").strip()
        for row in gold_rows
    }
    manifest_regions = [microtext_region_identity(row) for row in manifest_rows]
    queue_regions = [microtext_region_identity(row) for row in queue_rows]
    gold_regions = {
        region
        for row in gold_rows
        for region in [microtext_region_identity(row)]
        if region
    }

    for label, values in (
        ("manifest", manifest_ids),
        ("queue", queue_ids),
        ("checklist", checklist_ids),
    ):
        empty = sum(not value for value in values)
        duplicates = sorted(value for value, count in Counter(values).items() if value and count > 1)
        if empty:
            issues.append(f"{label} has {empty} blank candidate_id values")
        if duplicates:
            issues.append(f"{label} has {len(duplicates)} duplicate candidate_id values")

    if manifest_ids != queue_ids:
        issues.append("manifest candidate order/identity differs from queue")
    if checklist_ids != queue_ids:
        issues.append("checklist candidate order/identity differs from queue")
    if manifest_regions != queue_regions:
        issues.append("manifest physical-region order/identity differs from queue")

    queue_region_counts = Counter(region for region in queue_regions if region)
    duplicate_regions = sorted(
        region for region, count in queue_region_counts.items() if count > 1
    )
    if duplicate_regions:
        issues.append(f"queue has {len(duplicate_regions)} duplicate physical regions")

    gold_collisions = sorted(set(queue_ids) & gold_ids)
    if gold_collisions:
        issues.append(f"queue has {len(gold_collisions)} candidate IDs already in gold")
    gold_region_collisions = sorted(
        region for region in queue_region_counts if region in gold_regions
    )
    if gold_region_collisions:
        issues.append(
            f"queue has {len(gold_region_collisions)} physical regions already in gold"
        )

    missing_evidence: list[str] = []
    invalid_images: list[str] = []
    page_paths: set[str] = set()
    index_text = required["index"].read_text(encoding="utf-8")
    for row in manifest_rows:
        candidate_id = str(row.get("candidate_id") or "")
        for field in ("crop_path", "page_path", "image_path"):
            value = str(row.get(field) or "").strip()
            if not value:
                missing_evidence.append(f"{candidate_id}:{field}:blank")
                continue
            path = root_path(root, pack_dir, value)
            if not path.is_file():
                missing_evidence.append(f"{candidate_id}:{field}:{value}")
                continue
            if field in {"crop_path", "page_path"}:
                try:
                    previous_limit = Image.MAX_IMAGE_PIXELS
                    if max_image_pixels is not None:
                        Image.MAX_IMAGE_PIXELS = max_image_pixels
                    with Image.open(path) as image:
                        image.verify()
                except Exception as exc:  # pragma: no cover - Pillow details vary
                    invalid_images.append(f"{candidate_id}:{field}:{exc}")
                finally:
                    Image.MAX_IMAGE_PIXELS = previous_limit
            if field == "page_path":
                page_paths.add(str(path.resolve()))
        crop_name = Path(str(row.get("crop_path") or "")).name
        if crop_name and crop_name not in index_text:
            issues.append(f"index.html does not reference crop for {candidate_id}")

    if missing_evidence:
        issues.append(f"missing {len(missing_evidence)} evidence paths")
    if invalid_images:
        issues.append(f"found {len(invalid_images)} invalid crop/page images")

    statuses = Counter(str(row.get("review_status") or "").strip().lower() for row in checklist_rows)
    unexpected_statuses = sorted(status for status in statuses if status and status not in FINAL_STATUSES | {"needs_full_page"})
    if unexpected_statuses:
        warnings.append(f"checklist contains unexpected statuses: {unexpected_statuses}")

    xlsx_valid = False
    embedded_picture_count = 0
    exact_embedded_matches = 0
    embedded_evidence_valid = not require_embedded_evidence
    try:
        with zipfile.ZipFile(xlsx_paths[0]) as workbook:
            bad_member = workbook.testzip()
            names = set(workbook.namelist())
            xlsx_valid = bad_member is None and "xl/workbook.xml" in names and "[Content_Types].xml" in names
            if xlsx_valid and require_embedded_evidence:
                expected_hashes = [
                    sha256_file(root_path(root, pack_dir, str(row.get("crop_path") or "")))
                    for row in manifest_rows
                ]
                actual_pictures = embedded_picture_rows(workbook, "Review")
                embedded_picture_count = len(actual_pictures)
                actual_hashes = [str(row["media_sha256"]) for row in actual_pictures]
                actual_rows = [int(row["row"]) for row in actual_pictures]
                actual_columns = {int(row["col"]) for row in actual_pictures}
                exact_embedded_matches = sum(
                    expected == actual
                    for expected, actual in zip(expected_hashes, actual_hashes)
                )
                consecutive_rows = (
                    not actual_rows
                    or actual_rows == list(range(actual_rows[0], actual_rows[0] + len(actual_rows)))
                )
                embedded_evidence_valid = (
                    len(actual_hashes) == len(expected_hashes)
                    and actual_hashes == expected_hashes
                    and consecutive_rows
                    and len(actual_columns) == 1
                )
                if len(actual_hashes) != len(expected_hashes):
                    issues.append(
                        "checklist XLSX embedded image count differs from review rows"
                    )
                if actual_hashes != expected_hashes:
                    issues.append(
                        "checklist XLSX embedded image hash/order differs from assigned crops"
                    )
                if not consecutive_rows:
                    issues.append(
                        "checklist XLSX embedded images are not anchored to consecutive review rows"
                    )
                if len(actual_columns) != 1:
                    issues.append(
                        "checklist XLSX embedded images are not confined to one evidence column"
                    )
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        xlsx_valid = False
        if require_embedded_evidence:
            issues.append(f"checklist XLSX embedded evidence could not be verified: {exc}")
    if not xlsx_valid:
        issues.append("checklist XLSX is not a valid Office Open XML workbook")

    return {
        "valid": not issues,
        "root": str(root),
        "pack_dir": str(pack_dir),
        "queue": str(queue_path),
        "counts": {
            "queue_rows": len(queue_rows),
            "manifest_rows": len(manifest_rows),
            "checklist_rows": len(checklist_rows),
            "unique_candidate_ids": len(set(queue_ids)),
            "unique_physical_regions": len(queue_region_counts),
            "duplicate_physical_regions": len(duplicate_regions),
            "unique_full_pages": len(page_paths),
            "gold_id_collisions": len(gold_collisions),
            "gold_region_collisions": len(gold_region_collisions),
            "missing_evidence_paths": len(missing_evidence),
            "invalid_images": len(invalid_images),
            "embedded_picture_rows": embedded_picture_count,
            "exact_embedded_matches": exact_embedded_matches,
        },
        "checklist_statuses": dict(sorted(statuses.items())),
        "xlsx_valid": xlsx_valid,
        "embedded_evidence_required": require_embedded_evidence,
        "embedded_evidence_valid": embedded_evidence_valid,
        "issues": issues,
        "warnings": warnings,
        "missing_evidence": missing_evidence,
        "invalid_images": invalid_images,
        "gold_collisions": gold_collisions,
        "gold_region_collisions": gold_region_collisions,
        "duplicate_regions": duplicate_regions,
    }


def write_report(report: dict[str, Any], output_json: Path | None, output_md: Path | None) -> None:
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if output_md:
        counts = report.get("counts", {})
        lines = [
            "# Microtext Review Pack Verification",
            "",
            f"- Result: **{'PASS' if report['valid'] else 'FAIL'}**",
            f"- Queue rows: `{counts.get('queue_rows', 0)}`",
            f"- Manifest rows: `{counts.get('manifest_rows', 0)}`",
            f"- Checklist rows: `{counts.get('checklist_rows', 0)}`",
            f"- Unique candidate IDs: `{counts.get('unique_candidate_ids', 0)}`",
            f"- Unique physical regions: `{counts.get('unique_physical_regions', 0)}`",
            f"- Duplicate physical regions: `{counts.get('duplicate_physical_regions', 0)}`",
            f"- Unique full pages: `{counts.get('unique_full_pages', 0)}`",
            f"- Gold ID collisions: `{counts.get('gold_id_collisions', 0)}`",
            f"- Gold region collisions: `{counts.get('gold_region_collisions', 0)}`",
            f"- Missing evidence paths: `{counts.get('missing_evidence_paths', 0)}`",
            f"- Invalid images: `{counts.get('invalid_images', 0)}`",
            f"- XLSX valid: `{report.get('xlsx_valid', False)}`",
            "",
            "## Issues",
            "",
        ]
        lines.extend(f"- {issue}" for issue in report.get("issues", []))
        if not report.get("issues"):
            lines.append("- None.")
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.get("warnings", []))
        if not report.get("warnings"):
            lines.append("- None.")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        help="Audited Pillow pixel limit for intentionally large engineering scans",
    )
    parser.add_argument(
        "--require-embedded-evidence",
        action="store_true",
        help=(
            "Require one exact crop image embedded per review row in the Review sheet"
        ),
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    pack_dir = args.pack_dir if args.pack_dir.is_absolute() else root / args.pack_dir
    queue = args.queue if args.queue.is_absolute() else root / args.queue
    report = audit_pack(
        root,
        pack_dir,
        queue,
        args.max_image_pixels,
        require_embedded_evidence=args.require_embedded_evidence,
    )
    write_report(report, args.output_json, args.output_md)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
