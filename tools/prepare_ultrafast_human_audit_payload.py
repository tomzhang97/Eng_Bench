#!/usr/bin/env python3
"""Prepare a stable payload and evidence images for the ultrafast audit forms.

The existing canonical workbooks remain the assignment source of truth. This
tool only reads them and extracts their embedded evidence; it never edits gold.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import verify_multi_reviewer_handoff as workbook_io
except ImportError:  # Direct script execution from tools/.
    import verify_multi_reviewer_handoff as workbook_io


PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

START_SHEET = "\u5f00\u59cb"
MICRO_SHEET = "MicroText"
VISUAL_SHEET = "VisualDiff"

INDEX = "#"
MICRO_SUGGESTION = "\u673a\u5668\u5efa\u8bae\uff08\u4e0d\u4e00\u5b9a\u5bf9\uff09"
MICRO_CORRECT_TEXT = "\u6b63\u786e\u6587\u5b57\uff08\u4ec5\u9700\u4fee\u6539\uff09"
MICRO_CORRECT_CATEGORY = "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5\u9700\u4fee\u6539\uff09"
MICRO_FULL_PAGE = "\u6574\u9875\u8def\u5f84\uff08\u4e0d\u786e\u5b9a\u65f6\u590d\u5236\u6253\u5f00\uff09"
VISUAL_SUGGESTION = "\u673a\u5668\u63d0\u793a\uff08\u4e0d\u4e00\u5b9a\u5bf9\uff09"
VISUAL_DESCRIPTION = "\u53d8\u5316\u63cf\u8ff0\uff08\u5df2\u9884\u586b\u7684\u53ea\u9700\u6838\u5bf9\uff09"
VISUAL_OLD_PAGE = "\u65e7\u7248\u6574\u9875\u8def\u5f84"
VISUAL_NEW_PAGE = "\u65b0\u7248\u6574\u9875\u8def\u5f84"


def ensure_child(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"output path must be below {root}: {resolved}")


def resolve_archive_target(source_path: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(
        posixpath.join(PurePosixPath(source_path).parent.as_posix(), target)
    )


def relationships_path(part_path: str) -> str:
    part = PurePosixPath(part_path)
    return (part.parent / "_rels" / f"{part.name}.rels").as_posix()


def relation_targets(archive: zipfile.ZipFile, part_path: str) -> dict[str, str]:
    rels_path = relationships_path(part_path)
    if rels_path not in archive.namelist():
        return {}
    root = ET.fromstring(archive.read(rels_path))
    return {
        relation.get("Id", ""): resolve_archive_target(
            part_path, relation.get("Target", "")
        )
        for relation in root.findall(f"{{{PKG_REL_NS}}}Relationship")
        if relation.get("TargetMode") != "External"
    }


def extract_sheet_images(
    workbook: Path,
    sheet_name: str,
    output_dir: Path,
) -> dict[int, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(workbook, "r") as archive:
        sheets = workbook_io.workbook_sheet_paths(archive)
        sheet_path = sheets.get(sheet_name)
        if not sheet_path:
            raise KeyError(f"{workbook}: missing sheet {sheet_name}")
        sheet_relations = relation_targets(archive, sheet_path)
        drawing_paths = sorted(
            target
            for target in sheet_relations.values()
            if "/drawings/" in target and target.endswith(".xml")
        )
        if not drawing_paths:
            raise ValueError(f"{workbook}: {sheet_name} has no drawing relationship")

        extracted: dict[int, Path] = {}
        for drawing_path in drawing_paths:
            drawing_relations = relation_targets(archive, drawing_path)
            drawing = ET.fromstring(archive.read(drawing_path))
            for anchor in list(drawing):
                picture = anchor.find(f"{{{DRAWING_NS}}}pic")
                start = anchor.find(f"{{{DRAWING_NS}}}from")
                if picture is None or start is None:
                    continue
                row_node = start.find(f"{{{DRAWING_NS}}}row")
                blip = picture.find(f".//{{{DRAWINGML_NS}}}blip")
                if row_node is None or blip is None:
                    continue
                relationship_id = blip.get(f"{{{OFFICE_REL_NS}}}embed", "")
                media_path = drawing_relations.get(relationship_id, "")
                if not media_path or media_path not in archive.namelist():
                    raise ValueError(
                        f"{workbook}: missing media for {sheet_name} relationship {relationship_id}"
                    )
                excel_row = int(row_node.text or "0") + 1
                if excel_row in extracted:
                    raise ValueError(
                        f"{workbook}: multiple evidence pictures on {sheet_name} row {excel_row}"
                    )
                suffix = PurePosixPath(media_path).suffix.lower() or ".png"
                destination = output_dir / f"row_{excel_row:04d}{suffix}"
                destination.write_bytes(archive.read(media_path))
                extracted[excel_row] = destination
        return extracted


def required(row: dict[str, str], key: str, context: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{context}: missing {key}")
    return value


def primary_rows(workbook: Path, evidence_dir: Path) -> list[dict[str, Any]]:
    micro, _ = workbook_io.table_records(workbook, MICRO_SHEET)
    visual, _ = workbook_io.table_records(workbook, VISUAL_SHEET)
    micro_images = extract_sheet_images(workbook, MICRO_SHEET, evidence_dir / "microtext")
    visual_images = extract_sheet_images(workbook, VISUAL_SHEET, evidence_dir / "visualdiff")
    if len(micro) != 369 or len(visual) != 129:
        raise ValueError(
            f"{workbook}: expected 369 MicroText and 129 VisualDiff rows, "
            f"found {len(micro)} and {len(visual)}"
        )

    rows: list[dict[str, Any]] = []
    for excel_row, row in enumerate(micro, start=2):
        image = micro_images.get(excel_row)
        if image is None:
            raise ValueError(f"{workbook}: MicroText row {excel_row} has no evidence image")
        rows.append(
            {
                "task": "microtext",
                "display_index": required(row, INDEX, f"MicroText row {excel_row}"),
                "primary_index": required(row, "primary_index", f"MicroText row {excel_row}"),
                "candidate_id": required(row, "candidate_id", f"MicroText row {excel_row}"),
                "machine_suggestion": required(
                    row, MICRO_SUGGESTION, f"MicroText row {excel_row}"
                ),
                "corrected_text": str(row.get(MICRO_CORRECT_TEXT, "")),
                "corrected_category": str(row.get(MICRO_CORRECT_CATEGORY, "")),
                "full_page_path": str(row.get(MICRO_FULL_PAGE, "")),
                "evidence_path": image.resolve().as_posix(),
            }
        )
    for excel_row, row in enumerate(visual, start=2):
        image = visual_images.get(excel_row)
        if image is None:
            raise ValueError(f"{workbook}: VisualDiff row {excel_row} has no evidence image")
        rows.append(
            {
                "task": "visualdiff",
                "display_index": required(row, INDEX, f"VisualDiff row {excel_row}"),
                "primary_index": required(row, "primary_index", f"VisualDiff row {excel_row}"),
                "pair_id": required(row, "pair_id", f"VisualDiff row {excel_row}"),
                "machine_suggestion": required(
                    row, VISUAL_SUGGESTION, f"VisualDiff row {excel_row}"
                ),
                "change_description": str(row.get(VISUAL_DESCRIPTION, "")),
                "old_page_path": str(row.get(VISUAL_OLD_PAGE, "")),
                "new_page_path": str(row.get(VISUAL_NEW_PAGE, "")),
                "evidence_path": image.resolve().as_posix(),
            }
        )
    primary_indexes = [row["primary_index"] for row in rows]
    if len(set(primary_indexes)) != 498:
        raise ValueError("primary workbook does not contain 498 unique primary indexes")
    return rows


def auditor_rows(
    workbook: Path,
    primary_by_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    micro, _ = workbook_io.table_records(workbook, MICRO_SHEET)
    visual, _ = workbook_io.table_records(workbook, VISUAL_SHEET)
    if len(micro) != 9 or len(visual) != 3:
        raise ValueError(
            f"{workbook}: expected 9 MicroText and 3 VisualDiff rows, "
            f"found {len(micro)} and {len(visual)}"
        )
    rows: list[dict[str, Any]] = []
    for source, task in [(row, "microtext") for row in micro] + [
        (row, "visualdiff") for row in visual
    ]:
        primary_index = required(source, "primary_index", workbook.name)
        primary = primary_by_index.get(primary_index)
        if primary is None:
            raise ValueError(f"{workbook}: primary index {primary_index} is missing")
        identifier_key = "candidate_id" if task == "microtext" else "pair_id"
        identifier = required(source, identifier_key, workbook.name)
        if primary["task"] != task or primary[identifier_key] != identifier:
            raise ValueError(f"{workbook}: primary identity mismatch at {primary_index}")
        row = dict(primary)
        row["display_index"] = required(source, INDEX, workbook.name)
        rows.append(row)
    if len({row["primary_index"] for row in rows}) != 12:
        raise ValueError(f"{workbook}: duplicate auditor assignment")
    return rows


def build_payload(
    canonical_dir: Path,
    output_dir: Path,
    output_json: Path,
    overwrite: bool,
) -> dict[str, Any]:
    canonical_dir = canonical_dir.resolve()
    output_dir = output_dir.resolve()
    output_json = output_json.resolve()
    allowed_root = output_json.parents[2] if len(output_json.parents) >= 3 else output_json.parent
    ensure_child(output_dir, allowed_root)
    ensure_child(output_json, allowed_root)
    if not canonical_dir.is_dir():
        raise FileNotFoundError(canonical_dir)
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(output_dir)
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    primary_workbook = canonical_dir / "PRIMARY_REVIEW_498.xlsx"
    primary = primary_rows(primary_workbook, output_dir / "evidence")
    primary_by_index = {row["primary_index"]: row for row in primary}
    auditors: list[dict[str, Any]] = []
    all_auditor_indexes: list[str] = []
    for number in range(1, 11):
        name = f"AUDITOR_{number:02d}_REVIEW_12.xlsx"
        rows = auditor_rows(canonical_dir / name, primary_by_index)
        all_auditor_indexes.extend(row["primary_index"] for row in rows)
        auditors.append({"number": number, "workbook": name, "rows": rows})
    if len(all_auditor_indexes) != 120 or len(set(all_auditor_indexes)) != 120:
        raise ValueError("auditor assignments are not 120 unique primary rows")

    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "ultrafast human audit; one required answer per row",
        "canonical_dir": canonical_dir.as_posix(),
        "primary": {
            "workbook": primary_workbook.name,
            "rows": primary,
        },
        "auditors": auditors,
        "counts": {
            "primary": len(primary),
            "primary_microtext": sum(row["task"] == "microtext" for row in primary),
            "primary_visualdiff": sum(row["task"] == "visualdiff" for row in primary),
            "auditors": len(auditors),
            "auditor_rows": len(all_auditor_indexes),
            "unique_auditor_rows": len(set(all_auditor_indexes)),
            "evidence_images": len(list((output_dir / "evidence").rglob("*.*"))),
        },
        "gold_rows_modified": 0,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload["counts"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    counts = build_payload(
        args.canonical_dir,
        args.output_dir,
        args.output_json,
        args.overwrite,
    )
    print(json.dumps(counts, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
