#!/usr/bin/env python3
"""Build and verify a one-workbook-per-person Eng_Bench delivery ZIP.

The source batch remains the machine-side return-processing template. The
delivery ZIP intentionally contains only the eleven image-embedded workbooks
and one Chinese distribution guide. Uncertain rows may be returned as
``needs_full_page`` and remain held outside gold for project-side follow-up.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assignments(source_batch: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "role": "primary_reviewer",
            "rows": 498,
            "expected_media": 498,
            "source": source_batch / "01_PRIMARY_REVIEWER" / "PRIMARY_REVIEW_498.xlsx",
            "folder": "01_PRIMARY_REVIEWER_498",
            "workbook": "PRIMARY_REVIEW_498.xlsx",
        }
    ]
    for number in range(1, 11):
        rows.append(
            {
                "role": f"auditor_{number:02d}",
                "rows": 12,
                "expected_media": 12,
                "source": (
                    source_batch
                    / "02_INDEPENDENT_AUDITORS"
                    / f"auditor_{number:02d}"
                    / f"AUDITOR_{number:02d}_REVIEW_12.xlsx"
                ),
                "folder": f"02_AUDITOR_{number:02d}_12",
                "workbook": f"AUDITOR_{number:02d}_REVIEW_12.xlsx",
            }
        )
    return rows


def guide_text() -> str:
    return """Eng_Bench Gold v2.0 Global - 最省时人工审核分发说明

一、你作为分发人只做这些事
1. 把 01_PRIMARY_REVIEWER_498/PRIMARY_REVIEW_498.xlsx 发给主审核员/实习生。
2. 把 02_AUDITOR_01_12 到 02_AUDITOR_10_12 中的 Excel 分别发给十位不同复核员。
3. 每个人只收到并填写一个 Excel。不要把整个 ZIP 发给同一个审核员。
4. 十位复核员必须独立判断，不得查看、询问或复制主审核员及其他人的答案。
5. 收回时只收 Excel，文件名必须保持不变。

二、每个审核员怎么做
1. 打开 Excel，先看“开始”工作表。
2. 依次查看 MicroText 和 VisualDiff 工作表中的嵌入图片。
3. 只填写黄色列；灰色列、编号、公式和机器建议不要修改。
4. 机器建议仅供参考，必须以图片为准。
5. 截图不足或确实无法判断时选 needs_full_page，然后继续下一行；可以直接交回。
6. 当“开始”页显示“可以交回”时，关闭并交回原文件。

三、MicroText 快速规则
- accepted：截图完整，机器文字和类别都正确；修正栏留空。
- edited：截图有效，但文字或类别错误；填写正确文字和/或正确类别。
- rejected：缺字、截断、模糊、不是有效工程标签，或文字被明确删除线作废；填写简短原因。
- needs_full_page：仅凭截图无法可靠判断；项目侧会另行处理，不会直接进入 gold。
- 如果截图只有 B-9A，而机器猜成完整的 MB-9A，仍然 rejected，因为证据截图缺字。
- 普通引线、边框或下划线不算“被划掉”；只有明确删除/作废线才 rejected。

四、VisualDiff 快速规则
- edit：OLD/NEW 裁剪框内确实发生工程内容变化；填写准确变化描述。
- reject_unclear：两图完全相同、只有整体轻微偏移、变化只在框外，或裁剪内无法确认；填写简短原因。
- needs_full_page：必须看整页才能判断；项目侧后续处理。
- 两图完全一样不是 layout；一丁点整体偏移通常也不是 layout，均选 reject_unclear。

五、工作量和覆盖
- 主审核员：498 条 = 369 MicroText + 129 VisualDiff。
- 498 条中包含 Wave39 DOE Handbook MicroText 的全部 95 条。
- 每位独立复核员：12 条 = 9 MicroText + 3 VisualDiff。
- 十位复核员合计独立复核 120 个不重复样本，用于一致性统计。

