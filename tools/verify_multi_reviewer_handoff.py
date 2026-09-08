#!/usr/bin/env python3
"""Verify an Eng_Bench primary-plus-independent-auditor handoff."""
from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import verify_review_batch_package
except ImportError:  # Direct script execution from tools/.
    import verify_review_batch_package


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
FORMULA_ERRORS = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference.upper())
    if not match:
        return 0
    result = 0
    for char in match.group(1):
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(f"{{{MAIN_NS}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))
    return values


def workbook_sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    result: dict[str, str] = {}
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        return result
    for sheet in sheets:
        relationship_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id", "")
        target = targets.get(relationship_id, "")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = f"xl/{target}"
        result[sheet.attrib.get("name", "")] = target
    return result


def cell_text(cell: ET.Element, strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s" and raw:
        try:
            return strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    return raw


def read_xlsx_sheet(path: Path, sheet_name: str) -> tuple[list[list[str]], int, bool]:
    with zipfile.ZipFile(path, "r") as archive:
        if archive.testzip() is not None:
            raise ValueError(f"{path}: workbook CRC failure")
        strings = shared_strings(archive)
        sheet_paths = workbook_sheet_paths(archive)
        if sheet_name not in sheet_paths:
            raise KeyError(f"{path}: missing sheet {sheet_name}")
        root = ET.fromstring(archive.read(sheet_paths[sheet_name]))
        rows: list[list[str]] = []
        formula_count = 0
        for row_node in root.iter(f"{{{MAIN_NS}}}row"):
            values: list[str] = []
            for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                index = column_index(cell.attrib.get("r", "A1"))
                while len(values) <= index:
                    values.append("")
                values[index] = cell_text(cell, strings)
                if cell.find(f"{{{MAIN_NS}}}f") is not None:
                    formula_count += 1
            rows.append(values)
        validation_present = root.find(f"{{{MAIN_NS}}}dataValidations") is not None
    return rows, formula_count, validation_present


def table_records(path: Path, sheet_name: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    rows, formulas, validation = read_xlsx_sheet(path, sheet_name)
    if not rows:
        return [], {"formula_count": formulas, "data_validation": validation}
    headers = rows[0]
    records = []
    for values in rows[1:]:
        if not any(str(value).strip() for value in values):
            continue
        padded = values + [""] * max(0, len(headers) - len(values))
        records.append({str(header): str(padded[index]) for index, header in enumerate(headers)})
    return records, {"formula_count": formulas, "data_validation": validation}


def check_evidence_paths(csv_path: Path, rows: list[dict[str, str]]) -> list[str]:
    issues: list[str] = []
    fields = (
        "crop_path",
        "page_path",
        "panel_path",
        "old_crop_path",
        "new_crop_path",
        "old_page_path",
        "new_page_path",
    )
    for row_number, row in enumerate(rows, start=2):
        for field in fields:
            value = str(row.get(field) or "").strip()
            if not value:
                continue
            target = (csv_path.parent / value).resolve()
            if not target.is_file():
                issues.append(f"{csv_path.name}:{row_number}: missing {field} -> {value}")
    return issues


def check_workbook(
    workbook: Path,
    expected_micro: list[dict[str, str]],
    expected_visual: list[dict[str, str]],
    reviewer_id: str,
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    if not workbook.exists():
        return {"path": workbook.as_posix(), "valid": False}, [f"missing workbook {workbook}"]
    try:
        guide, _, _ = read_xlsx_sheet(workbook, "说明")
        micro, micro_meta = table_records(workbook, "MICROTEXT")
        visual, visual_meta = table_records(workbook, "VISUALDIFF")
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        return {"path": workbook.as_posix(), "valid": False}, [str(exc)]

    if len(micro) != len(expected_micro):
        issues.append(f"{workbook.name}: MicroText rows expected {len(expected_micro)}, found {len(micro)}")
    if len(visual) != len(expected_visual):
        issues.append(f"{workbook.name}: VisualDiff rows expected {len(expected_visual)}, found {len(visual)}")

    for task, actual, expected, id_field, status_field in (
        ("MicroText", micro, expected_micro, "candidate_id", "review_status"),
        ("VisualDiff", visual, expected_visual, "pair_id", "human_status"),
    ):
        actual_ids = [row.get(id_field, "") for row in actual]
        expected_ids = [row.get(id_field, "") for row in expected]
        if actual_ids != expected_ids:
            issues.append(f"{workbook.name}: {task} IDs/order do not match control CSV")
        if any(row.get("reviewer_id", "") != reviewer_id for row in actual):
            issues.append(f"{workbook.name}: {task} reviewer_id mismatch")
        if any(row.get(status_field, "") for row in actual):
            issues.append(f"{workbook.name}: {task} contains prefilled human decisions")
        for row in actual:
            if any(error in str(value) for value in row.values() for error in FORMULA_ERRORS):
                issues.append(f"{workbook.name}: {task} contains formula error token")

    if micro_meta["formula_count"] < len(expected_micro):
        issues.append(f"{workbook.name}: MicroText row_check formulas missing")
    if visual_meta["formula_count"] < len(expected_visual):
        issues.append(f"{workbook.name}: VisualDiff row_check formulas missing")
    if not micro_meta["data_validation"]:
        issues.append(f"{workbook.name}: MicroText status validation missing")
    if not visual_meta["data_validation"]:
        issues.append(f"{workbook.name}: VisualDiff status validation missing")
    guide_text = " ".join(value for row in guide for value in row)
    for term in ("Gold v2.0 Global", "row_check", "needs_full_page"):
        if term not in guide_text:
            issues.append(f"{workbook.name}: guide missing {term!r}")

    return {
        "path": workbook.as_posix(),
        "size_bytes": workbook.stat().st_size,
        "microtext_rows": len(micro),
        "visualdiff_rows": len(visual),
        "formula_count": micro_meta["formula_count"] + visual_meta["formula_count"],
        "data_validation": bool(micro_meta["data_validation"] and visual_meta["data_validation"]),
        "issues": issues,
        "valid": not issues,
    }, issues


def verify_dir(batch_dir: Path) -> dict[str, Any]:
    batch_dir = batch_dir.resolve()
    issues: list[str] = []
    generic = verify_review_batch_package.verify_batch_dir(batch_dir)
    issues.extend(f"review packs: {issue}" for issue in generic.get("issues", []))

    required = (
        "00_START_HERE_ZH.md",
        "WHO_DOES_WHAT_ZH.md",
        "01_PRIMARY_REVIEWER/PRIMARY_INSTRUCTIONS_ZH.md",
        "01_PRIMARY_REVIEWER/PRIMARY_INDEX.html",
        "02_INDEPENDENT_AUDITORS/AUDITOR_INSTRUCTIONS_ZH.md",
        "03_MACHINE_CONTROL/audit_assignments.csv",
        "03_MACHINE_CONTROL/return_manifest.csv",
        "03_MACHINE_CONTROL/multi_reviewer_build_report.json",
        "03_MACHINE_CONTROL/workbook_build_report.json",
    )
    for name in required:
        if not (batch_dir / name).exists():
            issues.append(f"missing {name}")

    nested_zips = sorted(path.relative_to(batch_dir).as_posix() for path in batch_dir.rglob("*.zip"))
    if nested_zips:
        issues.append(f"nested ZIP files present: {nested_zips[:10]}")

    start_text = (batch_dir / "00_START_HERE_ZH.md").read_text(encoding="utf-8")
    for term in ("完整解压", "主审核员", "独立复核员", "498", "12", "不要", "交回"):
        if term not in start_text:
            issues.append(f"root instructions missing {term!r}")

    primary_dir = batch_dir / "01_PRIMARY_REVIEWER"
    primary_micro_path = primary_dir / "PRIMARY_MICROTEXT_SOURCE.csv"
    primary_visual_path = primary_dir / "PRIMARY_VISUALDIFF_SOURCE.csv"
    primary_micro = read_csv(primary_micro_path)
    primary_visual = read_csv(primary_visual_path)
    if len(primary_micro) != 369:
        issues.append(f"primary MicroText source expected 369, found {len(primary_micro)}")
    if len(primary_visual) != 129:
        issues.append(f"primary VisualDiff source expected 129, found {len(primary_visual)}")
    issues.extend(check_evidence_paths(primary_micro_path, primary_micro))
    issues.extend(check_evidence_paths(primary_visual_path, primary_visual))

    workbook_reports: list[dict[str, Any]] = []
    report, workbook_issues = check_workbook(
        primary_dir / "PRIMARY_REVIEW_498.xlsx",
        primary_micro,
        primary_visual,
        "primary_reviewer",
    )
    workbook_reports.append(report)
    issues.extend(workbook_issues)

    assignment_path = batch_dir / "03_MACHINE_CONTROL" / "audit_assignments.csv"
    assignments = read_csv(assignment_path)
    assignment_ids = [row.get("record_id", "") for row in assignments]
    primary_ids = {row["candidate_id"] for row in primary_micro} | {
        row["pair_id"] for row in primary_visual
    }
    if len(assignments) != 120:
        issues.append(f"expected 120 independent-audit assignments, found {len(assignments)}")
    if len(set(assignment_ids)) != len(assignment_ids):
        issues.append("independent-audit assignments overlap each other")
    if not set(assignment_ids).issubset(primary_ids):
        issues.append("some audit assignments are not a subset of the primary cohort")

    auditors_dir = batch_dir / "02_INDEPENDENT_AUDITORS"
    for number in range(1, 11):
        auditor_id = f"auditor_{number:02d}"
        folder = auditors_dir / auditor_id
        expected = [row for row in assignments if row.get("auditor_id") == auditor_id]
        expected_micro_ids = [row["record_id"] for row in expected if row.get("task") == "microtext"]
        expected_visual_ids = [row["record_id"] for row in expected if row.get("task") == "visualdiff"]
        micro_path = folder / f"AUDITOR_{number:02d}_MICROTEXT_SOURCE.csv"
        visual_path = folder / f"AUDITOR_{number:02d}_VISUALDIFF_SOURCE.csv"
        micro = read_csv(micro_path)
        visual = read_csv(visual_path)
        if len(expected) != 12 or len(micro) != 9 or len(visual) != 3:
            issues.append(
                f"{auditor_id}: expected 12 assignments / 9 MicroText / 3 VisualDiff, "
                f"found {len(expected)} / {len(micro)} / {len(visual)}"
            )
        if [row["candidate_id"] for row in micro] != expected_micro_ids:
            issues.append(f"{auditor_id}: MicroText source CSV does not match assignment control")
        if [row["pair_id"] for row in visual] != expected_visual_ids:
            issues.append(f"{auditor_id}: VisualDiff source CSV does not match assignment control")
        issues.extend(check_evidence_paths(micro_path, micro))
        issues.extend(check_evidence_paths(visual_path, visual))
        if not (folder / "index.html").exists():
            issues.append(f"{auditor_id}: missing index.html")
        report, workbook_issues = check_workbook(
            folder / f"AUDITOR_{number:02d}_REVIEW_12.xlsx",
            micro,
            visual,
            auditor_id,
        )
        workbook_reports.append(report)
        issues.extend(workbook_issues)

    return_manifest = read_csv(batch_dir / "03_MACHINE_CONTROL" / "return_manifest.csv")
    if len(return_manifest) != 11:
        issues.append(f"return manifest expected 11 files, found {len(return_manifest)}")

    expected_paths = {Path(row["return_file"]).name for row in return_manifest}
    actual_paths = {path.name for path in batch_dir.rglob("*.xlsx")}
    if expected_paths != actual_paths:
        issues.append(
            f"return workbook filenames mismatch: expected {sorted(expected_paths)}, found {sorted(actual_paths)}"
        )

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
        "workbooks": workbook_reports,
        "workbook_count": len(workbook_reports),
        "nested_zip_files": nested_zips,
        "gold_rows_modified": 0,
        "issues": issues,
        "valid": not issues,
    }


def unsafe_zip_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or bool(path.drive)


def verify_zip(zip_path: Path) -> dict[str, Any]:
    zip_path = zip_path.resolve()
    issues: list[str] = []
    if not zip_path.exists():
        return {"zip_path": zip_path.as_posix(), "issues": ["missing ZIP"], "valid": False}
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
        "nested_zip_entries": len(nested),
        "duplicate_entries": len(duplicates),
        "unsafe_entries": len(unsafe),
        "crc_valid": corrupt is None,
        "folder_report": folder_report,
        "issues": issues,
        "valid": not issues,
    }


def render_markdown(report: dict[str, Any]) -> str:
    folder = report.get("folder_report") or report
    return "\n".join(
        [
            "# Multi-reviewer Handoff Verification",
            "",
            f"- Goal: `{report.get('goal', 'Gold v2.0 Global')}`",
            f"- Valid: `{str(report.get('valid', False)).lower()}`",
            f"- Primary rows: `{folder.get('primary_rows', 0)}`",
            f"- Workbooks: `{folder.get('workbook_count', 0)}`",
            f"- Unique double-reviewed rows: `{folder.get('unique_double_review_rows', 0)}`",
            f"- Cross-auditor overlap: `{folder.get('cross_auditor_overlap', 0)}`",
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
