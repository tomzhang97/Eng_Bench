#!/usr/bin/env python3
"""Build a flat, one-workbook-per-person Eng_Bench human-audit ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import verify_multi_reviewer_handoff as workbook_io
    from . import verify_simple_multi_reviewer_handoff as simple_io
except ImportError:  # Direct script execution from tools/.
    import verify_multi_reviewer_handoff as workbook_io
    import verify_simple_multi_reviewer_handoff as simple_io


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assignments(source_batch: Path, workbook_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "role": "primary_reviewer",
            "rows": 498,
            "microtext_rows": 369,
            "visualdiff_rows": 129,
            "expected_media": 498,
            "source": source_batch / "01_PRIMARY_REVIEWER" / "PRIMARY_REVIEW_498.xlsx",
            "workbook": workbook_dir / "PRIMARY_REVIEW_498.xlsx",
            "name": "PRIMARY_REVIEW_498.xlsx",
        }
    ]
    for number in range(1, 11):
        token = f"{number:02d}"
        name = f"AUDITOR_{token}_REVIEW_12.xlsx"
        rows.append(
            {
                "role": f"auditor_{token}",
                "rows": 12,
                "microtext_rows": 9,
                "visualdiff_rows": 3,
                "expected_media": 12,
                "source": (
                    source_batch
                    / "02_INDEPENDENT_AUDITORS"
                    / f"auditor_{token}"
                    / name
                ),
                "workbook": workbook_dir / name,
                "name": name,
            }
        )
    return rows


def guide_text() -> str:
    return """Eng_Bench Gold v2.0 Global - 发包人只看这一页

你只做 4 件事：
1. 完整解压 ZIP。不要在压缩包预览窗口中编辑 Excel。
2. 把 PRIMARY_REVIEW_498.xlsx 和 01_MESSAGE_TO_PRIMARY_ZH.txt 发给主审核实习生。
3. 把 AUDITOR_01_REVIEW_12.xlsx 至 AUDITOR_10_REVIEW_12.xlsx 一人一份发给 10 位不同复核员，并附上 02_MESSAGE_TO_AUDITOR_ZH.txt。
4. 收回原文件名不变的 11 个 XLSX。不要收截图、CSV、PDF 或新建表格。

审核人员实际只做：看图 -> D 列下拉选结论 -> 只有纠正格变黄时才补写。每个 XLSX 的“开始”页也写了完整规则。

重要：
- 10 位复核员必须独立盲审，不能看实习生或其他复核员的答案。
- 11 个 XLSX 已内嵌全部 618 张要看的图片，不需要另发图片文件夹、HTML 或 CSV。
- 实习生 498 条：369 MicroText + 129 VisualDiff，其中已包含 Wave39 DOE 95 条。
- 每位复核员 12 条：9 MicroText + 3 VisualDiff；10 人共覆盖 120 个互不重复样本。
- 本包只收集人工结论，不会直接修改 active gold。
"""


def primary_message_text() -> str:
    return """请完成附件 PRIMARY_REVIEW_498.xlsx，共 498 条。

每一行都只做 3 步：
1. 下载并打开 XLSX，不要在微信/压缩包预览中编辑。
2. 先看 B 列图片，再核对 C 列机器猜测。
3. 在 D 列下拉选结论。只有 E/F/G 自动变黄时才补写；没变黄就留空。

看 K 列即可查漏：红色=还没完成，绿色=完成，黄色“待整页（可交回）”=这一行可以先跳过。

MicroText：
- accepted：图片完整，机器文字和类别都正确；不用再填。
- edited：图片有效，但文字或类别错；只填变黄的 E/F。
- rejected：缺字、截断、模糊、无效标签或明确作废；G 写短原因。
- needs_full_page：仅靠截图无法可靠判断。

VisualDiff：
- edit：OLD/NEW 框内有真实工程变化；E 写一句具体变化。
- reject_unclear：两图相同、仅整体轻微偏移、变化只在框外或无法确认；F 写短原因。
- needs_full_page：必须看整页才能判断。

