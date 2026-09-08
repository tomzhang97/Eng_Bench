#!/usr/bin/env python3
"""Build the one-workbook-per-person Eng_Bench human-audit delivery."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import build_easiest_human_audit_delivery as base
except ImportError:  # Direct script execution from tools/.
    import build_easiest_human_audit_delivery as base


README_NAME = "00_READ_ME_FIRST_CN.txt"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
WORKBOOK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)
RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SPREADSHEET_DRAWING_NS = (
    "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def owner_guide() -> str:
    reviewer_map = "\n".join(
        f"- 复核员 {number:02d}：AUDITOR_{number:02d}_REVIEW_12.xlsx"
        for number in range(1, 11)
    )
    return f"""Eng_Bench Gold v2.0 Global - 最简单发包说明

你只需要做 3 步：
1. 完整解压本 ZIP。不要直接在微信或压缩包预览里编辑。
2. 每人只发一个 XLSX，不要另发图片、CSV、说明文档或其他人的答案。
3. 收回文件名不变的 11 个 XLSX；不要收截图、PDF 或重新制作的表。

发给谁：
- 主审实习生：PRIMARY_REVIEW_498.xlsx
{reviewer_map}

可直接发给主审实习生的话：
“请下载并用 Excel/WPS 打开附件，先看‘开始’页。全部图片和完整步骤都在表内。每行先看 B 列图片和 C 列机器建议，再在黄色 D 列选结论；只有选择‘需修改’或确认真实变化且描述为空/错误时才需要打字。完成后保持原文件名，只交回这个 XLSX。”

可直接发给每位复核员的话：
“请下载并用 Excel/WPS 打开附件，先看‘开始’页。你只需独立完成 12 条：每行看 B 列图片和 C 列机器建议，在黄色 D 列选一个结论。无需改文字或写变化描述。完成后保持原文件名，只交回这个 XLSX。不要看或复制其他人的答案。”

主审实习生的最省时流程：
- 共 498 条：369 条 MicroText + 129 条 VisualDiff，已包含 Wave39 DOE 的全部 95 条。
- 每行必做：只选一次黄色 D 列。
- MicroText 选“正确”后直接下一行；选“需修改”时只改错误的文字或类别；裁剪缺字、模糊、无效标签、明确被删除线划掉时选“不合格”；必须看整页才能判断时选“看不清”。
- VisualDiff 红框内有真实工程变化时选“有真实变化”；两图相同、只有轻微整体偏移、变化只在红框外时选“无有效变化”；无法可靠判断时选“看不清”。
- 48 条文字型 VisualDiff 已预填描述，正确时不需要重写。

10 位独立复核员的最省时流程：
- 每人 12 条，且 10 份样本互不重复；合计复核 120 条。
- 每行只选黄色 D 列。MicroText 选“通过 / 有问题 / 看不清”；VisualDiff 选“有真实变化 / 无有效变化 / 看不清”。
- 不要求改字、改类别或写描述。E 列问题类型和备注都可以留空。
- 必须独立盲审，不要询问主审或其他复核员。

共同规则：
- 截图少字母或只显示半个字母，即使机器猜对完整文字，也不能通过。
- 明确被删除线划掉的标签不能通过；普通引线、边框或下划线不算删除线。
- VisualDiff 完全相同、只有一点整体偏移、或变化只在红框外，都选“无有效变化”。
- 工作簿“开始”页显示“可以交回”后保存。不要删除、增加、排序行，也不要另存为 CSV。

