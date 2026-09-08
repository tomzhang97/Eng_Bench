#!/usr/bin/env python3
"""Build the single-guide, one-workbook-per-person human-audit ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

try:
    from . import build_ultrafast_human_audit_delivery as ultrafast
except ImportError:  # Direct script execution from tools/.
    import build_ultrafast_human_audit_delivery as ultrafast


GUIDE_NAME = "00_READ_ME_FIRST_CN.txt"
PRIMARY_WORKBOOK = "PRIMARY_REVIEW_498.xlsx"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workbook_names() -> set[str]:
    return {PRIMARY_WORKBOOK} | {
        f"AUDITOR_{number:02d}_REVIEW_12.xlsx" for number in range(1, 11)
    }


def expected_names() -> set[str]:
    return {GUIDE_NAME} | workbook_names()


def guide_text(lean_primary: bool = False) -> str:
    assignments = "\n".join(
        f"- 复核员 {number:02d}：AUDITOR_{number:02d}_REVIEW_12.xlsx"
        for number in range(1, 11)
    )
    primary_workflow = (
        "从第 7 行开始，每行只看 B 列图片和 C 列机器内容，在黄色 D 列输入一个数字后按 Enter："
        if lean_primary
        else "从第 7 行开始，看 B 列图片和 D 列机器内容，在黄色 E 列输入一个数字后按 Enter："
    )
    category_examples = """类别速查（按文字在图中的作用判断，不要只看字符串）：
- 尺寸值：11.8°、R10、3'-3\"、0.7500。
- 设备标签：MOTOR、PUMP、BOILER NO. 1。
- 引脚/端子/元件标签：L1、R10、Q3、GP14、3V3。
- 管线标签：DRAIN、OIL TANK VENT、PG05001-6\"。
- 房间/区域标签：DECK、KITCHEN、HALL、PLATFORM。
- 易混边界：R10 在尺寸线/圆弧旁可为尺寸值，在电气元件旁可为元件标签；DECK 是房间/区域标签，不是设备标签。

VisualDiff 文字变化规则：尺寸、标签、房间名或工程注释的含义改变，算真实工程变化；只换字体、位置、清晰度或渲染效果，不算工程变化。
"""
    return f"""Eng_Bench Gold v2.0 Global - 本轮人工审核唯一交付说明

30 秒最短版（负责人只看这里就能发任务）：
1. 完整解压 ZIP。
2. 给主审实习生只发 {PRIMARY_WORKBOOK}。
3. 给复核员 NN 只发 AUDITOR_NN_REVIEW_12.xlsx；10 人的文件不要串号。
4. 每个人只填一个黄色答案列：主审正常每行只按 1/2/3/4 + Enter，复核员只按 1/2/3 + Enter。
5. 只有主审选 2 时才填右侧修正栏；其他情况不写备注。
6. 收回原文件名的 11 个 XLSX。不收 CSV、PDF、截图或重做表格。

工作簿本身已包含图片、简短规则、进度和保存提示。审核员不需要阅读项目文档，也不需要打开其他文件。

负责人只做三件事：
1. 完整解压本 ZIP。不要在微信、邮箱或网盘预览中编辑。
2. 每个人只发送一个与其身份对应的 Excel，不要把整个 ZIP 发给审核员：
- 主审实习生：{PRIMARY_WORKBOOK}
{assignments}
3. 收回文件名不变的 11 个 XLSX。不要收 CSV、PDF、截图或重做的表格。

本轮范围：
- 主审 498 条：369 条 MicroText + 129 条 VisualDiff。
- 10 位复核员各 12 条，共 120 条互不重复的独立复核。
- 共 618 次人工判断；618 张证据图已嵌入 Excel，不需要图片文件夹。
- 498 条主审任务已包含 Wave39 DOE 的 95 条。不要再发送 Wave39 或其他旧 ZIP。

{category_examples}
给主审实习生的聊天文字，可直接复制：
---
请用桌面版 Excel/WPS 打开 PRIMARY_REVIEW_498.xlsx。图片和规则都在表内。
{primary_workflow}
1=全对；2=样本有效但机器内容要修改；3=样本无效或无真实变化；4=证据不足、看不清。
只有选 2 才填写右侧修正栏。截图缺字、删除线划掉、VisualDiff 两图相同、仅整体轻微偏移/渲染差异或变化在框外，都选 3。不要猜，不要排序或删除行。进度到 498/498 后按 Ctrl+S，不改文件名，只交回这个 XLSX。
---