只记住 3 个易错点：
- 截图少字母时，即使机器猜出了完整文字，也选 rejected。
- 明确被删除线划掉的标签选 rejected；普通引线、边框或下划线不算删除。
- VisualDiff 完全相同或只有轻微整体偏移，不算 layout change，选 reject_unclear。

全部 K 列变成绿色或黄色后，保存原文件名 PRIMARY_REVIEW_498.xlsx，只交回这个 XLSX。不要删除、新增或排序行，不要另存为 CSV。
"""


def auditor_message_text() -> str:
    return """请独立完成附件 AUDITOR_XX_REVIEW_12.xlsx，共 12 条。不要向其他人要答案，也不要看主审核实习生的结果。

每一行都只做 3 步：
1. 下载并打开 XLSX；先看“开始”页。
2. 先看 B 列图片，再核对 C 列机器猜测。
3. 在 D 列下拉选结论；只有 E/F/G 自动变黄时才补写。

看 K 列即可查漏：红色=还没完成，绿色=完成，黄色“待整页（可交回）”=这一行可以先跳过。

MicroText：
- accepted = 图片完整，文字和类别都对。
- edited = 图片有效，但文字或类别错；只填变黄的 E/F。
- rejected = 缺字/截断/模糊/无效标签/明确被划掉；G 写短原因。
- needs_full_page = 截图不足以判断。

VisualDiff：
- edit = OLD/NEW 框内确有真实工程变化；E 写一句具体变化。
- reject_unclear = 两图相同、仅整体轻微偏移、变化在框外或无法确认；F 写短原因。
- needs_full_page = 必须看整页才能判断。