本 ZIP 的设计目标就是每人只处理一个 Excel。所有 618 张证据图片已经嵌入工作簿；没有图片文件夹、没有子 ZIP、没有外部文件依赖。人工返回不会自动写入 active gold，仍需机器侧安全门检查后才能晋升。
"""


def expected_names() -> set[str]:
    names = {README_NAME, "PRIMARY_REVIEW_498.xlsx"}
    names.update(f"AUDITOR_{number:02d}_REVIEW_12.xlsx" for number in range(1, 11))
    return names


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def repaired_content_types(data: bytes) -> bytes:
    ET.register_namespace("", CONTENT_TYPES_NS)
    root = ET.fromstring(data)
    default_tag = f"{{{CONTENT_TYPES_NS}}}Default"
    override_tag = f"{{{CONTENT_TYPES_NS}}}Override"
    xml_default = None
    workbook_override = None
    for child in root:
        if child.tag == default_tag and child.get("Extension", "").lower() == "xml":
            xml_default = child
        if child.tag == override_tag and child.get("PartName") == "/xl/workbook.xml":
            workbook_override = child
    if xml_default is None:
        xml_default = ET.Element(
            default_tag,
            {"Extension": "xml", "ContentType": "application/xml"},
        )
        root.insert(0, xml_default)
    else:
        xml_default.set("ContentType", "application/xml")
    if workbook_override is None:
        workbook_override = ET.Element(
            override_tag,
            {"PartName": "/xl/workbook.xml", "ContentType": WORKBOOK_CONTENT_TYPE},
        )
        root.append(workbook_override)
    else:
        workbook_override.set("ContentType", WORKBOOK_CONTENT_TYPE)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def repaired_relationships(data: bytes, relationship_path: str) -> bytes:
    ET.register_namespace("", RELATIONSHIPS_NS)
    root = ET.fromstring(data)
    relationship_tag = f"{{{RELATIONSHIPS_NS}}}Relationship"
    rels_path = PurePosixPath(relationship_path)
    if relationship_path == "_rels/.rels":
        source_directory = "."
    else:
        source_directory = rels_path.parent.parent.as_posix() or "."
    for relationship in root.findall(relationship_tag):
        if relationship.get("TargetMode") == "External":
            continue
        target = relationship.get("Target", "")
        if target.startswith("/"):
            relationship.set(
                "Target",
                posixpath.relpath(target.lstrip("/"), start=source_directory),
            )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def copy_excel_compatible_workbook(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(source, "r") as input_archive, zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=False,
        ) as output_archive:
            for item in input_archive.infolist():
                if item.filename.startswith("xl/tables/"):
                    continue
                data = input_archive.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    data = repaired_content_types(data)
                    root = ET.fromstring(data)
                    for child in list(root):
                        if child.get("PartName", "").startswith("/xl/tables/"):
                            root.remove(child)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                elif item.filename.endswith(".rels"):
                    data = repaired_relationships(data, item.filename)
                    root = ET.fromstring(data)
                    for child in list(root):
                        if child.get("Type", "").endswith("/table"):
                            root.remove(child)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                elif item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                    root = ET.fromstring(data)
                    table_parts_tag = (
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}tableParts"
                    )
                    for child in root.findall(table_parts_tag):
                        root.remove(child)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                output_archive.writestr(item, data)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def ensure_within(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"output path must be below {root}: {resolved}")


def manifest_candidate_ids(path: Path) -> set[str]:
    candidate_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            candidate_id = str(row.get("candidate_id", "")).strip()
            if not candidate_id:
                raise ValueError(f"{path}:{line_number}: missing candidate_id")
            if candidate_id in candidate_ids:
                raise ValueError(f"{path}:{line_number}: duplicate candidate_id {candidate_id}")
            candidate_ids.add(candidate_id)
    return candidate_ids


def workbook_primary_indexes(path: Path) -> set[str]:
    indexes: set[str] = set()
    for sheet_name in ("MicroText", "VisualDiff"):
        rows, _ = base.workbook_io.table_records(path, sheet_name)
        for row in rows:
            value = row.get("primary_index", "").strip()
            if value:
                indexes.add(value)
    return indexes


def workbook_candidate_ids(path: Path) -> set[str]:
    rows, _ = base.workbook_io.table_records(path, "MicroText")
    return {row.get("candidate_id", "").strip() for row in rows if row.get("candidate_id", "").strip()}


def referenced_media_hashes(path: Path) -> Counter[str]:
    hashes: Counter[str] = Counter()
    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        drawing_names = sorted(
            name
            for name in names
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml")
        )
        for drawing_name in drawing_names:
            drawing_path = PurePosixPath(drawing_name)
            rels_name = (
                drawing_path.parent
                / "_rels"
                / f"{drawing_path.name}.rels"
            ).as_posix()
            if rels_name not in names:
                continue
            relationships = ET.fromstring(archive.read(rels_name))
            targets = {
                node.get("Id", ""): node.get("Target", "")
                for node in relationships.findall(
                    f"{{{RELATIONSHIPS_NS}}}Relationship"
                )
                if node.get("TargetMode") != "External"
            }
            drawing = ET.fromstring(archive.read(drawing_name))
            for blip in drawing.iter(f"{{{DRAWINGML_NS}}}blip"):
                relationship_id = blip.get(f"{{{OFFICE_REL_NS}}}embed", "")
                target = targets.get(relationship_id, "")
                if not target:
                    continue
                if target.startswith("/"):
                    media_name = target.lstrip("/")
                else:
                    media_name = posixpath.normpath(
                        posixpath.join(drawing_path.parent.as_posix(), target)
                    )
                if media_name not in names:
                    raise ValueError(f"{path}: missing referenced image {media_name}")
                hashes[hashlib.sha256(archive.read(media_name)).hexdigest()] += 1
    return hashes


def picture_anchor_report(path: Path) -> tuple[int, list[str]]:
    picture_count = 0
    issues: list[str] = []
    with zipfile.ZipFile(path, "r") as archive:
        drawing_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml")
        )
        for drawing_name in drawing_names:
            root = ET.fromstring(archive.read(drawing_name))
            for anchor_index, anchor in enumerate(list(root), start=1):
                if anchor.find(f"{{{SPREADSHEET_DRAWING_NS}}}pic") is None:
                    continue
                picture_count += 1
                if anchor.tag != f"{{{SPREADSHEET_DRAWING_NS}}}twoCellAnchor":
                    issues.append(
                        f"{drawing_name} anchor {anchor_index}: expected twoCellAnchor"
                    )
                    continue
                start = anchor.find(f"{{{SPREADSHEET_DRAWING_NS}}}from")
                end = anchor.find(f"{{{SPREADSHEET_DRAWING_NS}}}to")
                if start is None or end is None:
                    issues.append(
                        f"{drawing_name} anchor {anchor_index}: missing from/to marker"
                    )
                    continue

                def marker_value(marker: ET.Element, name: str) -> int:
                    node = marker.find(f"{{{SPREADSHEET_DRAWING_NS}}}{name}")
                    return int(node.text or "0") if node is not None else -1

                start_col = marker_value(start, "col")
                end_col = marker_value(end, "col")
                start_row = marker_value(start, "row")
                end_row = marker_value(end, "row")
                start_col_offset = marker_value(start, "colOff")
                end_col_offset = marker_value(end, "colOff")
                start_row_offset = marker_value(start, "rowOff")
                end_row_offset = marker_value(end, "rowOff")
                if start_col != 1 or end_col != 1:
                    issues.append(
                        f"{drawing_name} anchor {anchor_index}: picture leaves evidence column B"
                    )
                if start_row != end_row:
                    issues.append(
                        f"{drawing_name} anchor {anchor_index}: picture crosses review rows"
                    )
                if not (
                    0 <= start_col_offset < end_col_offset
                    and 0 <= start_row_offset < end_row_offset
                ):
                    issues.append(
                        f"{drawing_name} anchor {anchor_index}: invalid picture offsets"
                    )
    return picture_count, issues


def verify_polished_workbook(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    report, issues = base.verify_workbook(item)
    media_prefixes = (
        f"{item['name']}: embedded evidence changed",
        f"{item['name']}: expected {item['expected_media']} images",
    )
    issues = [
        issue
        for issue in issues
        if not any(issue.startswith(prefix) for prefix in media_prefixes)
    ]
    source_media = referenced_media_hashes(Path(item["source"]))
    actual_media = referenced_media_hashes(Path(item["workbook"]))
    if actual_media != source_media:
        issues.append(f"{item['name']}: referenced evidence image set changed")
    if sum(actual_media.values()) != item["expected_media"]:
        issues.append(
            f"{item['name']}: expected {item['expected_media']} referenced images, "
            f"found {sum(actual_media.values())}"
        )
    anchor_count, anchor_issues = picture_anchor_report(Path(item["workbook"]))
    if anchor_count != item["expected_media"]:
        issues.append(
            f"{item['name']}: expected {item['expected_media']} picture anchors, "
            f"found {anchor_count}"
        )
    issues.extend(f"{item['name']}: {issue}" for issue in anchor_issues)
    report["embedded_images"] = sum(actual_media.values())
    report["media_parts"] = sum(base.simple_io.workbook_media_hashes(Path(item["workbook"])).values())
    report["picture_anchors"] = anchor_count
    report["picture_anchor_issues"] = anchor_issues
    report["issues"] = issues
    report["valid"] = not issues
    return report, issues


def verify_delivery(
    source_dir: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    wave39_manifest: Path,
    zip_path: Path | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    reports: list[dict[str, Any]] = []
    required_terms = [
        "每人只发一个 XLSX",
        "PRIMARY_REVIEW_498.xlsx",
        "Wave39 DOE 的全部 95 条",
        "每人 12 条",
        "所有 618 张证据图片已经嵌入工作簿",
    ]
    guide_path = delivery_dir / README_NAME
    if not guide_path.exists():
        issues.append(f"missing {README_NAME}")
    else:
        guide = guide_path.read_text(encoding="utf-8-sig")
        for term in required_terms:
            if term not in guide:
                issues.append(f"{README_NAME}: missing {term!r}")

    assignments = base.assignments(source_dir.resolve(), workbook_dir.resolve())
    for item in assignments:
        check = dict(item)
        check["workbook"] = delivery_dir / item["name"]
        report, workbook_issues = verify_polished_workbook(check)
        reports.append(report)
        issues.extend(workbook_issues)

    actual_names = {path.name for path in delivery_dir.iterdir() if path.is_file()}
    if actual_names != expected_names():
        issues.append(
            f"delivery file mismatch: missing={sorted(expected_names() - actual_names)}, "
            f"extra={sorted(actual_names - expected_names())}"
        )
    subdirectories = [path.name for path in delivery_dir.iterdir() if path.is_dir()]
    if subdirectories:
        issues.append(f"delivery contains subdirectories: {sorted(subdirectories)}")

    primary = delivery_dir / "PRIMARY_REVIEW_498.xlsx"
    wave39_ids = manifest_candidate_ids(wave39_manifest)
    primary_candidate_ids = workbook_candidate_ids(primary)
    wave39_missing = sorted(wave39_ids - primary_candidate_ids)
    if len(wave39_ids) != 95:
        issues.append(f"Wave39 manifest has {len(wave39_ids)} unique rows, expected 95")
    if wave39_missing:
        issues.append(f"primary workbook is missing {len(wave39_missing)} Wave39 rows")

    primary_indexes = workbook_primary_indexes(primary)
    auditor_indexes: list[str] = []
    for number in range(1, 11):
        workbook = delivery_dir / f"AUDITOR_{number:02d}_REVIEW_12.xlsx"
        auditor_indexes.extend(sorted(workbook_primary_indexes(workbook)))
    auditor_unique = set(auditor_indexes)
    if len(auditor_indexes) != 120:
        issues.append(f"auditor assignment count is {len(auditor_indexes)}, expected 120")
    if len(auditor_unique) != 120:
        issues.append(f"auditor assignments contain {120 - len(auditor_unique)} duplicate rows")
    if not auditor_unique.issubset(primary_indexes):
        issues.append("auditor assignments include rows outside the primary workbook")

    zip_report: dict[str, Any] | None = None
    if zip_path is not None:
        zip_issues: list[str] = []
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
                corrupt = archive.testzip()
                if corrupt:
                    zip_issues.append(f"ZIP CRC failure at {corrupt}")
                nested = [name for name in names if name.lower().endswith(".zip")]
                if nested:
                    zip_issues.append(f"nested ZIP entries: {sorted(nested)}")
                unsafe = [name for name in names if unsafe_zip_name(name)]
                if unsafe:
                    zip_issues.append(f"unsafe ZIP entries: {sorted(unsafe)}")
                if set(names) != expected_names():
                    zip_issues.append("ZIP root file set differs from delivery directory")
                with tempfile.TemporaryDirectory() as temporary:
                    archive.extractall(temporary)
                    extracted = Path(temporary)
                    for name in expected_names():
                        original = delivery_dir / name
                        copied = extracted / name
                        if not copied.exists() or sha256(original) != sha256(copied):
                            zip_issues.append(f"extracted file mismatch: {name}")
        except (OSError, zipfile.BadZipFile) as exc:
            zip_issues.append(f"invalid ZIP: {exc}")
        issues.extend(zip_issues)
        zip_report = {
            "path": zip_path.resolve().as_posix(),
            "size_bytes": zip_path.stat().st_size if zip_path.exists() else 0,
            "sha256": sha256(zip_path) if zip_path.exists() else "",
            "entry_count": len(expected_names()),
            "root_files_only": True,
            "issues": zip_issues,
            "valid": not zip_issues,
        }

    return {
        "goal": "Gold v2.0 Global",
        "workflow": "one workbook per person; one required decision per row",
        "primary_rows": 498,
        "primary_microtext_rows": 369,
        "primary_visualdiff_rows": 129,
        "wave39_manifest_rows": len(wave39_ids),
        "wave39_rows_in_primary": len(wave39_ids & primary_candidate_ids),
        "auditors": 10,
        "rows_per_auditor": 12,
        "unique_double_review_rows": len(auditor_unique),
        "workbook_count": len(reports),
        "embedded_images": sum(report.get("embedded_images", 0) for report in reports),
        "delivery_files": len(expected_names()),
        "instruction_files": 1,
        "nested_zip_files": 0,
        "gold_rows_modified": 0,
        "workbooks": reports,
        "zip": zip_report,
        "issues": issues,
        "valid": not issues,
    }


def build_delivery(
    root: Path,
    source_dir: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    wave39_manifest: Path,
    zip_path: Path,
    overwrite: bool,
) -> dict[str, Any]:
    root = root.resolve()
    source_dir = source_dir.resolve()
    workbook_dir = workbook_dir.resolve()
    delivery_dir = delivery_dir.resolve()
    wave39_manifest = wave39_manifest.resolve()
    zip_path = zip_path.resolve()
    allowed_output_root = root / "derived" / "human_adjudication"
    ensure_within(delivery_dir, allowed_output_root)
    ensure_within(zip_path, allowed_output_root)
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    if not workbook_dir.is_dir():
        raise FileNotFoundError(workbook_dir)
    if not wave39_manifest.is_file():
        raise FileNotFoundError(wave39_manifest)
    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)
    (delivery_dir / README_NAME).write_text(owner_guide(), encoding="utf-8-sig")
    for item in base.assignments(source_dir, workbook_dir):
        source = Path(item["workbook"])
        if not source.exists():
            raise FileNotFoundError(source)
        copy_excel_compatible_workbook(source, delivery_dir / item["name"])

    preliminary = verify_delivery(
        source_dir,
        workbook_dir,
        delivery_dir,
        wave39_manifest,
    )
    if not preliminary["valid"]:
        return preliminary
    write_delivery_zip(delivery_dir, zip_path, overwrite)
    return verify_delivery(
        source_dir,
        workbook_dir,
        delivery_dir,
        wave39_manifest,
        zip_path,
    )


def write_delivery_zip(
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
) -> None:
    if zip_path.exists():
        if not overwrite:
            raise FileExistsError(zip_path)
        zip_path.unlink()
    temporary_zip = zip_path.with_name(f".{zip_path.name}.tmp")
    if temporary_zip.exists():
        temporary_zip.unlink()
    try:
        with zipfile.ZipFile(
            temporary_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=False,
        ) as archive:
            for path in sorted(delivery_dir.iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
        temporary_zip.replace(zip_path)
    finally:
        if temporary_zip.exists():
            temporary_zip.unlink()


def package_existing_delivery(
    root: Path,
    source_dir: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    wave39_manifest: Path,
    zip_path: Path,
    overwrite: bool,
) -> dict[str, Any]:
    root = root.resolve()
    source_dir = source_dir.resolve()
    workbook_dir = workbook_dir.resolve()
    delivery_dir = delivery_dir.resolve()
    wave39_manifest = wave39_manifest.resolve()
    zip_path = zip_path.resolve()
    allowed_output_root = root / "derived" / "human_adjudication"
    ensure_within(delivery_dir, allowed_output_root)
    ensure_within(zip_path, allowed_output_root)
    preliminary = verify_delivery(
        source_dir,
        workbook_dir,
        delivery_dir,
        wave39_manifest,
    )
    if not preliminary["valid"]:
        return preliminary
    write_delivery_zip(delivery_dir, zip_path, overwrite)
    return verify_delivery(
        source_dir,
        workbook_dir,
        delivery_dir,
        wave39_manifest,
        zip_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--workbook-dir", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--wave39-manifest", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--package-existing",
        action="store_true",
        help="verify and package an already polished delivery directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    operation = package_existing_delivery if args.package_existing else build_delivery
    report = operation(
        args.root,
        args.source_dir,
        args.workbook_dir,
        args.delivery_dir,
        args.wave39_manifest,
        args.zip_path,
        args.overwrite,
    )
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