重要：本 ZIP 只收集人工结论，不会自动修改 active gold。所有返回结果仍需经过来源、去重、泄漏、split 和 strict validator 门禁。
"""


def workbook_report(path: Path, expected_media: int) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    media_count = 0
    sheet_names: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                issues.append(f"{path.name}: inner XLSX CRC failure at {corrupt}")
            media_count = sum(
                1
                for name in archive.namelist()
                if name.startswith("xl/media/") and not name.endswith("/")
            )
            if media_count != expected_media:
                issues.append(
                    f"{path.name}: expected {expected_media} embedded images, found {media_count}"
                )
            workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
            sheet_names = [
                item.attrib.get("name", "")
                for item in workbook_xml.findall(f".//{{{MAIN_NS}}}sheet")
            ]
            if sheet_names != ["开始", "MicroText", "VisualDiff"]:
                issues.append(f"{path.name}: unexpected sheets {sheet_names}")
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        issues.append(f"{path.name}: unreadable workbook: {exc}")
    return {
        "path": path.as_posix(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "sha256": sha256(path) if path.exists() else "",
        "embedded_images": media_count,
        "sheet_names": sheet_names,
        "valid": not issues,
    }, issues


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def verify_delivery(
    source_batch: Path,
    delivery_dir: Path,
    zip_path: Path | None = None,
) -> dict[str, Any]:
    source_batch = source_batch.resolve()
    delivery_dir = delivery_dir.resolve()
    issues: list[str] = []
    expected = assignments(source_batch)
    expected_files = {"00_START_HERE_ZH.txt"}
    workbook_reports: list[dict[str, Any]] = []

    guide = delivery_dir / "00_START_HERE_ZH.txt"
    if not guide.exists():
        issues.append("missing 00_START_HERE_ZH.txt")
    else:
        text = guide.read_text(encoding="utf-8")
        for term in (
            "每个人只收到并填写一个 Excel",
            "needs_full_page",
            "文件名必须保持不变",
            "必须独立判断",
            "Wave39",
        ):
            if term not in text:
                issues.append(f"distribution guide missing {term!r}")

    for item in expected:
        relative = Path(item["folder"]) / item["workbook"]
        expected_files.add(relative.as_posix())
        delivered = delivery_dir / relative
        source = Path(item["source"])
        if not source.exists():
            issues.append(f"missing source workbook {source}")
            continue
        if not delivered.exists():
            issues.append(f"missing delivered workbook {relative.as_posix()}")
            continue
        report, workbook_issues = workbook_report(delivered, int(item["expected_media"]))
        report.update({"role": item["role"], "rows": item["rows"]})
        workbook_reports.append(report)
        issues.extend(workbook_issues)
        if sha256(delivered) != sha256(source):
            issues.append(f"{relative.as_posix()}: differs from canonical return template")

    actual_files = {
        path.relative_to(delivery_dir).as_posix()
        for path in delivery_dir.rglob("*")
        if path.is_file()
    }
    extra = sorted(actual_files - expected_files)
    missing = sorted(expected_files - actual_files)
    if extra:
        issues.append(f"unexpected delivery files: {extra}")
    if missing:
        issues.append(f"missing delivery files: {missing}")

    zip_report: dict[str, Any] | None = None
    if zip_path is not None:
        zip_path = zip_path.resolve()
        zip_issues: list[str] = []
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = archive.namelist()
                corrupt = archive.testzip()
                nested = sorted(name for name in names if name.lower().endswith(".zip"))
                unsafe = sorted(name for name in names if unsafe_zip_name(name))
                roots = sorted({PurePosixPath(name).parts[0] for name in names if name})
                if corrupt:
                    zip_issues.append(f"ZIP CRC failure at {corrupt}")
                if nested:
                    zip_issues.append(f"nested ZIP entries present: {nested}")
                if unsafe:
                    zip_issues.append(f"unsafe ZIP entries present: {unsafe}")
                if roots != [delivery_dir.name]:
                    zip_issues.append(f"expected ZIP root {delivery_dir.name!r}, found {roots}")
                with tempfile.TemporaryDirectory() as temporary:
                    archive.extractall(temporary)
                    extracted = Path(temporary) / delivery_dir.name
                    extracted_files = {
                        path.relative_to(extracted).as_posix()
                        for path in extracted.rglob("*")
                        if path.is_file()
                    }
                    if extracted_files != expected_files:
                        zip_issues.append("extracted file set differs from delivery directory")
                    for item in expected:
                        relative = Path(item["folder"]) / item["workbook"]
                        extracted_book = extracted / relative
                        source = Path(item["source"])
                        if not extracted_book.exists() or sha256(extracted_book) != sha256(source):
                            zip_issues.append(
                                f"extracted workbook mismatch: {relative.as_posix()}"
                            )
        except (OSError, zipfile.BadZipFile) as exc:
            zip_issues.append(f"invalid ZIP: {exc}")
        issues.extend(zip_issues)
        zip_report = {
            "path": zip_path.as_posix(),
            "size_bytes": zip_path.stat().st_size if zip_path.exists() else 0,
            "sha256": sha256(zip_path) if zip_path.exists() else "",
            "issues": zip_issues,
            "valid": not zip_issues,
        }

    return {
        "goal": "Gold v2.0 Global",
        "source_batch": source_batch.as_posix(),
        "delivery_dir": delivery_dir.as_posix(),
        "primary_rows": 498,
        "primary_microtext_rows": 369,
        "primary_visualdiff_rows": 129,
        "wave39_rows_included": 95,
        "auditors": 10,
        "rows_per_auditor": 12,
        "unique_double_review_rows": 120,
        "workbooks": workbook_reports,
        "workbook_count": len(workbook_reports),
        "embedded_images": sum(item["embedded_images"] for item in workbook_reports),
        "nested_zip_files": 0,
        "gold_rows_modified": 0,
        "zip": zip_report,
        "issues": issues,
        "valid": not issues,
    }


def build_delivery(
    source_batch: Path,
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
) -> dict[str, Any]:
    source_batch = source_batch.resolve()
    delivery_dir = delivery_dir.resolve()
    zip_path = zip_path.resolve()
    if not source_batch.is_dir():
        raise FileNotFoundError(source_batch)
    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)

    (delivery_dir / "00_START_HERE_ZH.txt").write_text(guide_text(), encoding="utf-8")
    for item in assignments(source_batch):
        source = Path(item["source"])
        if not source.exists():
            raise FileNotFoundError(source)
        destination = delivery_dir / item["folder"] / item["workbook"]
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)

    if zip_path.exists():
        if not overwrite:
            raise FileExistsError(zip_path)
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
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
            for path in sorted(delivery_dir.rglob("*")):
                if path.is_file():
                    archive.write(
                        path,
                        (Path(delivery_dir.name) / path.relative_to(delivery_dir)).as_posix(),
                    )
        temporary_zip.replace(zip_path)
    finally:
        if temporary_zip.exists():
            temporary_zip.unlink()

    return verify_delivery(source_batch, delivery_dir, zip_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-batch", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--report-json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_delivery(
        args.source_batch,
        args.delivery_dir,
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
