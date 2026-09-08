#!/usr/bin/env python3
"""Verify a review-batch ZIP and its editable XLSX workbooks for delivery."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

import verify_review_batch_package


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE_XLSX_FILES = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        return -1
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s" and raw.isdigit():
        index = int(raw)
        return shared_strings[index] if index < len(shared_strings) else ""
    return raw


def first_worksheet_rows(data: bytes) -> tuple[list[list[str]], list[str]]:
    issues: list[str] = []
    with zipfile.ZipFile(BytesIO(data), "r") as zf:
        bad_member = zf.testzip()
        if bad_member:
            return [], [f"corrupt XLSX member: {bad_member}"]
        names = set(zf.namelist())
        missing = sorted(CORE_XLSX_FILES - names)
        if missing:
            return [], [f"missing XLSX core files: {', '.join(missing)}"]

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{MAIN_NS}}}si"):
                shared_strings.append("".join(node.text or "" for node in item.findall(f".//{{{MAIN_NS}}}t")))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        first_sheet = workbook.find(f".//{{{MAIN_NS}}}sheet")
        if first_sheet is None:
            return [], ["XLSX workbook has no worksheets"]
        rel_id = first_sheet.attrib.get(f"{{{REL_NS}}}id", "")
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
            for rel in rels.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        target = targets.get(rel_id, "")
        if not target:
            return [], ["first worksheet relationship is missing"]
        sheet_name = target.lstrip("/") if target.startswith("/xl/") else str(PurePosixPath("xl") / target)
        if sheet_name not in names:
            return [], [f"first worksheet payload is missing: {sheet_name}"]

        sheet = ET.fromstring(zf.read(sheet_name))
        rows: list[list[str]] = []
        for row in sheet.findall(f".//{{{MAIN_NS}}}row"):
            values: dict[int, str] = {}
            for cell in row.findall(f"{{{MAIN_NS}}}c"):
                index = column_index(cell.attrib.get("r", ""))
                if index >= 0:
                    values[index] = cell_text(cell, shared_strings)
            if values:
                width = max(values) + 1
                rows.append([values.get(index, "") for index in range(width)])
        return rows, issues


def workbook_row_ids(data: bytes, identity_field: str) -> tuple[list[str], list[str]]:
    rows, issues = first_worksheet_rows(data)
    if issues or not rows:
        return [], issues or ["XLSX first worksheet is empty"]
    normalized_identity = re.sub(r"[^a-z0-9]+", "_", identity_field.lower()).strip("_")
    header_index = -1
    identity_column = -1
    for index, row in enumerate(rows):
        headers = [re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") for value in row]
        if normalized_identity in headers:
            header_index = index
            identity_column = headers.index(normalized_identity)
            break
    if header_index < 0:
        return [], [f"XLSX first worksheet is missing {identity_field} header"]
    row_ids = [
        row[identity_column].strip()
        for row in rows[header_index + 1 :]
        if identity_column < len(row) and row[identity_column].strip()
    ]
    if len(row_ids) != len(set(row_ids)):
        issues.append(f"XLSX contains duplicate {identity_field} values")
    return row_ids, issues


def workbook_evidence_report(data: bytes) -> dict[str, Any]:
    issues: list[str] = []
    media_count = 0
    anchor_rows: list[int] = []
    formula_errors: list[str] = []
    mandatory_rewrite_formula_count = 0
    drawing_ns = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
    try:
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            corrupt = zf.testzip()
            if corrupt:
                return {
                    "media_count": 0,
                    "picture_anchors": 0,
                    "unique_anchor_rows": 0,
                    "issues": [f"corrupt XLSX member: {corrupt}"],
                }
            names = zf.namelist()
            media_count = sum(name.startswith("xl/media/") and not name.endswith("/") for name in names)
            for name in names:
                if not (name.startswith("xl/drawings/drawing") and name.endswith(".xml")):
                    continue
                root = ET.fromstring(zf.read(name))
                anchors = list(root.findall(f"{{{drawing_ns}}}oneCellAnchor"))
                anchors.extend(root.findall(f"{{{drawing_ns}}}twoCellAnchor"))
                for anchor in anchors:
                    if anchor.find(f"{{{drawing_ns}}}pic") is None:
                        continue
                    row = anchor.find(f"{{{drawing_ns}}}from/{{{drawing_ns}}}row")
                    if row is not None and row.text is not None:
                        anchor_rows.append(int(row.text))
            xml_text = "\n".join(
                zf.read(name).decode("utf-8", "ignore")
                for name in names
                if name.endswith(".xml")
            )
            formula_errors = [
                value
                for value in ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?")
                if value in xml_text
            ]
            mandatory_rewrite_formula_count = (
                xml_text.count("必须选2并重写")
                + xml_text.count("不能直接选1")
            )
    except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
        issues.append(f"invalid workbook evidence: {exc}")
    if formula_errors:
        issues.append(f"formula error literals: {', '.join(formula_errors)}")
    return {
        "media_count": media_count,
        "picture_anchors": len(anchor_rows),
        "unique_anchor_rows": len(set(anchor_rows)),
        "first_anchor_row_zero_based": min(anchor_rows) if anchor_rows else None,
        "last_anchor_row_zero_based": max(anchor_rows) if anchor_rows else None,
        "formula_error_literals": formula_errors,
        "mandatory_rewrite_formula_count": mandatory_rewrite_formula_count,
        "issues": issues,
    }


def verify_inventory(
    zf: zipfile.ZipFile,
    entries: set[str],
    root: str,
) -> dict[str, Any]:
    inventory_name = f"{root}/FILE_INVENTORY.csv"
    sums_name = f"{root}/SHA256SUMS.txt"
    issues: list[str] = []
    if inventory_name not in entries or sums_name not in entries:
        missing = [name for name in ("FILE_INVENTORY.csv", "SHA256SUMS.txt") if f"{root}/{name}" not in entries]
        return {"files": 0, "issues": [f"missing {name}" for name in missing], "valid": False}
    rows = list(csv.DictReader(StringIO(zf.read(inventory_name).decode("utf-8-sig"))))
    expected_hashes: dict[str, str] = {}
    for row in rows:
        relative = str(row.get("relative_path") or "").replace("\\", "/").strip()
        expected_hash = str(row.get("sha256") or "").lower().strip()
        full_name = f"{root}/{relative}"
        if not relative or unsafe_zip_entry(relative):
            issues.append(f"unsafe inventory path: {relative or '<blank>'}")
            continue
        if relative in expected_hashes:
            issues.append(f"duplicate inventory path: {relative}")
            continue
        expected_hashes[relative] = expected_hash
        if full_name not in entries:
            issues.append(f"inventory file missing from ZIP: {relative}")
            continue
        data = zf.read(full_name)
        try:
            expected_size = int(str(row.get("size_bytes") or "-1"))
        except ValueError:
            expected_size = -1
        if len(data) != expected_size:
            issues.append(f"inventory size mismatch: {relative}")
        if hashlib.sha256(data).hexdigest() != expected_hash:
            issues.append(f"inventory hash mismatch: {relative}")
    sums: dict[str, str] = {}
    for line in zf.read(sums_name).decode("ascii", "strict").splitlines():
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if not separator:
            issues.append("invalid SHA256SUMS line")
            continue
        sums[relative] = digest.lower()
    if sums != expected_hashes:
        issues.append("SHA256SUMS does not exactly match FILE_INVENTORY.csv")
    expected_entries = {f"{root}/{relative}" for relative in expected_hashes}
    expected_entries.update({inventory_name, sums_name})
    if entries != expected_entries:
        issues.append("ZIP entries do not exactly match inventory plus control files")
    return {"files": len(rows), "issues": issues, "valid": not issues}


def unsafe_zip_entry(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        normalized.startswith("/")
        or any(part == ".." for part in path.parts)
        or (bool(path.parts) and path.parts[0].endswith(":"))
    )


def verify_delivery(zip_path: Path) -> dict[str, Any]:
    base = verify_review_batch_package.verify_batch_zip(zip_path)
    issues = [f"package: {issue}" for issue in base.get("issues", [])]
    report: dict[str, Any] = {
        "zip_path": zip_path.as_posix(),
        "sha256": "",
        "entry_count": 0,
        "batch_root": base.get("batch_root", ""),
        "base_package_valid": bool(base.get("valid")),
        "embedded_workbooks": [],
        "inventory": {},
        "issues": issues,
        "valid": False,
    }
    if not zip_path.exists():
        report["issues"].append("missing ZIP")
        return report

    report["sha256"] = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [name.replace("\\", "/") for name in zf.namelist()]
            entries = set(names)
            report["entry_count"] = len(names)
            bad_member = zf.testzip()
            if bad_member:
                issues.append(f"corrupt ZIP member: {bad_member}")
            unsafe = sorted(name for name in names if unsafe_zip_entry(name))
            if unsafe:
                issues.append(f"unsafe ZIP entries: {len(unsafe)}")
            nested = sorted(name for name in names if name.lower().endswith(".zip"))
            if nested:
                issues.append(f"nested ZIP files are not allowed: {len(nested)}")

            root = str(report["batch_root"])
            manifest_name = f"{root}/NEXT_REVIEW_BATCH_MANIFEST.csv"
            manifest_rows: list[dict[str, str]] = []
            if manifest_name in entries:
                manifest_rows = list(csv.DictReader(StringIO(zf.read(manifest_name).decode("utf-8-sig"))))
            explicit_workbooks = [
                str(row.get("workbook") or "").strip()
                for row in manifest_rows
                if str(row.get("workbook") or "").strip()
            ]
            workbook_build_report: dict[str, Any] = {}
            if explicit_workbooks:
                for required in (
                    "workbook_build_payload.json",
                    "WORKBOOK_BUILD_REPORT.json",
                    "FINALIZATION_REPORT.json",
                    "FILE_INVENTORY.csv",
                    "SHA256SUMS.txt",
                ):
                    if not root or f"{root}/{required}" not in entries:
                        issues.append(f"missing {required}")
                report["inventory"] = verify_inventory(zf, entries, root)
                issues.extend(f"inventory: {issue}" for issue in report["inventory"].get("issues", []))
                instruction_name = f"{root}/HUMAN_REVIEW_STEPS.md"
                if instruction_name in entries:
                    instruction_text = zf.read(instruction_name).decode("utf-8")
                    for workbook_name in explicit_workbooks:
                        if workbook_name not in instruction_text:
                            issues.append(f"instructions do not name {workbook_name}")
                    return_phrase = f"只交回上述 {len(explicit_workbooks)} 个填写后的 XLSX"
                    if return_phrase not in instruction_text:
                        issues.append("instructions contain an incorrect workbook return count")
                for row in manifest_rows:
                    if str(row.get("safe_to_merge_gold") or "").strip().lower() != "false":
                        issues.append("manifest does not explicitly block Gold merge")
                finalization_name = f"{root}/FINALIZATION_REPORT.json"
                if finalization_name in entries:
                    finalization = json.loads(zf.read(finalization_name).decode("utf-8"))
                    if finalization.get("safe_to_merge_gold") is not False:
                        issues.append("finalization report does not block Gold merge")
                workbook_report_name = f"{root}/WORKBOOK_BUILD_REPORT.json"
                if workbook_report_name in entries:
                    workbook_build_report = json.loads(zf.read(workbook_report_name).decode("utf-8"))
            else:
                for required in ("INTERN_INSTRUCTIONS_ZH.md", "RETURN_ONLY_THIS_XLSX.txt"):
                    if not root or f"{root}/{required}" not in entries:
                        issues.append(f"missing {required}")
            for row in manifest_rows:
                pack_name = str(row.get("pack_name") or "").strip()
                csv_name = str(row.get("checklist") or "").strip()
                if not pack_name or not csv_name:
                    continue
                pack_prefix = f"{root}/review_packs/{pack_name}/"
                csv_path = f"{pack_prefix}{csv_name}"
                explicit_workbook = str(row.get("workbook") or "").strip()
                xlsx_path = (
                    f"{root}/{explicit_workbook}"
                    if explicit_workbook
                    else f"{pack_prefix}{Path(csv_name).with_suffix('.xlsx').name}"
                )
                workbook_report: dict[str, Any] = {
                    "pack": pack_name,
                    "xlsx": xlsx_path,
                    "expected_rows": int(str(row.get("rows") or "0")),
                    "workbook_rows": 0,
                    "candidate_order_match": False,
                    "identity_field": "",
                    "media_count": 0,
                    "picture_anchors": 0,
                    "unique_anchor_rows": 0,
                    "mandatory_rewrite_rows": 0,
                    "mandatory_rewrite_formula_count": 0,
                    "issues": [],
                }
                if xlsx_path not in entries:
                    workbook_report["issues"].append("missing editable XLSX")
                elif csv_path not in entries:
                    workbook_report["issues"].append("missing authoritative CSV")
                else:
                    csv_rows = list(csv.DictReader(StringIO(zf.read(csv_path).decode("utf-8-sig"))))
                    csv_fields = set(csv_rows[0]) if csv_rows else set()
                    identity_field = "pair_id" if "pair_id" in csv_fields else "candidate_id"
                    workbook_report["identity_field"] = identity_field
                    csv_ids = [str(item.get(identity_field) or "").strip() for item in csv_rows]
                    rewrite_rows = [
                        item
                        for item in csv_rows
                        if str(item.get("description_rewrite_required") or "").strip().lower() == "true"
                    ]
                    invalid_rewrite_flags = [
                        str(item.get(identity_field) or "").strip()
                        for item in csv_rows
                        if str(item.get("description_rewrite_required") or "").strip().lower()
                        not in {"", "true", "false"}
                    ]
                    missing_rewrite_tasks = [
                        str(item.get(identity_field) or "").strip()
                        for item in rewrite_rows
                        if not str(item.get("description_task") or "").strip()
                    ]
                    workbook_report["mandatory_rewrite_rows"] = len(rewrite_rows)
                    if invalid_rewrite_flags:
                        workbook_report["issues"].append(
                            f"invalid description_rewrite_required values: {len(invalid_rewrite_flags)}"
                        )
                    if missing_rewrite_tasks:
                        workbook_report["issues"].append(
                            f"mandatory rewrite rows missing description_task: {len(missing_rewrite_tasks)}"
                        )
                    workbook_data = zf.read(xlsx_path)
                    xlsx_ids, xlsx_issues = workbook_row_ids(workbook_data, identity_field)
                    workbook_report["workbook_rows"] = len(xlsx_ids)
                    workbook_report["candidate_order_match"] = xlsx_ids == csv_ids
                    workbook_report["issues"].extend(xlsx_issues)
                    if explicit_workbook:
                        evidence = workbook_evidence_report(workbook_data)
                        workbook_report.update(
                            {
                                key: value
                                for key, value in evidence.items()
                                if key != "issues"
                            }
                        )
                        workbook_report["issues"].extend(evidence["issues"])
                        expected_rows = workbook_report["expected_rows"]
                        if workbook_report["media_count"] != expected_rows:
                            workbook_report["issues"].append(
                                f"embedded image count mismatch: expected {expected_rows}, "
                                f"found {workbook_report['media_count']}"
                            )
                        if workbook_report["picture_anchors"] != expected_rows:
                            workbook_report["issues"].append(
                                f"picture anchor count mismatch: expected {expected_rows}, "
                                f"found {workbook_report['picture_anchors']}"
                            )
                        if workbook_report["unique_anchor_rows"] != expected_rows:
                            workbook_report["issues"].append(
                                f"unique picture anchor row mismatch: expected {expected_rows}, "
                                f"found {workbook_report['unique_anchor_rows']}"
                            )
                        if rewrite_rows:
                            formula_count = workbook_report["mandatory_rewrite_formula_count"]
                            if formula_count != len(rewrite_rows):
                                workbook_report["issues"].append(
                                    "mandatory rewrite formula count mismatch: "
                                    f"expected {len(rewrite_rows)}, found {formula_count}"
                                )
                            build_workbooks = workbook_build_report.get("workbooks", [])
                            build_row = next(
                                (
                                    item
                                    for item in build_workbooks
                                    if str(item.get("workbook") or "") == explicit_workbook
                                ),
                                {},
                            )
                            reported_count = build_row.get("description_rewrite_required")
                            if reported_count != len(rewrite_rows):
                                workbook_report["issues"].append(
                                    "workbook build report rewrite count mismatch: "
                                    f"expected {len(rewrite_rows)}, found {reported_count!r}"
                                )
                    if len(xlsx_ids) != workbook_report["expected_rows"]:
                        workbook_report["issues"].append(
                            f"XLSX row count mismatch: expected {workbook_report['expected_rows']}, found {len(xlsx_ids)}"
                        )
                    if xlsx_ids != csv_ids:
                        workbook_report["issues"].append(
                            f"XLSX {identity_field} order does not match CSV"
                        )
                report["embedded_workbooks"].append(workbook_report)
                issues.extend(f"{pack_name}: {issue}" for issue in workbook_report["issues"])
    except (OSError, zipfile.BadZipFile) as exc:
        issues.append(f"invalid ZIP: {exc}")

    report["valid"] = not issues
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Review Batch Delivery Verification",
        "",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        f"- ZIP: `{report.get('zip_path', '')}`",
        f"- SHA-256: `{report.get('sha256', '')}`",
        f"- Entries: `{report.get('entry_count', 0)}`",
        f"- Embedded workbooks: `{len(report.get('embedded_workbooks', []))}`",
        "",
    ]
    for workbook in report.get("embedded_workbooks", []):
        lines.append(
            f"- `{workbook['pack']}`: {workbook['workbook_rows']}/{workbook['expected_rows']} rows, "
            f"candidate order match `{str(workbook['candidate_order_match']).lower()}`, "
            f"images/anchors `{workbook.get('media_count', 0)}/{workbook.get('picture_anchors', 0)}`, "
            f"mandatory rewrites/formulas `{workbook.get('mandatory_rewrite_rows', 0)}/"
            f"{workbook.get('mandatory_rewrite_formula_count', 0)}`"
        )
    if report.get("inventory"):
        lines.append(
            f"- Inventory: `{report['inventory'].get('files', 0)}` files, "
            f"valid `{str(report['inventory'].get('valid', False)).lower()}`"
        )
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="zip_path", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = verify_delivery(Path(args.zip_path))
    if args.output_json:
        write_json(Path(args.output_json), report)
        print(f"[OK] Wrote {args.output_json}")
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
        print(f"[OK] Wrote {args.output_md}")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
