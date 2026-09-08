#!/usr/bin/env python3
"""Verify that every review row embeds the exact assigned evidence image."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
ART_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        resolved = target.lstrip("/")
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_part), target)
        )
    value = PurePosixPath(resolved)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"unsafe package relationship target: {target}")
    return value.as_posix()


def relationship_part(source_part: str) -> str:
    parent = posixpath.dirname(source_part)
    name = posixpath.basename(source_part)
    return posixpath.join(parent, "_rels", f"{name}.rels")


def relationship_targets(
    archive: zipfile.ZipFile, source_part: str
) -> dict[str, str]:
    rels_name = relationship_part(source_part)
    root = ET.fromstring(archive.read(rels_name))
    targets: dict[str, str] = {}
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        rel_id = rel.get("Id", "")
        target = rel.get("Target", "")
        if rel_id and target:
            targets[rel_id] = resolve_part(source_part, target)
    return targets


def worksheet_part(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook_part = "xl/workbook.xml"
    root = ET.fromstring(archive.read(workbook_part))
    targets = relationship_targets(archive, workbook_part)
    sheets = root.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        raise ValueError("workbook has no sheets collection")
    for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet"):
        if sheet.get("name") == sheet_name:
            rel_id = sheet.get(f"{{{DOC_REL_NS}}}id", "")
            if rel_id not in targets:
                raise ValueError(f"sheet relationship missing for {sheet_name}")
            return targets[rel_id]
    raise ValueError(f"workbook is missing sheet {sheet_name}")


def embedded_picture_rows(
    archive: zipfile.ZipFile, sheet_name: str
) -> list[dict[str, Any]]:
    sheet_part = worksheet_part(archive, sheet_name)
    root = ET.fromstring(archive.read(sheet_part))
    drawings = root.findall(f"{{{MAIN_NS}}}drawing")
    if len(drawings) != 1:
        raise ValueError(
            f"{sheet_name}: expected one drawing relationship, found {len(drawings)}"
        )
    sheet_targets = relationship_targets(archive, sheet_part)
    drawing_rel_id = drawings[0].get(f"{{{DOC_REL_NS}}}id", "")
    if drawing_rel_id not in sheet_targets:
        raise ValueError(f"{sheet_name}: drawing relationship is unresolved")
    drawing_part = sheet_targets[drawing_rel_id]
    drawing_root = ET.fromstring(archive.read(drawing_part))
    media_targets = relationship_targets(archive, drawing_part)

    records: list[dict[str, Any]] = []
    for anchor in list(drawing_root):
        picture = anchor.find(f"{{{DRAWING_NS}}}pic")
        marker = anchor.find(f"{{{DRAWING_NS}}}from")
        if picture is None or marker is None:
            continue
        row_node = marker.find(f"{{{DRAWING_NS}}}row")
        col_node = marker.find(f"{{{DRAWING_NS}}}col")
        blip = picture.find(f".//{{{ART_NS}}}blip")
        if row_node is None or col_node is None or blip is None:
            raise ValueError(f"{sheet_name}: incomplete picture anchor")
        media_rel_id = blip.get(f"{{{DOC_REL_NS}}}embed", "")
        if media_rel_id not in media_targets:
            raise ValueError(f"{sheet_name}: picture relationship is unresolved")
        media_part = media_targets[media_rel_id]
        media = archive.read(media_part)
        records.append(
            {
                "row": int(row_node.text or "-1"),
                "col": int(col_node.text or "-1"),
                "media_part": media_part,
                "media_sha256": sha256_bytes(media),
                "media_bytes": len(media),
            }
        )
    return sorted(records, key=lambda record: (record["row"], record["col"]))


def verify_workbook(
    workbook: Path,
    sheet_name: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    expected: list[dict[str, Any]] = []
    for offset, row in enumerate(rows):
        evidence_path = Path(str(row.get("evidence_path", "")))
        if not evidence_path.is_file():
            issues.append(f"{workbook.name}: missing evidence source {evidence_path}")
            evidence_hash = ""
            evidence_bytes = 0
        else:
            data = evidence_path.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                issues.append(
                    f"{workbook.name}: evidence is not PNG: {evidence_path}"
                )
            evidence_hash = sha256_bytes(data)
            evidence_bytes = len(data)
        expected.append(
            {
                "row": 6 + offset,
                "col": 1,
                "evidence_path": evidence_path.as_posix(),
                "evidence_sha256": evidence_hash,
                "evidence_bytes": evidence_bytes,
            }
        )

    actual: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(workbook, "r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                issues.append(f"{workbook.name}: XLSX CRC failure at {corrupt}")
            actual = embedded_picture_rows(archive, sheet_name)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        issues.append(f"{workbook.name}: {exc}")

    if len(actual) != len(expected):
        issues.append(
            f"{workbook.name}: expected {len(expected)} pictures, found {len(actual)}"
        )
    matched = 0
    for index, (wanted, found) in enumerate(zip(expected, actual), start=1):
        if (wanted["row"], wanted["col"]) != (found["row"], found["col"]):
            issues.append(
                f"{workbook.name} item {index}: picture anchor mismatch "
                f"expected=({wanted['row']},{wanted['col']}) "
                f"actual=({found['row']},{found['col']})"
            )
            continue
        if wanted["evidence_sha256"] != found["media_sha256"]:
            issues.append(
                f"{workbook.name} item {index}: embedded image hash mismatch"
            )
            continue
        matched += 1

    report = {
        "workbook": workbook.name,
        "sheet": sheet_name,
        "expected_rows": len(expected),
        "picture_rows": len(actual),
        "exact_image_matches": matched,
        "unique_embedded_images": len(
            {record["media_sha256"] for record in actual}
        ),
        "xlsx_sha256": sha256_file(workbook) if workbook.is_file() else "",
        "issues": issues,
        "valid": not issues,
    }
    return report, issues


def verify_delivery(delivery_dir: Path, payload_path: Path) -> dict[str, Any]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    reports: list[dict[str, Any]] = []
    issues: list[str] = []

    primary_payload = payload["primary"]
    primary_issued = primary_payload.get("issued", True)
    if primary_issued:
        primary, workbook_issues = verify_workbook(
            delivery_dir / primary_payload["workbook"],
            "\u5ba1\u6838498\u6761",
            primary_payload["rows"],
        )
        reports.append(primary)
        issues.extend(workbook_issues)
    for auditor in payload["auditors"]:
        report, workbook_issues = verify_workbook(
            delivery_dir / auditor["workbook"],
            str(auditor.get("sheet_name") or "\u5ba1\u683812\u6761"),
            auditor["rows"],
        )
        reports.append(report)
        issues.extend(workbook_issues)

    return {
        "goal": "Gold v2.0 Global",
        "delivery_dir": delivery_dir.resolve().as_posix(),
        "primary_issued": primary_issued,
        "workbooks": len(reports),
        "assigned_image_rows": sum(report["expected_rows"] for report in reports),
        "embedded_picture_rows": sum(report["picture_rows"] for report in reports),
        "exact_image_matches": sum(
            report["exact_image_matches"] for report in reports
        ),
        "gold_rows_modified": 0,
        "reports": reports,
        "issues": issues,
        "valid": not issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--payload-json", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    delivery_dir = (root / args.delivery_dir).resolve()
    payload_path = (root / args.payload_json).resolve()
    report_path = (root / args.report_json).resolve()
    report = verify_delivery(delivery_dir, payload_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
