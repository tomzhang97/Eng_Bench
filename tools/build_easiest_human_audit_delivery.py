#!/usr/bin/env python3
"""Package and verify the lowest-friction Eng_Bench human-audit delivery."""
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


PRIMARY_MICRO_CHOICES = {"正确", "需修改", "不合格", "看不清"}
AUDITOR_MICRO_CHOICES = {"通过", "有问题", "看不清"}
VISUAL_CHOICES = {"有真实变化", "无有效变化", "看不清"}
ISSUE_CHOICES = {"文字错误", "类别错误", "裁剪或模糊", "无效标签", "其他"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assignments(source_dir: Path, workbook_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "role": "primary",
            "reviewer_id": "primary_reviewer",
            "name": "PRIMARY_REVIEW_498.xlsx",
            "rows": 498,
            "microtext_rows": 369,
            "visualdiff_rows": 129,
            "expected_media": 498,
        }
    ]
    for number in range(1, 11):
        token = f"{number:02d}"
        rows.append(
            {
                "role": "auditor",
                "reviewer_id": f"auditor_{token}",
                "name": f"AUDITOR_{token}_REVIEW_12.xlsx",
                "rows": 12,
                "microtext_rows": 9,
                "visualdiff_rows": 3,
                "expected_media": 12,
            }
        )
    for item in rows:
        item["source"] = source_dir / item["name"]
        item["workbook"] = workbook_dir / item["name"]
    return rows


def owner_guide() -> str:
    return """Eng_Bench Gold v2.0 Global - 发包人操作（只看这一页）

这次只发 11 个 Excel：
- 主审实习生：PRIMARY_REVIEW_498.xlsx（498 条）
- 10 位独立复核员：AUDITOR_01_REVIEW_12.xlsx 到 AUDITOR_10_REVIEW_12.xlsx（每人 12 条）

你只做 4 步：
1. 完整解压 ZIP；不要直接在微信或压缩包预览里编辑。
2. 把 PRIMARY_REVIEW_498.xlsx 和 01_SEND_TO_PRIMARY_ZH.txt 发给主审实习生。
3. 给 10 位不同复核员各发一个 AUDITOR_XX_REVIEW_12.xlsx，并附 02_SEND_TO_AUDITOR_ZH.txt。每人只收到自己的文件。
4. 收回原文件名不变的 11 个 XLSX。不要收截图、CSV、PDF，也不要让他们重新做表。

所有图片都已经嵌入 Excel，不需要另发图片文件夹。10 位复核员必须独立判断，不能看主审或其他人的结果。

工作量：主审 498 条（369 MicroText + 129 VisualDiff，包含 Wave39 DOE 95 条）；复核共 120 条（10 人 x 12），样本互不重复。
本包只收集人工判断，不会自动写入 active gold。
"""


def primary_guide() -> str:
    return """请完成 PRIMARY_REVIEW_498.xlsx，共 498 条。

最省时做法：
1. 下载后用 Excel/WPS 打开；先看“开始”页。
2. 打开 MicroText 或 VisualDiff 页。每行先看 B 列图片，再看 C 列机器建议。
3. 只在黄色 D 列选择一个结论。大多数行选完就直接做下一行。

MicroText 的 D 列：
- 正确：截图完整，机器文字和类别都正确。不用再填。
- 需修改：截图有效，但文字或类别错误。只在 E/F 中改错误的那一项；至少填一项。
- 不合格：截图缺字、截断、模糊、不是有效工程标签，或文字明确被划掉。不用补写；G 可选。
- 看不清：必须看整页才能可靠判断。不要猜，直接下一行。

VisualDiff 的 D 列：
- 有真实变化：OLD/NEW 红框内确有工程内容变化。E 列若已预填且正确，不用改；空白或错误时写一句准确描述。
- 无有效变化：两图相同、只有轻微整体偏移、变化只在框外，或不是工程变化。
- 看不清：必须看整页才能可靠判断。

其中 48 条文字型 VisualDiff 已预填变化描述；只需核对，正确时不要重写。

三个重要规则：
- 截图少字母时，即使机器猜到完整文字，也选“不合格”。
- 明确删除线划掉的标签选“不合格”；普通引线、边框、下划线不算删除线。
- VisualDiff 完全相同或只有轻微整体偏移，不算变化，选“无有效变化”。

K 列是完成检查。所有行 K 列变绿，且“开始”页 B13 显示“可以交回”后，保存原文件名 PRIMARY_REVIEW_498.xlsx，只交回这个 XLSX。不要删除、新增、排序行，也不要另存 CSV。
"""