给每位复核员的聊天文字，可直接复制：
---
请用桌面版 Excel/WPS 打开附件。你只独立复核 12 条。
每行只看 B 列图片和 C 列问题，在黄色 D 列输入一个数字后按 Enter：1=是，2=否，3=看不清。
文字题只判断图片是否完整且机器文字/类别都正确；差异题只判断 OLD/NEW 区域内是否有真实工程变化。两图相同、轻微整体偏移、仅渲染差异或区域外变化都填 2。无需改文字或写备注。完成 12/12 后按 Ctrl+S，不改文件名，只交回这个 XLSX。
---

最省时的操作：正常行只按一个数字键和 Enter，不点下拉。每个工作簿只有一个可见审核页，黄色列是唯一主要输入区。

重要：人工结果返回后仍需机器侧门禁检查；任何未复核结果都不能直接写入 gold。
"""


def write_zip(delivery_dir: Path, zip_path: Path, overwrite: bool) -> None:
    if zip_path.exists():
        if not overwrite:
            raise FileExistsError(zip_path)
        zip_path.unlink()
    temporary = zip_path.with_name(f".{zip_path.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=False,
        ) as archive:
            for path in sorted(delivery_dir.iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
        temporary.replace(zip_path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_delivery(
    delivery_dir: Path,
    payload: dict[str, Any],
    zip_path: Path | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    guide = delivery_dir / GUIDE_NAME
    if not guide.is_file():
        issues.append(f"missing {GUIDE_NAME}")
    else:
        text = guide.read_text(encoding="utf-8-sig")
        for term in (
            "Gold v2.0 Global",
            "每个人只发送一个",
            "498 条",
            "120 条",
            "Wave39",
            "618 张证据图已嵌入 Excel",
            "尺寸值：11.8°",
            "DECK 是房间/区域标签",
            "VisualDiff 文字变化规则",
            "1=全对",
            "1=是，2=否，3=看不清",
            "不能直接写入 gold",
        ):
            if term not in text:
                issues.append(f"{GUIDE_NAME}: missing {term!r}")

    reports: list[dict[str, Any]] = []
    primary_report, workbook_issues = ultrafast.validate_workbook(
        delivery_dir / payload["primary"]["workbook"],
        "primary",
        payload["primary"]["rows"],
    )
    reports.append(primary_report)
    issues.extend(workbook_issues)

    auditor_indexes: list[str] = []
    for auditor in payload["auditors"]:
        report, workbook_issues = ultrafast.validate_workbook(
            delivery_dir / auditor["workbook"], "auditor", auditor["rows"]
        )
        reports.append(report)
        issues.extend(workbook_issues)
        auditor_indexes.extend(str(row["primary_index"]) for row in auditor["rows"])
    if len(auditor_indexes) != 120 or len(set(auditor_indexes)) != 120:
        issues.append("auditor assignments are not 120 unique primary rows")

    actual_names = {path.name for path in delivery_dir.iterdir() if path.is_file()}
    if actual_names != expected_names():
        issues.append(
            f"delivery file mismatch: missing={sorted(expected_names() - actual_names)}, "
            f"extra={sorted(actual_names - expected_names())}"
        )
    subdirectories = [path.name for path in delivery_dir.iterdir() if path.is_dir()]
    if subdirectories:
        issues.append(f"delivery contains subdirectories: {sorted(subdirectories)}")

    zip_report: dict[str, Any] | None = None
    if zip_path is not None:
        zip_issues: list[str] = []
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
                corrupt = archive.testzip()
                if corrupt:
                    zip_issues.append(f"ZIP CRC failure at {corrupt}")
                if set(names) != expected_names():
                    zip_issues.append("ZIP root file set differs from delivery directory")
                nested = [name for name in names if name.lower().endswith(".zip")]
                if nested:
                    zip_issues.append(f"nested ZIP entries: {nested}")
                unsafe = [name for name in names if ultrafast.unsafe_zip_name(name)]
                if unsafe:
                    zip_issues.append(f"unsafe ZIP entries: {unsafe}")
                with tempfile.TemporaryDirectory() as temporary:
                    archive.extractall(temporary)
                    extracted = Path(temporary)
                    for name in expected_names():
                        original = delivery_dir / name
                        copied = extracted / name
                        if not copied.exists() or sha256(original) != sha256(copied):
                            zip_issues.append(f"clean extraction mismatch: {name}")
        except (OSError, zipfile.BadZipFile) as exc:
            zip_issues.append(f"invalid ZIP: {exc}")
        issues.extend(zip_issues)
        zip_report = {
            "path": zip_path.resolve().as_posix(),
            "size_bytes": zip_path.stat().st_size if zip_path.exists() else 0,
            "sha256": sha256(zip_path) if zip_path.exists() else "",
            "entry_count": len(expected_names()),
            "root_files_only": True,
            "nested_zip_files": 0,
            "issues": zip_issues,
            "valid": not zip_issues,
        }

    return {
        "goal": "Gold v2.0 Global",
        "workflow": "single-guide numeric-key audit with one workbook per person",
        "primary_rows": len(payload["primary"]["rows"]),
        "primary_microtext_rows": sum(
            row.get("task") == "microtext" for row in payload["primary"]["rows"]
        ),
        "primary_visualdiff_rows": sum(
            row.get("task") == "visualdiff" for row in payload["primary"]["rows"]
        ),
        "auditors": len(payload["auditors"]),
        "rows_per_auditor": 12,
        "unique_double_review_rows": len(set(auditor_indexes)),
        "workbook_count": len(reports),
        "embedded_images": sum(report["embedded_images"] for report in reports),
        "delivery_files": len(expected_names()),
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
    payload_json: Path,
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
    lean_primary: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    source_dir = source_dir.resolve()
    payload_json = payload_json.resolve()
    delivery_dir = delivery_dir.resolve()
    zip_path = zip_path.resolve()
    allowed_root = root / "derived" / "human_adjudication"
    ultrafast.ensure_child(delivery_dir, allowed_root)
    ultrafast.ensure_child(zip_path, allowed_root)
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    if not payload_json.is_file():
        raise FileNotFoundError(payload_json)
    payload = json.loads(payload_json.read_text(encoding="utf-8"))

    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)
    (delivery_dir / GUIDE_NAME).write_text(
        guide_text(lean_primary=lean_primary), encoding="utf-8-sig"
    )
    for name in workbook_names():
        source = source_dir / name
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = delivery_dir / name
        shutil.copy2(source, destination)
        ultrafast.set_sheet_state(
            destination, ultrafast.MACHINE_SHEET, "veryHidden"
        )

    preliminary = verify_delivery(delivery_dir, payload)
    if not preliminary["valid"]:
        return preliminary
    write_zip(delivery_dir, zip_path, overwrite)
    return verify_delivery(delivery_dir, payload, zip_path)


def render_markdown(report: dict[str, Any]) -> str:
    zip_report = report.get("zip") or {}
    return "\n".join(
        [
            "# Single-guide Human Audit Delivery Verification",
            "",
            f"- Goal: `{report.get('goal', 'Gold v2.0 Global')}`",
            f"- Valid: `{str(report.get('valid', False)).lower()}`",
            f"- Primary rows: `{report.get('primary_rows', 0)}`",
            f"- Independent checks: `{report.get('unique_double_review_rows', 0)}`",
            f"- Workbooks: `{report.get('workbook_count', 0)}`",
            f"- Embedded images: `{report.get('embedded_images', 0)}`",
            f"- ZIP root files: `{zip_report.get('entry_count', 0)}`",
            f"- Nested ZIP files: `{zip_report.get('nested_zip_files', 0)}`",
            f"- ZIP SHA-256: `{zip_report.get('sha256', '')}`",
            f"- Issues: `{len(report.get('issues', []))}`",
            f"- Gold rows modified: `{report.get('gold_rows_modified', 0)}`",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--payload-json", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--lean-primary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_delivery(
        args.root,
        args.source_dir,
        args.payload_json,
        args.delivery_dir,
        args.zip_path,
        args.overwrite,
        args.lean_primary,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.report_md:
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
