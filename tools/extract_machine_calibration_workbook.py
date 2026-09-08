#!/usr/bin/env python3
"""Extract a returned machine-calibration XLSX into a frozen, validated CSV."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import re
import zipfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XDR_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
ALLOWED_DECISIONS = {"correct", "incorrect", "unclear"}
RESPONSE_FIELDS = (
    "reviewer_decision",
    "corrected_text",
    "corrected_category",
    "reviewer_notes",
)
NUMERIC_FIELDS = {
    "sample_index",
    "page_index",
    "ocr_confidence",
    "crop_width",
    "crop_height",
    "context_width",
    "context_height",
}
CELL_REF = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def column_number(letters: str) -> int:
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - ord("A") + 1
    return value


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(name))
    return [
        "".join(node.text or "" for node in item.findall(f".//{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    ]


def sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    result: dict[str, str] = {}
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        name = str(sheet.attrib.get("name") or "")
        relation_id = str(sheet.attrib.get(f"{{{REL_NS}}}id") or "")
        target = targets.get(relation_id, "")
        if not name or not target:
            continue
        normalized = target.lstrip("/")
        if not normalized.startswith("xl/"):
            normalized = posixpath.normpath(posixpath.join("xl", normalized))
        if ".." in normalized.split("/") or not normalized.startswith("xl/worksheets/"):
            raise ValueError(f"unsafe worksheet target for {name}: {target}")
        result[name] = normalized
    return result


def related_part_path(source_path: str, target: str) -> str:
    normalized = target.lstrip("/")
    if not normalized.startswith("xl/"):
        normalized = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_path), normalized)
        )
    if ".." in normalized.split("/") or not normalized.startswith("xl/"):
        raise ValueError(f"unsafe OOXML relationship target: {target}")
    return normalized


def relationship_path(source_path: str) -> str:
    return posixpath.join(
        posixpath.dirname(source_path),
        "_rels",
        posixpath.basename(source_path) + ".rels",
    )


def relationships(archive: zipfile.ZipFile, source_path: str) -> dict[str, str]:
    rel_path = relationship_path(source_path)
    if rel_path not in archive.namelist():
        return {}
    root = ET.fromstring(archive.read(rel_path))
    return {
        relation.attrib["Id"]: related_part_path(
            source_path, relation.attrib.get("Target", "")
        )
        for relation in root.findall(f"{{{PKG_REL_NS}}}Relationship")
    }


def worksheet_image_hashes(
    archive: zipfile.ZipFile, sheet_path: str
) -> tuple[dict[int, str], list[str]]:
    """Return crop hashes keyed by one-based worksheet row."""
    issues: list[str] = []
    sheet_root = ET.fromstring(archive.read(sheet_path))
    drawing_nodes = sheet_root.findall(f"{{{MAIN_NS}}}drawing")
    if len(drawing_nodes) != 1:
        return {}, [f"review_drawing_count:{len(drawing_nodes)}"]
    drawing_id = drawing_nodes[0].attrib.get(f"{{{REL_NS}}}id", "")
    drawing_path = relationships(archive, sheet_path).get(drawing_id, "")
    if not drawing_path or drawing_path not in archive.namelist():
        return {}, ["review_drawing_missing"]

    drawing_root = ET.fromstring(archive.read(drawing_path))
    image_relationships = relationships(archive, drawing_path)
    row_hashes: dict[int, str] = {}
    anchors = list(drawing_root.findall(f"{{{XDR_NS}}}oneCellAnchor"))
    anchors.extend(drawing_root.findall(f"{{{XDR_NS}}}twoCellAnchor"))
    for anchor in anchors:
        origin = anchor.find(f"{{{XDR_NS}}}from")
        row_node = origin.find(f"{{{XDR_NS}}}row") if origin is not None else None
        col_node = origin.find(f"{{{XDR_NS}}}col") if origin is not None else None
        blip = anchor.find(f".//{{{A_NS}}}blip")
        if row_node is None or col_node is None or blip is None:
            issues.append("malformed_review_image_anchor")
            continue
        try:
            row = int(row_node.text or "") + 1
            column = int(col_node.text or "") + 1
        except ValueError:
            issues.append("malformed_review_image_position")
            continue
        if column != 2:
            issues.append(f"review_image_wrong_column:{row}:{column}")
        relation_id = blip.attrib.get(f"{{{REL_NS}}}embed", "")
        media_path = image_relationships.get(relation_id, "")
        if not media_path or media_path not in archive.namelist():
            issues.append(f"review_image_missing:{row}")
            continue
        digest = hashlib.sha256(archive.read(media_path)).hexdigest()
        if row in row_hashes:
            issues.append(f"duplicate_review_image_row:{row}")
            continue
        row_hashes[row] = digest
    return row_hashes, issues


def worksheet_cells(
    archive: zipfile.ZipFile, sheet_path: str, strings: list[str]
) -> dict[tuple[int, int], str]:
    root = ET.fromstring(archive.read(sheet_path))
    cells: dict[tuple[int, int], str] = {}
    for cell in root.findall(f".//{{{MAIN_NS}}}c"):
        reference = str(cell.attrib.get("r") or "")
        match = CELL_REF.match(reference)
        if not match:
            continue
        column = column_number(match.group(1))
        row = int(match.group(2))
        cell_type = str(cell.attrib.get("t") or "")
        if cell_type == "inlineStr":
            value = "".join(
                node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t")
            )
        else:
            value_node = cell.find(f"{{{MAIN_NS}}}v")
            value = value_node.text if value_node is not None and value_node.text else ""
            if cell_type == "s" and value:
                try:
                    value = strings[int(value)]
                except (IndexError, ValueError) as exc:
                    raise ValueError(f"invalid shared string reference in {reference}") from exc
        cells[(row, column)] = value
    return cells


def canonical_number(value: str) -> str:
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return f"invalid:{value}"
    if number == number.to_integral():
        return str(number.quantize(Decimal("1")))
    return format(number.normalize(), "f")


def immutable_equal(field: str, expected: str, actual: str) -> bool:
    if field in NUMERIC_FIELDS:
        return canonical_number(expected) == canonical_number(actual)
    return str(expected) == str(actual)


def read_expected(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    missing = [field for field in RESPONSE_FIELDS if field not in fields]
    if missing:
        raise ValueError(f"expected checklist missing response fields: {missing}")
    return fields, rows


def extract(
    *, workbook_path: Path, expected_path: Path, output_path: Path, report_path: Path
) -> dict[str, Any]:
    fields, expected_rows = read_expected(expected_path)
    issues: list[str] = []
    extracted_rows: list[dict[str, str]] = []
    workbook_path = workbook_path.resolve()
    expected_path = expected_path.resolve()
    output_path = output_path.resolve()
    report_path = report_path.resolve()

    with zipfile.ZipFile(workbook_path) as archive:
        bad_entry = archive.testzip()
        if bad_entry:
            issues.append(f"xlsx_crc_failure:{bad_entry}")
        paths = sheet_paths(archive)
        review_name = f"审核{len(expected_rows)}条"
        required_sheets = {review_name, "MachineControl"}
        missing_sheets = sorted(required_sheets - set(paths))
        if missing_sheets:
            issues.extend(f"missing_sheet:{name}" for name in missing_sheets)
            review_cells: dict[tuple[int, int], str] = {}
            control_cells: dict[tuple[int, int], str] = {}
            review_image_hashes: dict[int, str] = {}
        else:
            strings = shared_strings(archive)
            review_cells = worksheet_cells(archive, paths[review_name], strings)
            control_cells = worksheet_cells(archive, paths["MachineControl"], strings)
            review_image_hashes, image_issues = worksheet_image_hashes(
                archive, paths[review_name]
            )
            issues.extend(image_issues)

    control_headers = [control_cells.get((1, column), "") for column in range(1, 23)]
    if control_headers != fields:
        issues.append("machine_control_header_mismatch")

    control_candidate_ids: list[str] = []
    decision_counts: Counter[str] = Counter()
    for index, expected in enumerate(expected_rows):
        review_row = index + 6
        control_row = index + 2
        actual_control = {
            field: control_cells.get((control_row, column), "")
            for column, field in enumerate(fields, 1)
        }
        candidate_id = actual_control.get("candidate_id", "")
        control_candidate_ids.append(candidate_id)
        for field in fields:
            if field in RESPONSE_FIELDS:
                continue
            if not immutable_equal(field, expected.get(field, ""), actual_control.get(field, "")):
                issues.append(f"immutable_mismatch:{index + 1}:{field}")

        visible_checks = {
            "sample_index": review_cells.get((review_row, 1), ""),
            "proposed_text": review_cells.get((review_row, 3), ""),
            "category": review_cells.get((review_row, 4), ""),
        }
        for field, actual in visible_checks.items():
            if not immutable_equal(field, expected.get(field, ""), actual):
                issues.append(f"review_reference_mismatch:{index + 1}:{field}")
        actual_crop_hash = review_image_hashes.get(review_row, "")
        if actual_crop_hash != expected.get("crop_sha256", ""):
            issues.append(f"review_crop_hash_mismatch:{index + 1}")

        decision = review_cells.get((review_row, 5), "").strip().lower()
        corrected_text = review_cells.get((review_row, 6), "").strip()
        corrected_category = review_cells.get((review_row, 7), "").strip()
        reviewer_notes = review_cells.get((review_row, 8), "").strip()
        decision_counts[decision or "blank"] += 1
        if decision not in ALLOWED_DECISIONS:
            issues.append(f"invalid_or_missing_decision:{index + 1}")
        if decision == "incorrect" and not (
            corrected_text or corrected_category or reviewer_notes
        ):
            issues.append(f"incorrect_without_correction_or_note:{index + 1}")
        if decision == "unclear" and not reviewer_notes:
            issues.append(f"unclear_without_note:{index + 1}")
        extracted_rows.append(
            {
                **expected,
                "reviewer_decision": decision,
                "corrected_text": corrected_text,
                "corrected_category": corrected_category,
                "reviewer_notes": reviewer_notes,
            }
        )

    if len(control_candidate_ids) != len(set(control_candidate_ids)):
        issues.append("duplicate_machine_control_candidate_id")
    if any(not value for value in control_candidate_ids):
        issues.append("missing_machine_control_candidate_id")
    expected_after_row = len(expected_rows) + 2
    if control_cells.get((expected_after_row, 2), ""):
        issues.append("unexpected_extra_machine_control_row")
    if review_cells.get((len(expected_rows) + 6, 1), ""):
        issues.append("unexpected_extra_review_row")
    expected_review_rows = set(range(6, len(expected_rows) + 6))
    if set(review_image_hashes) - expected_review_rows:
        issues.append("unexpected_extra_review_image")

    valid = not issues
    if valid:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(extracted_rows)
    report = {
        "schema": "eng_bench_machine_calibration_xlsx_extraction_v1",
        "goal": "Gold v2.0 Global",
        "valid": valid,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "workbook": workbook_path.as_posix(),
        "workbook_sha256": file_sha256(workbook_path),
        "expected_checklist": expected_path.as_posix(),
        "expected_checklist_sha256": file_sha256(expected_path),
        "expected_rows": len(expected_rows),
        "verified_embedded_crops": len(review_image_hashes),
        "decision_counts": dict(sorted(decision_counts.items())),
        "output_written": valid,
        "output_csv": output_path.as_posix() if valid else "",
        "output_csv_sha256": file_sha256(output_path) if valid else "",
        "issues": issues,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--expected-checklist", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = extract(
        workbook_path=args.workbook,
        expected_path=args.expected_checklist,
        output_path=args.output_csv,
        report_path=args.report_json,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
