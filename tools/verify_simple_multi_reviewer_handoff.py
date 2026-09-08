#!/usr/bin/env python3
"""Verify the simplified image-embedded Eng_Bench multi-review handoff."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import verify_multi_reviewer_handoff as base
    from . import verify_review_batch_package
except ImportError:  # Direct script execution from tools/.
    import verify_multi_reviewer_handoff as base
    import verify_review_batch_package


EXCEL_ERRORS = {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA"}
MICRO_STATUS_VALUES = {"accepted", "edited", "rejected", "needs_full_page"}
VISUAL_STATUS_VALUES = {"edit", "reject_unclear", "needs_full_page"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workbook_media_hashes(path: Path) -> Counter[str]:
    hashes: Counter[str] = Counter()
    with zipfile.ZipFile(path, "r") as archive:
        for name in archive.namelist():
            if name.startswith("xl/media/") and not name.endswith("/"):
                hashes[hashlib.sha256(archive.read(name)).hexdigest()] += 1
    return hashes


def invalid_error_cells(path: Path) -> list[str]:
    issues: list[str] = []
    with zipfile.ZipFile(path, "r") as archive:
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            for cell in root.iter(f"{{{base.MAIN_NS}}}c"):
                if cell.attrib.get("t") != "e":
                    continue
                value = cell.find(f"{{{base.MAIN_NS}}}v")
                text = value.text if value is not None else ""
                if text not in EXCEL_ERRORS:
                    issues.append(f"{name}:{cell.attrib.get('r', '?')} invalid cached error {text!r}")
    return issues


def validation_values(path: Path, sheet_name: str, column: str) -> set[str]:
    values: set[str] = set()
    with zipfile.ZipFile(path, "r") as archive:
        target = base.workbook_sheet_paths(archive)[sheet_name]
        root = ET.fromstring(archive.read(target))
        for validation in root.findall(f".//{{{base.MAIN_NS}}}dataValidation"):
            references = validation.attrib.get("sqref", "").split()
            if not any(
                re.fullmatch(fr"{re.escape(column)}\d+(?::{re.escape(column)}\d+)?", reference)
                for reference in references
            ):
                continue
            formula = validation.find(f"{{{base.MAIN_NS}}}formula1")
            raw = (formula.text or "").strip().strip('"') if formula is not None else ""
            values.update(value.strip() for value in raw.split(",") if value.strip())
    return values


def expected_media(rows: list[dict[str, str]], workbook_folder: Path, task: str) -> Counter[str]:
    field = "crop_path" if task == "microtext" else "panel_path"
    hashes: Counter[str] = Counter()
    for row in rows:
        evidence = (workbook_folder / row[field]).resolve()
        hashes[sha256(evidence)] += 1
    return hashes


def check_simple_workbook(
    workbook: Path,
    expected_micro: list[dict[str, str]],
    expected_visual: list[dict[str, str]],
    reviewer_id: str,
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    if not workbook.exists():
        return {"path": workbook.as_posix(), "valid": False}, [f"missing workbook {workbook}"]
    try:
        start_rows, _, _ = base.read_xlsx_sheet(workbook, "开始")
        micro, micro_meta = base.table_records(workbook, "MicroText")
        visual, visual_meta = base.table_records(workbook, "VisualDiff")
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        return {"path": workbook.as_posix(), "valid": False}, [str(exc)]

    if len(micro) != len(expected_micro):
        issues.append(f"{workbook.name}: expected {len(expected_micro)} MicroText rows, found {len(micro)}")
    if len(visual) != len(expected_visual):
        issues.append(f"{workbook.name}: expected {len(expected_visual)} VisualDiff rows, found {len(visual)}")

    if [row.get("candidate_id", "") for row in micro] != [
        row.get("candidate_id", "") for row in expected_micro
    ]:
        issues.append(f"{workbook.name}: MicroText IDs/order do not match control source")
    if [row.get("pair_id", "") for row in visual] != [
        row.get("pair_id", "") for row in expected_visual
    ]:
        issues.append(f"{workbook.name}: VisualDiff IDs/order do not match control source")

    for row in micro:
        if any(row.get(field, "") for field in ("你的结论", "正确文字（仅 edited）", "正确类别（仅 edited）", "原因/备注")):
            issues.append(f"{workbook.name}: prefilled MicroText human decision")
            break
    for row in visual:
        if any(row.get(field, "") for field in ("你的结论", "变化描述（edit 必填）", "原因/备注")):
            issues.append(f"{workbook.name}: prefilled VisualDiff human decision")
            break

    if micro_meta["formula_count"] < len(expected_micro):
        issues.append(f"{workbook.name}: missing MicroText completion formulas")
    if visual_meta["formula_count"] < len(expected_visual):
        issues.append(f"{workbook.name}: missing VisualDiff completion formulas")
    if not micro_meta["data_validation"]:
        issues.append(f"{workbook.name}: missing MicroText dropdown validation")
    if not visual_meta["data_validation"]:
        issues.append(f"{workbook.name}: missing VisualDiff dropdown validation")

    try:
        micro_status_values = validation_values(workbook, "MicroText", "D")
        visual_status_values = validation_values(workbook, "VisualDiff", "D")
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        micro_status_values = set()
        visual_status_values = set()
        issues.append(f"{workbook.name}: unreadable dropdown values: {exc}")
    if micro_status_values != MICRO_STATUS_VALUES:
        issues.append(
            f"{workbook.name}: MicroText dropdown values differ from contract: "
            f"{sorted(micro_status_values)}"
        )
    if visual_status_values != VISUAL_STATUS_VALUES:
        issues.append(
            f"{workbook.name}: VisualDiff dropdown values differ from contract: "
            f"{sorted(visual_status_values)}"
        )

    start_text = " ".join(value for row in start_rows for value in row)
    for term in ("Gold v2.0 Global", "图片已经在 Excel", "只填写黄色列", "needs_full_page"):
        if term not in start_text:
            issues.append(f"{workbook.name}: start sheet missing {term!r}")

    actual_media = workbook_media_hashes(workbook)
    expected_hashes = expected_media(expected_micro, workbook.parent, "microtext")
    expected_hashes.update(expected_media(expected_visual, workbook.parent, "visualdiff"))
    if actual_media != expected_hashes:
        issues.append(
            f"{workbook.name}: embedded evidence hashes differ from expected "
            f"({sum(actual_media.values())} actual vs {sum(expected_hashes.values())} expected)"
        )
    invalid_errors = invalid_error_cells(workbook)
    issues.extend(f"{workbook.name}: {item}" for item in invalid_errors)

    return {
        "path": workbook.as_posix(),
        "size_bytes": workbook.stat().st_size,
        "reviewer_id": reviewer_id,
        "microtext_rows": len(micro),
        "visualdiff_rows": len(visual),
        "embedded_images": sum(actual_media.values()),
        "expected_images": sum(expected_hashes.values()),
        "formula_count": micro_meta["formula_count"] + visual_meta["formula_count"],
        "dropdowns_present": bool(micro_meta["data_validation"] and visual_meta["data_validation"]),
        "microtext_status_values": sorted(micro_status_values),
        "visualdiff_status_values": sorted(visual_status_values),
        "invalid_cached_error_cells": len(invalid_errors),
        "issues": issues,
        "valid": not issues,
    }, issues


def reviewer_folder_noise(folder: Path, workbook_name: str) -> list[str]:
    allowed = {workbook_name, "OPEN_THIS_WORKBOOK_ZH.txt"}
    return sorted(path.name for path in folder.iterdir() if path.name not in allowed)


def verify_dir(batch_dir: Path) -> dict[str, Any]:
    batch_dir = batch_dir.resolve()
    issues: list[str] = []
    generic = verify_review_batch_package.verify_batch_dir(batch_dir)
    issues.extend(f"review packs: {issue}" for issue in generic.get("issues", []))

    required = (
        "00_START_HERE_ZH.txt",
        "WHO_DOES_WHAT_ZH.md",
        "03_MACHINE_CONTROL/audit_assignments.csv",
        "03_MACHINE_CONTROL/workbook_payload.json",
        "03_MACHINE_CONTROL/simple_package_build_report.json",
        "03_MACHINE_CONTROL/simple_workbook_build_report.json",
    )
    for relative in required:
        if not (batch_dir / relative).exists():
            issues.append(f"missing {relative}")

    start_text = (batch_dir / "00_START_HERE_ZH.txt").read_text(encoding="utf-8")
    for term in ("只需要", "图片已经嵌入 Excel", "只填写黄色列", "十位复核员", "独立工作"):
        if term not in start_text:
            issues.append(f"root instructions missing {term!r}")

    nested_zips = sorted(path.relative_to(batch_dir).as_posix() for path in batch_dir.rglob("*.zip"))
    if nested_zips:
        issues.append(f"nested ZIP files present: {nested_zips[:10]}")

    source_root = batch_dir / "03_MACHINE_CONTROL" / "source_tables"
    primary_micro = base.read_csv(source_root / "PRIMARY_MICROTEXT_SOURCE.csv")
    primary_visual = base.read_csv(source_root / "PRIMARY_VISUALDIFF_SOURCE.csv")
    primary_dir = batch_dir / "01_PRIMARY_REVIEWER"
    noise = reviewer_folder_noise(primary_dir, "PRIMARY_REVIEW_498.xlsx")
    if noise:
        issues.append(f"primary reviewer folder contains extra files: {noise}")
    report, workbook_issues = check_simple_workbook(
        primary_dir / "PRIMARY_REVIEW_498.xlsx",
        primary_micro,
        primary_visual,
        "primary_reviewer",
    )
    workbook_reports = [report]
    issues.extend(workbook_issues)

    assignments = base.read_csv(batch_dir / "03_MACHINE_CONTROL" / "audit_assignments.csv")
    assignment_ids = [row.get("record_id", "") for row in assignments]
    if len(assignments) != 120 or len(set(assignment_ids)) != 120:
        issues.append(
            f"expected 120 unique independent-audit rows, found {len(assignments)} rows / "
            f"{len(set(assignment_ids))} unique"
        )

    for number in range(1, 11):
        auditor_id = f"auditor_{number:02d}"
        folder = batch_dir / "02_INDEPENDENT_AUDITORS" / auditor_id
        workbook_name = f"AUDITOR_{number:02d}_REVIEW_12.xlsx"
        noise = reviewer_folder_noise(folder, workbook_name)
        if noise:
            issues.append(f"{auditor_id} folder contains extra files: {noise}")
        micro = base.read_csv(source_root / auditor_id / f"AUDITOR_{number:02d}_MICROTEXT_SOURCE.csv")
        visual = base.read_csv(source_root / auditor_id / f"AUDITOR_{number:02d}_VISUALDIFF_SOURCE.csv")
        report, workbook_issues = check_simple_workbook(
            folder / workbook_name,
            micro,
            visual,
            auditor_id,
        )
        workbook_reports.append(report)
        issues.extend(workbook_issues)

    return {
        "goal": "Gold v2.0 Global",
        "batch_dir": batch_dir.as_posix(),
        "review_pack_verification": generic,
        "primary_rows": len(primary_micro) + len(primary_visual),
        "primary_microtext_rows": len(primary_micro),
        "primary_visualdiff_rows": len(primary_visual),
        "auditor_count": 10,
        "rows_per_auditor": 12,
        "unique_double_review_rows": len(set(assignment_ids)),
        "cross_auditor_overlap": len(assignment_ids) - len(set(assignment_ids)),
        "workbook_count": len(workbook_reports),
        "embedded_images": sum(int(report.get("embedded_images", 0)) for report in workbook_reports),
        "workbooks": workbook_reports,
        "reviewer_visible_csv_files": sum(
            1
            for root in (batch_dir / "01_PRIMARY_REVIEWER", batch_dir / "02_INDEPENDENT_AUDITORS")
            for _ in root.rglob("*.csv")
        ),
        "reviewer_visible_html_files": sum(
            1
            for root in (batch_dir / "01_PRIMARY_REVIEWER", batch_dir / "02_INDEPENDENT_AUDITORS")
            for _ in root.rglob("*.html")
        ),
        "nested_zip_files": nested_zips,
        "gold_rows_modified": 0,
        "issues": issues,
        "valid": not issues,
    }


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def verify_zip(zip_path: Path) -> dict[str, Any]:
    zip_path = zip_path.resolve()
    if not zip_path.exists():
        return {"zip_path": zip_path.as_posix(), "issues": ["missing ZIP"], "valid": False}
    issues: list[str] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = archive.namelist()
            corrupt = archive.testzip()
            duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
            unsafe = sorted(name for name in names if unsafe_zip_name(name))
            nested = sorted(name for name in names if name.lower().endswith(".zip"))
            roots = sorted({PurePosixPath(name).parts[0] for name in names if name})
            if corrupt:
                issues.append(f"CRC failure: {corrupt}")
            if duplicates:
                issues.append(f"duplicate ZIP entries: {duplicates[:10]}")
            if unsafe:
                issues.append(f"unsafe ZIP entries: {unsafe[:10]}")
            if nested:
                issues.append(f"nested ZIP entries: {nested[:10]}")
            if len(roots) != 1:
                issues.append(f"expected one ZIP root, found {roots}")
            if issues:
                return {
                    "zip_path": zip_path.as_posix(),
                    "entry_count": len(names),
                    "issues": issues,
                    "valid": False,
                }
            with tempfile.TemporaryDirectory() as temporary:
                archive.extractall(temporary)
                folder_report = verify_dir(Path(temporary) / roots[0])
    except (zipfile.BadZipFile, OSError) as exc:
        return {"zip_path": zip_path.as_posix(), "issues": [str(exc)], "valid": False}
    issues.extend(folder_report.get("issues", []))
    return {
        "goal": "Gold v2.0 Global",
        "zip_path": zip_path.as_posix(),
        "size_bytes": zip_path.stat().st_size,
        "entry_count": len(names),
        "single_root": roots[0],
        "crc_valid": corrupt is None,
        "nested_zip_entries": len(nested),
        "duplicate_entries": len(duplicates),
        "unsafe_entries": len(unsafe),
        "folder_report": folder_report,
        "issues": issues,
        "valid": not issues,
    }


def render_markdown(report: dict[str, Any]) -> str:
    folder = report.get("folder_report") or report
    return "\n".join(
        [
            "# Simplified Multi-reviewer Handoff Verification",
            "",
            f"- Goal: `{report.get('goal', 'Gold v2.0 Global')}`",
            f"- Valid: `{str(report.get('valid', False)).lower()}`",
            f"- Primary rows: `{folder.get('primary_rows', 0)}`",
            f"- Workbooks: `{folder.get('workbook_count', 0)}`",
            f"- Embedded images: `{folder.get('embedded_images', 0)}`",
            f"- Unique double-reviewed rows: `{folder.get('unique_double_review_rows', 0)}`",
            f"- Reviewer-visible CSV/HTML: `{folder.get('reviewer_visible_csv_files', 0)}/{folder.get('reviewer_visible_html_files', 0)}`",
            f"- Issues: `{len(report.get('issues', []))}`",
            f"- Gold rows modified: `{folder.get('gold_rows_modified', 0)}`",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch-dir", type=Path)
    group.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = verify_zip(args.zip_path) if args.zip_path else verify_dir(args.batch_dir)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report_md:
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