全部 K 列变成绿色或黄色后，保存原文件名，只交回这个 XLSX。你是在独立重做这 12 条，不要复制他人的答案，也不要发送截图或 CSV。
"""


def instruction_files() -> dict[str, str]:
    return {
        "00_OWNER_READ_FIRST_ZH.txt": guide_text(),
        "01_MESSAGE_TO_PRIMARY_ZH.txt": primary_message_text(),
        "02_MESSAGE_TO_AUDITOR_ZH.txt": auditor_message_text(),
    }


def _media_hashes(path: Path) -> Counter[str]:
    return simple_io.workbook_media_hashes(path)


def _first_sheet_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        sheet_names = list(workbook_io.workbook_sheet_paths(archive))
    if not sheet_names:
        return ""
    rows, _, _ = workbook_io.read_xlsx_sheet(path, sheet_names[0])
    return " ".join(value for row in rows for value in row)


def workbook_report(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    source = Path(item["source"])
    workbook = Path(item["workbook"])
    issues: list[str] = []
    if not source.exists():
        return {"path": workbook.as_posix(), "valid": False}, [f"missing source {source}"]
    if not workbook.exists():
        return {"path": workbook.as_posix(), "valid": False}, [f"missing workbook {workbook}"]

    try:
        with zipfile.ZipFile(workbook, "r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                issues.append(f"{workbook.name}: XLSX CRC failure at {corrupt}")
        source_micro, _ = workbook_io.table_records(source, "MicroText")
        source_visual, _ = workbook_io.table_records(source, "VisualDiff")
        micro, micro_meta = workbook_io.table_records(workbook, "MicroText")
        visual, visual_meta = workbook_io.table_records(workbook, "VisualDiff")
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        return {"path": workbook.as_posix(), "valid": False}, [f"{workbook.name}: {exc}"]

    if micro != source_micro:
        issues.append(f"{workbook.name}: MicroText values/order differ from canonical source")
    if visual != source_visual:
        issues.append(f"{workbook.name}: VisualDiff values/order differ from canonical source")
    if len(micro) != int(item["microtext_rows"]):
        issues.append(
            f"{workbook.name}: expected {item['microtext_rows']} MicroText rows, found {len(micro)}"
        )
    if len(visual) != int(item["visualdiff_rows"]):
        issues.append(
            f"{workbook.name}: expected {item['visualdiff_rows']} VisualDiff rows, found {len(visual)}"
        )
    if micro_meta["formula_count"] < len(micro):
        issues.append(f"{workbook.name}: missing MicroText completion formulas")
    if visual_meta["formula_count"] < len(visual):
        issues.append(f"{workbook.name}: missing VisualDiff completion formulas")
    if not micro_meta["data_validation"] or not visual_meta["data_validation"]:
        issues.append(f"{workbook.name}: missing decision dropdown validation")

    micro_values = simple_io.validation_values(workbook, "MicroText", "D")
    visual_values = simple_io.validation_values(workbook, "VisualDiff", "D")
    if micro_values != simple_io.MICRO_STATUS_VALUES:
        issues.append(f"{workbook.name}: invalid MicroText dropdown {sorted(micro_values)}")
    if visual_values != simple_io.VISUAL_STATUS_VALUES:
        issues.append(f"{workbook.name}: invalid VisualDiff dropdown {sorted(visual_values)}")

    actual_media = _media_hashes(workbook)
    source_media = _media_hashes(source)
    if actual_media != source_media:
        issues.append(
            f"{workbook.name}: embedded evidence differs from canonical source "
            f"({sum(actual_media.values())} actual vs {sum(source_media.values())} expected)"
        )
    if sum(actual_media.values()) != int(item["expected_media"]):
        issues.append(
            f"{workbook.name}: expected {item['expected_media']} images, "
            f"found {sum(actual_media.values())}"
        )

    start_text = _first_sheet_text(workbook)
    for term in (
        "Gold v2.0 Global",
        "只做两步",
        "最省时顺序",
        "needs_full_page",
        "只交回",
    ):
        if term not in start_text:
            issues.append(f"{workbook.name}: start sheet missing {term!r}")

    cached_errors = simple_io.invalid_error_cells(workbook)
    issues.extend(f"{workbook.name}: {issue}" for issue in cached_errors)
    return {
        "path": workbook.as_posix(),
        "name": workbook.name,
        "role": item["role"],
        "rows": len(micro) + len(visual),
        "microtext_rows": len(micro),
        "visualdiff_rows": len(visual),
        "embedded_images": sum(actual_media.values()),
        "formula_count": micro_meta["formula_count"] + visual_meta["formula_count"],
        "dropdowns_present": bool(
            micro_meta["data_validation"] and visual_meta["data_validation"]
        ),
        "sha256": sha256(workbook),
        "issues": issues,
        "valid": not issues,
    }, issues


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def verify_delivery(
    source_batch: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    zip_path: Path | None = None,
) -> dict[str, Any]:
    expected = assignments(source_batch.resolve(), workbook_dir.resolve())
    issues: list[str] = []
    reports: list[dict[str, Any]] = []
    expected_names = {*instruction_files(), *(item["name"] for item in expected)}

    guide = delivery_dir / "00_OWNER_READ_FIRST_ZH.txt"
    if not guide.exists():
        issues.append("missing 00_OWNER_READ_FIRST_ZH.txt")
    else:
        value = guide.read_text(encoding="utf-8")
        for term in (
            "你只做 4 件事",
            "一人一份",
            "独立盲审",
            "内嵌全部 618 张",
            "Wave39 DOE 95 条",
        ):
            if term not in value:
                issues.append(f"guide missing {term!r}")

    primary_message = delivery_dir / "01_MESSAGE_TO_PRIMARY_ZH.txt"
    auditor_message = delivery_dir / "02_MESSAGE_TO_AUDITOR_ZH.txt"
    message_terms = {
        primary_message: (
            "每一行都只做 3 步",
            "D 列下拉",
            "K 列即可查漏",
            "needs_full_page",
            "只交回这个 XLSX",
        ),
        auditor_message: (
            "独立完成",
            "每一行都只做 3 步",
            "K 列即可查漏",
            "needs_full_page",
            "不要复制他人的答案",
        ),
    }
    for path, terms in message_terms.items():
        if not path.exists():
            issues.append(f"missing {path.name}")
            continue
        value = path.read_text(encoding="utf-8")
        for term in terms:
            if term not in value:
                issues.append(f"{path.name} missing {term!r}")

    for item in expected:
        delivered = delivery_dir / item["name"]
        check_item = dict(item)
        check_item["workbook"] = delivered
        report, workbook_issues = workbook_report(check_item)
        reports.append(report)
        issues.extend(workbook_issues)

    actual_names = {path.name for path in delivery_dir.iterdir() if path.is_file()}
    if actual_names != expected_names:
        issues.append(
            f"delivery file set mismatch: missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    subdirectories = sorted(path.name for path in delivery_dir.iterdir() if path.is_dir())
    if subdirectories:
        issues.append(f"delivery contains subdirectories: {subdirectories}")

    zip_report: dict[str, Any] | None = None
    if zip_path is not None:
        zip_issues: list[str] = []
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
                corrupt = archive.testzip()
                nested = sorted(name for name in names if name.lower().endswith(".zip"))
                unsafe = sorted(name for name in names if unsafe_zip_name(name))
                roots = sorted({PurePosixPath(name).parts[0] for name in names})
                expected_entries = {
                    f"{delivery_dir.name}/{name}" for name in expected_names
                }
                if corrupt:
                    zip_issues.append(f"ZIP CRC failure at {corrupt}")
                if nested:
                    zip_issues.append(f"nested ZIP entries present: {nested}")
                if unsafe:
                    zip_issues.append(f"unsafe ZIP entries present: {unsafe}")
                if roots != [delivery_dir.name]:
                    zip_issues.append(f"unexpected ZIP roots: {roots}")
                if set(names) != expected_entries:
                    zip_issues.append("ZIP file set differs from flat delivery directory")
                with tempfile.TemporaryDirectory() as temporary:
                    archive.extractall(temporary)
                    extracted = Path(temporary) / delivery_dir.name
                    for name in expected_names:
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
            "entry_count": len(expected_names),
            "issues": zip_issues,
            "valid": not zip_issues,
        }

    return {
        "goal": "Gold v2.0 Global",
        "source_batch": source_batch.resolve().as_posix(),
        "workbook_dir": workbook_dir.resolve().as_posix(),
        "delivery_dir": delivery_dir.resolve().as_posix(),
        "primary_rows": 498,
        "primary_microtext_rows": 369,
        "primary_visualdiff_rows": 129,
        "wave39_rows_included": 95,
        "auditors": 10,
        "rows_per_auditor": 12,
        "unique_double_review_rows": 120,
        "workbook_count": len(reports),
        "embedded_images": sum(report.get("embedded_images", 0) for report in reports),
        "flat_files": len(expected_names),
        "instruction_files": len(instruction_files()),
        "subdirectories": 0,
        "nested_zip_files": 0,
        "gold_rows_modified": 0,
        "workbooks": reports,
        "zip": zip_report,
        "issues": issues,
        "valid": not issues,
    }


def build_delivery(
    source_batch: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
) -> dict[str, Any]:
    source_batch = source_batch.resolve()
    workbook_dir = workbook_dir.resolve()
    delivery_dir = delivery_dir.resolve()
    zip_path = zip_path.resolve()
    if not source_batch.is_dir():
        raise FileNotFoundError(source_batch)
    if not workbook_dir.is_dir():
        raise FileNotFoundError(workbook_dir)
    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)
    for name, content in instruction_files().items():
        (delivery_dir / name).write_text(content, encoding="utf-8")
    for item in assignments(source_batch, workbook_dir):
        workbook = Path(item["workbook"])
        if not workbook.exists():
            raise FileNotFoundError(workbook)
        shutil.copy2(workbook, delivery_dir / item["name"])

    preliminary = verify_delivery(source_batch, workbook_dir, delivery_dir)
    if not preliminary["valid"]:
        return preliminary

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
            for path in sorted(delivery_dir.iterdir()):
                if path.is_file():
                    archive.write(path, f"{delivery_dir.name}/{path.name}")
        temporary_zip.replace(zip_path)
    finally:
        if temporary_zip.exists():
            temporary_zip.unlink()
    return verify_delivery(source_batch, workbook_dir, delivery_dir, zip_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-batch", type=Path, required=True)
    parser.add_argument("--workbook-dir", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_delivery(
        args.source_batch,
        args.workbook_dir,
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
