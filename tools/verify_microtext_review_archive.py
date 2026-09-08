#!/usr/bin/env python3
"""Verify a flat MicroText human-review ZIP against its staged source pack."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_source_conversion_readiness import microtext_region_identity


REQUIRED_FILES = {
    "README.md",
    "INTERN_INSTRUCTIONS_ZH.md",
    "RETURN_ONLY_THIS_XLSX.txt",
    "index.html",
    "manifest.jsonl",
    "microtext_validation_checklist.csv",
    "microtext_validation_checklist.xlsx",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl_bytes(data: bytes, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label}:{line_number}: expected JSON object")
        rows.append(value)
    return rows


def read_jsonl_path(path: Path) -> list[dict[str, Any]]:
    return read_jsonl_bytes(path.read_bytes(), str(path))


def safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not name.startswith(("/", "\\"))
        and "\\" not in name
        and ".." not in path.parts
    )


def valid_png(data: bytes, max_image_pixels: int | None = None) -> bool:
    original_limit = Image.MAX_IMAGE_PIXELS
    try:
        if max_image_pixels is not None:
            Image.MAX_IMAGE_PIXELS = max_image_pixels
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit


def valid_xlsx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as workbook:
            names = set(workbook.namelist())
            return (
                workbook.testzip() is None
                and "[Content_Types].xml" in names
                and "xl/workbook.xml" in names
            )
    except zipfile.BadZipFile:
        return False


def audit_archive(
    root: Path,
    zip_path: Path,
    pack_dir: Path,
    queue_path: Path,
    max_image_pixels: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    zip_path = zip_path.resolve()
    pack_dir = pack_dir.resolve()
    queue_path = queue_path.resolve()
    issues: list[str] = []

    if not zip_path.is_file():
        return {"valid": False, "issues": [f"missing archive: {zip_path}"]}
    if not pack_dir.is_dir():
        return {"valid": False, "issues": [f"missing pack directory: {pack_dir}"]}
    if not queue_path.is_file():
        return {"valid": False, "issues": [f"missing queue: {queue_path}"]}

    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        return {"valid": False, "issues": ["archive is not a valid ZIP"]}

    with archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        name_counts = Counter(names)
        duplicate_names = sorted(name for name, count in name_counts.items() if count > 1)
        unsafe_names = sorted(name for name in names if not safe_archive_name(name))
        nested_zips = sorted(name for name in names if name.lower().endswith(".zip"))
        crc_bad_member = archive.testzip()
        name_set = set(names)

        expected_names = {
            path.relative_to(pack_dir).as_posix()
            for path in pack_dir.rglob("*")
            if path.is_file()
        }
        missing_names = sorted(expected_names - name_set)
        unexpected_names = sorted(name_set - expected_names)
        missing_required = sorted(REQUIRED_FILES - name_set)

        if duplicate_names:
            issues.append(f"duplicate archive member names: {len(duplicate_names)}")
        if unsafe_names:
            issues.append(f"unsafe archive member names: {len(unsafe_names)}")
        if nested_zips:
            issues.append(f"nested ZIP files: {len(nested_zips)}")
        if crc_bad_member:
            issues.append(f"CRC failure: {crc_bad_member}")
        if missing_names:
            issues.append(f"missing pack files: {len(missing_names)}")
        if unexpected_names:
            issues.append(f"unexpected archive files: {len(unexpected_names)}")
        if missing_required:
            issues.append(f"missing required files: {len(missing_required)}")

        manifest_rows: list[dict[str, Any]] = []
        checklist_rows: list[dict[str, str]] = []
        queue_rows = read_jsonl_path(queue_path)
        if "manifest.jsonl" in name_set:
            try:
                manifest_rows = read_jsonl_bytes(archive.read("manifest.jsonl"), "manifest.jsonl")
            except ValueError as exc:
                issues.append(str(exc))
        if "microtext_validation_checklist.csv" in name_set:
            text = archive.read("microtext_validation_checklist.csv").decode("utf-8-sig")
            checklist_rows = list(csv.DictReader(io.StringIO(text)))

        def ids(rows: list[dict[str, Any]], field: str) -> list[str]:
            return [str(row.get(field) or "").strip() for row in rows]

        manifest_ids = ids(manifest_rows, "candidate_id")
        checklist_ids = ids(checklist_rows, "candidate_id")
        queue_ids = ids(queue_rows, "candidate_id")
        if manifest_ids != queue_ids:
            issues.append("manifest identity/order differs from queue")
        if checklist_ids != queue_ids:
            issues.append("checklist identity/order differs from queue")
        if any(not candidate_id for candidate_id in queue_ids):
            issues.append("queue has blank candidate IDs")
        if len(set(queue_ids)) != len(queue_ids):
            issues.append("queue has duplicate candidate IDs")

        manifest_regions = [microtext_region_identity(row) for row in manifest_rows]
        queue_regions = [microtext_region_identity(row) for row in queue_rows]
        if manifest_regions != queue_regions:
            issues.append("manifest physical-region identity/order differs from queue")
        if len(set(queue_regions)) != len(queue_regions):
            issues.append("queue has duplicate physical regions")

        missing_crop_members: list[str] = []
        missing_page_members: list[str] = []
        invalid_png_members: list[str] = []
        evidence_members: set[str] = set()
        for row in manifest_rows:
            crop_name = Path(str(row.get("crop_path") or "")).name
            page_name = Path(str(row.get("page_path") or "")).name
            crop_member = f"crops/{crop_name}" if crop_name else ""
            page_member = f"pages/{page_name}" if page_name else ""
            if not crop_member or crop_member not in name_set:
                missing_crop_members.append(crop_member or "<blank>")
            else:
                evidence_members.add(crop_member)
            if not page_member or page_member not in name_set:
                missing_page_members.append(page_member or "<blank>")
            else:
                evidence_members.add(page_member)

        mismatched_files: list[str] = []
        for name in sorted(name_set & expected_names):
            archived = archive.read(name)
            source = (pack_dir / PurePosixPath(name)).read_bytes()
            if sha256_bytes(archived) != sha256_bytes(source):
                mismatched_files.append(name)
        for name in sorted(evidence_members):
            if name in name_set and not valid_png(
                archive.read(name), max_image_pixels=max_image_pixels
            ):
                invalid_png_members.append(name)

        if missing_crop_members:
            issues.append(f"missing crop members: {len(missing_crop_members)}")
        if missing_page_members:
            issues.append(f"missing page members: {len(missing_page_members)}")
        if invalid_png_members:
            issues.append(f"invalid evidence PNG files: {len(invalid_png_members)}")
        if mismatched_files:
            issues.append(f"archive/source byte mismatches: {len(mismatched_files)}")

        index_missing_crops: list[str] = []
        if "index.html" in name_set:
            index_text = archive.read("index.html").decode("utf-8")
            for crop_member in sorted(name for name in evidence_members if name.startswith("crops/")):
                if Path(crop_member).name not in index_text:
                    index_missing_crops.append(crop_member)
        if index_missing_crops:
            issues.append(f"index missing crop references: {len(index_missing_crops)}")

        xlsx_ok = (
            "microtext_validation_checklist.xlsx" in name_set
            and valid_xlsx(archive.read("microtext_validation_checklist.xlsx"))
        )
        if not xlsx_ok:
            issues.append("checklist XLSX is invalid")

        return {
            "valid": not issues,
            "root": str(root),
            "zip_path": str(zip_path),
            "pack_dir": str(pack_dir),
            "queue": str(queue_path),
            "archive_sha256": sha256_bytes(zip_path.read_bytes()),
            "max_image_pixels": max_image_pixels,
            "counts": {
                "archive_files": len(names),
                "expected_pack_files": len(expected_names),
                "manifest_rows": len(manifest_rows),
                "checklist_rows": len(checklist_rows),
                "queue_rows": len(queue_rows),
                "unique_candidate_ids": len(set(queue_ids)),
                "unique_physical_regions": len(set(queue_regions)),
                "crop_files": sum(name.startswith("crops/") and name.endswith(".png") for name in names),
                "page_files": sum(name.startswith("pages/") and name.endswith(".png") for name in names),
                "verified_file_hashes": len(name_set & expected_names) - len(mismatched_files),
                "nested_zips": len(nested_zips),
                "unsafe_names": len(unsafe_names),
                "duplicate_names": len(duplicate_names),
                "missing_files": len(missing_names),
                "unexpected_files": len(unexpected_names),
                "byte_mismatches": len(mismatched_files),
                "invalid_pngs": len(invalid_png_members),
            },
            "crc_bad_member": crc_bad_member,
            "xlsx_valid": xlsx_ok,
            "issues": issues,
            "details": {
                "missing_required": missing_required,
                "missing_files": missing_names,
                "unexpected_files": unexpected_names,
                "nested_zips": nested_zips,
                "unsafe_names": unsafe_names,
                "duplicate_names": duplicate_names,
                "mismatched_files": mismatched_files,
                "missing_crop_members": missing_crop_members,
                "missing_page_members": missing_page_members,
                "invalid_png_members": invalid_png_members,
                "index_missing_crops": index_missing_crops,
            },
        }


def write_report(report: dict[str, Any], output_json: Path | None, output_md: Path | None) -> None:
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        counts = report.get("counts") or {}
        lines = [
            "# MicroText Review Archive Verification",
            "",
            f"- Result: **{'PASS' if report.get('valid') else 'FAIL'}**",
            f"- Archive files: `{counts.get('archive_files', 0)}`",
            f"- Queue / manifest / checklist rows: `{counts.get('queue_rows', 0)}` / `{counts.get('manifest_rows', 0)}` / `{counts.get('checklist_rows', 0)}`",
            f"- Crops / full pages: `{counts.get('crop_files', 0)}` / `{counts.get('page_files', 0)}`",
            f"- Verified file hashes: `{counts.get('verified_file_hashes', 0)}`",
            f"- Nested ZIPs: `{counts.get('nested_zips', 0)}`",
            f"- Invalid PNGs: `{counts.get('invalid_pngs', 0)}`",
            f"- XLSX valid: `{report.get('xlsx_valid', False)}`",
            f"- Archive SHA-256: `{report.get('archive_sha256', '')}`",
            "",
            "## Issues",
            "",
        ]
        lines.extend(f"- `{issue}`" for issue in report.get("issues") or [])
        if not report.get("issues"):
            lines.append("- None.")
        output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--zip", dest="zip_path", required=True)
    parser.add_argument("--pack-dir", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        help=(
            "Opt-in Pillow safety ceiling for audited large engineering scans. "
            "The default preserves Pillow's standard limit."
        ),
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)
    report = audit_archive(
        Path(args.root),
        Path(args.zip_path),
        Path(args.pack_dir),
        Path(args.queue),
        max_image_pixels=args.max_image_pixels,
    )
    write_report(
        report,
        Path(args.output_json) if args.output_json else None,
        Path(args.output_md) if args.output_md else None,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