def auditor_guide() -> str:
    return """请独立完成你收到的 AUDITOR_XX_REVIEW_12.xlsx，共 12 条。

每行只做一次选择：
1. 下载后用 Excel/WPS 打开；先看“开始”页。
2. 每行先看 B 列图片，再看 C 列机器建议。
3. 在黄色 D 列选择一个结论。选完马上做下一行；不要求你改文字或写描述。

MicroText：
- 通过：截图完整，机器文字和类别都正确。
- 有问题：文字、类别、裁剪完整性或标签有效性任一有问题。E 列问题类型可选，不填也能交回。
- 看不清：必须看整页才能判断；不要猜。

VisualDiff：
- 有真实变化：OLD/NEW 红框内确有工程内容变化。
- 无有效变化：两图相同、只有轻微整体偏移、变化只在框外，或不是工程变化。
- 看不清：必须看整页才能判断。

必须独立盲审，不要询问主审或其他复核员，也不要复制他们的答案。所有行 K 列变绿，且“开始”页 B13 显示“可以交回”后，保留原文件名，只交回这个 XLSX；不要发截图或 CSV。
"""


def instruction_files() -> dict[str, str]:
    return {
        "00_OWNER_READ_FIRST_ZH.txt": owner_guide(),
        "01_SEND_TO_PRIMARY_ZH.txt": primary_guide(),
        "02_SEND_TO_AUDITOR_ZH.txt": auditor_guide(),
    }


def _stable_micro(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row.get("#", ""),
        row.get("机器建议（不一定对）", ""),
        row.get("整页路径（不确定时复制打开）", ""),
        row.get("candidate_id", ""),
        row.get("primary_index", ""),
    )


def _stable_visual(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row.get("#", ""),
        row.get("机器提示（不一定对）", ""),
        row.get("旧版整页路径", ""),
        row.get("新版整页路径", ""),
        row.get("pair_id", ""),
        row.get("primary_index", ""),
    )


def _first_sheet_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        sheet_names = list(workbook_io.workbook_sheet_paths(archive))
    if not sheet_names:
        return ""
    rows, _, _ = workbook_io.read_xlsx_sheet(path, sheet_names[0])
    return " ".join(value for row in rows for value in row)


