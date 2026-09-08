#!/usr/bin/env python3
"""Validate and stage the returned Eng_Bench second-pass Excel workbook.

This tool never mutates active gold JSONL. It reads the generated package CSV
as the immutable source of truth, imports only the four reviewer-editable
fields from Excel, and writes a sanitized staging CSV plus an audit report.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


REVIEW_FIELDS = (
    "second_review_status",
    "final_text",
    "final_category",
    "second_review_notes",
)
ALLOWED_STATUSES = {"accepted", "edited", "rejected"}
ALLOWED_CATEGORIES = {
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pin_label",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "room_label",
    "tolerance_value",
}
REQUIRED_SHEETS = ("SECOND PASS", "CROP REPAIR")

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def text(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    if re.fullmatch(r"-?\d+\.0", value):
        return value[:-2]
    return value


def validate_return_rows(
    expected_rows: list[dict[str, str]],
    returned_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    errors: list[str] = []
    expected_ids = [text(row.get("candidate_id")) for row in expected_rows]
    returned_ids = [text(row.get("candidate_id")) for row in returned_rows]
    expected_counts = Counter(expected_ids)
    returned_counts = Counter(returned_ids)
    duplicate_expected = sorted(key for key, count in expected_counts.items() if key and count > 1)
    duplicate_returned = sorted(key for key, count in returned_counts.items() if key and count > 1)
    missing_ids = sorted((expected_counts - returned_counts).elements())
    unexpected_ids = sorted((returned_counts - expected_counts).elements())
    if duplicate_expected:
        errors.append(f"expected checklist has duplicate candidate IDs: {duplicate_expected[:5]}")
    if duplicate_returned:
        errors.append(f"returned workbook has duplicate candidate IDs: {duplicate_returned[:5]}")
    if missing_ids:
        errors.append(f"returned workbook is missing candidate IDs: {missing_ids[:5]}")
    if unexpected_ids:
        errors.append(f"returned workbook has unexpected candidate IDs: {unexpected_ids[:5]}")

    returned_by_id = {
        text(row.get("candidate_id")): row
        for row in returned_rows
        if text(row.get("candidate_id")) and returned_counts[text(row.get("candidate_id"))] == 1
    }
    fields = list(expected_rows[0].keys()) if expected_rows else []
    immutable_fields = [field for field in fields if field not in REVIEW_FIELDS]
    sanitized: list[dict[str, str]] = []
    immutable_changes = 0
    invalid_rows = 0
    status_counts: Counter[str] = Counter()

    for expected in expected_rows:
        candidate_id = text(expected.get("candidate_id"))
        returned = returned_by_id.get(candidate_id)
        output = {field: text(expected.get(field)) for field in fields}
        if returned is None:
            sanitized.append(output)
            continue

        changed = [
            field
            for field in immutable_fields
            if field in returned and text(expected.get(field)) != text(returned.get(field))
        ]
        if changed:
            immutable_changes += len(changed)
            for field in changed:
                errors.append(f"{candidate_id}: immutable field changed: {field}")

        for field in REVIEW_FIELDS:
            output[field] = text(returned.get(field))
        status = output["second_review_status"].lower()
        output["second_review_status"] = status
        status_counts[status or "blank"] += 1
        row_errors: list[str] = []
        if status not in ALLOWED_STATUSES:
            row_errors.append(
                f"invalid second_review_status {status!r}; expected accepted, edited, or rejected"
            )
        elif status == "accepted":
            if output["final_text"] or output["final_category"]:
                row_errors.append("accepted must leave final_text and final_category blank")
        elif status == "edited":
            if not output["final_text"] or not output["final_category"]:
                row_errors.append("edited requires final_text and final_category")
            elif output["final_category"] not in ALLOWED_CATEGORIES:
                row_errors.append(
                    f"edited has unsupported final_category {output['final_category']!r}"
                )
        elif status == "rejected" and not output["second_review_notes"]:
            row_errors.append("rejected requires second_review_notes")
        if row_errors:
            invalid_rows += 1
            errors.extend(f"{candidate_id}: {message}" for message in row_errors)
        sanitized.append(output)

    complete = (
        bool(expected_rows)
        and len(returned_rows) == len(expected_rows)
        and not errors
        and status_counts.get("blank", 0) == 0
    )
    report = {
        "complete": complete,
        "expected_rows": len(expected_rows),
        "returned_rows": len(returned_rows),
        "sanitized_rows": len(sanitized),
        "status_counts": dict(sorted(status_counts.items())),
        "invalid_rows": invalid_rows,
        "immutable_changes": immutable_changes,
        "missing_ids": len(missing_ids),
        "unexpected_ids": len(unexpected_ids),
        "duplicate_returned_ids": len(duplicate_returned),
        "errors": errors,
    }
    return sanitized, report


def column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        raise ValueError(f"invalid cell reference: {reference}")
    result = 0
    for char in letters.group(0):
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    ]


def sheet_targets(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    result: dict[str, str] = {}
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        return result
    for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet"):
        relation_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
        target = targets[relation_id].replace("\\", "/")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = str(PurePosixPath("xl") / target)
        result[sheet.attrib["name"]] = target
    return result


def cell_value(cell: ET.Element, strings: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    raw = value.text if value is not None and value.text is not None else ""
    if kind == "s" and raw:
        return strings[int(raw)]
    return text(raw)


def read_sheet(archive: zipfile.ZipFile, target: str, strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(target))
    result: list[list[str]] = []
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            values[column_index(cell.attrib.get("r", "A1"))] = cell_value(cell, strings)
        width = max(values, default=-1) + 1
        result.append([values.get(index, "") for index in range(width)])
    return result


def read_review_workbook(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        strings = shared_strings(archive)
        targets = sheet_targets(archive)
        missing = [name for name in REQUIRED_SHEETS if name not in targets]
        if missing:
            raise ValueError(f"workbook missing required sheets: {missing}")
        result: list[dict[str, str]] = []
        for name in REQUIRED_SHEETS:
            matrix = read_sheet(archive, targets[name], strings)
            if not matrix:
                raise ValueError(f"workbook sheet is empty: {name}")
            headers = [text(value) for value in matrix[0]]
            if "candidate_id" not in headers or "second_review_status" not in headers:
                raise ValueError(f"workbook sheet has invalid headers: {name}")
            for values in matrix[1:]:
                row = {
                    header: text(values[index] if index < len(values) else "")
                    for index, header in enumerate(headers)
                    if header
                }
                if any(row.values()):
                    result.append(row)
        return result


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Second-Pass Return Preflight",
        "",
        f"- Complete: `{str(report['complete']).lower()}`",
        f"- Expected rows: `{report['expected_rows']}`",
        f"- Returned rows: `{report['returned_rows']}`",
        f"- Invalid rows: `{report['invalid_rows']}`",
        f"- Immutable changes: `{report['immutable_changes']}`",
        f"- Missing IDs: `{report['missing_ids']}`",
        f"- Unexpected IDs: `{report['unexpected_ids']}`",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in report["status_counts"].items():
        lines.append(f"- `{status}`: `{count}`")
    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in report["errors"][:100])
    lines.extend(
        [
            "",
            "This preflight does not merge or modify active gold annotations.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root)
    package_root = Path(args.package_root)
    workbook = Path(args.workbook)
    output_dir = Path(args.output_dir)
    if not package_root.is_absolute():
        package_root = root / package_root
    if not workbook.is_absolute():
        workbook = root / workbook
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    expected_path = package_root / "second_pass_review_checklist.csv"
    expected = read_csv(expected_path)
    returned = read_review_workbook(workbook)
    sanitized, report = validate_return_rows(expected, returned)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "second_pass_review_sanitized.csv", sanitized, list(expected[0].keys()))
    (output_dir / "preflight_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "preflight_report.md").write_text(render_markdown(report), encoding="utf-8")
    shutil.copy2(workbook, output_dir / "returned_workbook_snapshot.xlsx")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if args.strict and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