def verify_workbook(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    source = Path(item["source"])
    workbook = Path(item["workbook"])
    issues: list[str] = []
    if not source.exists():
        return {"name": item["name"], "valid": False}, [f"missing source {source}"]
    if not workbook.exists():
        return {"name": item["name"], "valid": False}, [f"missing workbook {workbook}"]
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
        return {"name": item["name"], "valid": False}, [f"{workbook.name}: {exc}"]

    if len(micro) != item["microtext_rows"]:
        issues.append(f"{workbook.name}: MicroText row count {len(micro)}")
    if len(visual) != item["visualdiff_rows"]:
        issues.append(f"{workbook.name}: VisualDiff row count {len(visual)}")
    if [_stable_micro(row) for row in micro] != [_stable_micro(row) for row in source_micro]:
        issues.append(f"{workbook.name}: MicroText IDs/source/order changed")
    if [_stable_visual(row) for row in visual] != [_stable_visual(row) for row in source_visual]:
        issues.append(f"{workbook.name}: VisualDiff IDs/source/order changed")
    if any(row.get("结论（必填）", "") for row in micro + visual):
        issues.append(f"{workbook.name}: decision cells are not blank")

    if micro_meta["formula_count"] < len(micro) or visual_meta["formula_count"] < len(visual):
        issues.append(f"{workbook.name}: missing completion formulas")
    if not micro_meta["data_validation"] or not visual_meta["data_validation"]:
        issues.append(f"{workbook.name}: missing decision dropdown")

    micro_choices = simple_io.validation_values(workbook, "MicroText", "D")
    visual_choices = simple_io.validation_values(workbook, "VisualDiff", "D")
    expected_micro = PRIMARY_MICRO_CHOICES if item["role"] == "primary" else AUDITOR_MICRO_CHOICES
    if micro_choices != expected_micro:
        issues.append(f"{workbook.name}: MicroText choices {sorted(micro_choices)}")
    if visual_choices != VISUAL_CHOICES:
        issues.append(f"{workbook.name}: VisualDiff choices {sorted(visual_choices)}")
    if item["role"] == "auditor":
        issue_choices = simple_io.validation_values(workbook, "MicroText", "E")
        if issue_choices != ISSUE_CHOICES:
            issues.append(f"{workbook.name}: issue choices {sorted(issue_choices)}")

    actual_media: Counter[str] = simple_io.workbook_media_hashes(workbook)
    source_media: Counter[str] = simple_io.workbook_media_hashes(source)
    if actual_media != source_media:
        issues.append(f"{workbook.name}: embedded evidence changed")
    if sum(actual_media.values()) != item["expected_media"]:
        issues.append(
            f"{workbook.name}: expected {item['expected_media']} images, found {sum(actual_media.values())}"
        )

    start_text = _first_sheet_text(workbook)
    required_start = ["Gold v2.0 Global", "D 列", "可以交回"]
    if item["role"] == "primary":
        required_start += ["大多数行只需一次点击", "需修改", "无有效变化"]
    else:
        required_start += ["每行只做一次独立判断", "有问题", "不要看别人的答案"]
    for term in required_start:
        if term not in start_text:
            issues.append(f"{workbook.name}: start sheet missing {term!r}")

    cached_errors = simple_io.invalid_error_cells(workbook)
    issues.extend(f"{workbook.name}: {issue}" for issue in cached_errors)
    prefilled = 0
    if item["role"] == "primary":
        prefilled = sum(bool(row.get("变化描述（已预填的只需核对）", "")) for row in visual)
        if prefilled != 48:
            issues.append(f"{workbook.name}: expected 48 VisualDiff prefills, found {prefilled}")

    return {
        "name": workbook.name,
        "role": item["role"],
        "reviewer_id": item["reviewer_id"],
        "rows": len(micro) + len(visual),
        "microtext_rows": len(micro),
        "visualdiff_rows": len(visual),
        "embedded_images": sum(actual_media.values()),
        "prefilled_visual_descriptions": prefilled,
        "formula_count": micro_meta["formula_count"] + visual_meta["formula_count"],
        "sha256": sha256(workbook),
        "issues": issues,
        "valid": not issues,
    }, issues


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def verify_delivery(
    source_dir: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    zip_path: Path | None = None,
) -> dict[str, Any]:
    expected = assignments(source_dir.resolve(), workbook_dir.resolve())
    expected_names = {*instruction_files(), *(item["name"] for item in expected)}
    issues: list[str] = []
    workbook_reports: list[dict[str, Any]] = []

    for name, required_terms in {
        "00_OWNER_READ_FIRST_ZH.txt": ["只做 4 步", "11 个 XLSX", "所有图片都已经嵌入"],
        "01_SEND_TO_PRIMARY_ZH.txt": ["大多数行选完", "48", "K 列", "只交回这个 XLSX"],
        "02_SEND_TO_AUDITOR_ZH.txt": ["每行只做一次选择", "不要求你改文字", "独立盲审"],
    }.items():
        path = delivery_dir / name
        if not path.exists():
            issues.append(f"missing {name}")
            continue
        text = path.read_text(encoding="utf-8")
        for term in required_terms:
            if term not in text:
                issues.append(f"{name}: missing {term!r}")

    for item in expected:
        check = dict(item)
        check["workbook"] = delivery_dir / item["name"]
        report, workbook_issues = verify_workbook(check)
        workbook_reports.append(report)
        issues.extend(workbook_issues)

    actual_names = {path.name for path in delivery_dir.iterdir() if path.is_file()}
    if actual_names != expected_names:
        issues.append(
            f"delivery file mismatch: missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    subdirs = [path.name for path in delivery_dir.iterdir() if path.is_dir()]
    if subdirs:
        issues.append(f"delivery contains subdirectories: {sorted(subdirs)}")

    zip_report: dict[str, Any] | None = None
    if zip_path is not None:
        zip_issues: list[str] = []
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
                corrupt = archive.testzip()
                nested = [name for name in names if name.lower().endswith(".zip")]
                unsafe = [name for name in names if unsafe_zip_name(name)]
                roots = sorted({PurePosixPath(name).parts[0] for name in names})
                expected_entries = {f"{delivery_dir.name}/{name}" for name in expected_names}
                if corrupt:
                    zip_issues.append(f"ZIP CRC failure at {corrupt}")
                if nested:
                    zip_issues.append(f"nested ZIP entries: {sorted(nested)}")
                if unsafe:
                    zip_issues.append(f"unsafe ZIP entries: {sorted(unsafe)}")
                if roots != [delivery_dir.name]:
                    zip_issues.append(f"unexpected ZIP roots: {roots}")
                if set(names) != expected_entries:
                    zip_issues.append("ZIP file set differs from delivery directory")
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
        "workflow": "primary conditional correction; auditors one decision per row",
        "primary_rows": 498,
        "primary_microtext_rows": 369,
        "primary_visualdiff_rows": 129,
        "primary_visualdiff_descriptions_prefilled": 48,
        "wave39_rows_included": 95,
        "auditors": 10,
        "rows_per_auditor": 12,
        "unique_double_review_rows": 120,
        "workbook_count": len(workbook_reports),
        "embedded_images": sum(report.get("embedded_images", 0) for report in workbook_reports),
        "flat_files": len(expected_names),
        "nested_zip_files": 0,
        "gold_rows_modified": 0,
        "workbooks": workbook_reports,
        "zip": zip_report,
        "issues": issues,
        "valid": not issues,
    }


def build_delivery(
    source_dir: Path,
    workbook_dir: Path,
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    workbook_dir = workbook_dir.resolve()
    delivery_dir = delivery_dir.resolve()
    zip_path = zip_path.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    if not workbook_dir.is_dir():
        raise FileNotFoundError(workbook_dir)
    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)
    for name, content in instruction_files().items():
        (delivery_dir / name).write_text(content, encoding="utf-8")
    for item in assignments(source_dir, workbook_dir):
        source = Path(item["workbook"])
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, delivery_dir / item["name"])

    preliminary = verify_delivery(source_dir, workbook_dir, delivery_dir)
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
    return verify_delivery(source_dir, workbook_dir, delivery_dir, zip_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--workbook-dir", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_delivery(
        args.source_dir,
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
