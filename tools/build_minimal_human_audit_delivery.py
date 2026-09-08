#!/usr/bin/env python3
"""Build the flat, copy-ready Eng_Bench human-audit delivery ZIP."""
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


OWNER_NAME = "00_OWNER_ONLY_CN.txt"
PRIMARY_MESSAGE_NAME = "01_COPY_TO_PRIMARY_CN.txt"
AUDITOR_MESSAGE_NAME = "02_COPY_TO_EACH_AUDITOR_CN.txt"
PRIMARY_WORKBOOK = "PRIMARY_REVIEW_498.xlsx"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_names() -> set[str]:
    names = {
        OWNER_NAME,
        PRIMARY_MESSAGE_NAME,
        AUDITOR_MESSAGE_NAME,
        PRIMARY_WORKBOOK,
    }
    names.update(
        f"AUDITOR_{number:02d}_REVIEW_12.xlsx" for number in range(1, 11)
    )
    return names


def workbook_names() -> set[str]:
    return expected_names() - {
        OWNER_NAME,
        PRIMARY_MESSAGE_NAME,
        AUDITOR_MESSAGE_NAME,
    }


def owner_text(universal_primary: bool = False) -> str:
    assignments = "\n".join(
        f"   - 复核员 {number:02d}：AUDITOR_{number:02d}_REVIEW_12.xlsx"
        for number in range(1, 11)
    )
    primary_scheme = (
        "- 主审只有一个审核页；两类任务统一使用：1=全对，2=修改，3=剔除，4=看不清。\n"
        if universal_primary
        else ""
    )
    return f"""Eng_Bench Gold v2.0 Global - 负责人只看这一页

你只做 3 步：
1. 完整解压 ZIP。不要在微信、邮箱或网盘预览中编辑。
2. 每个人只发 1 个 Excel：
   - 主审实习生：{PRIMARY_WORKBOOK}
{assignments}
3. 收回文件名不变的 11 个 XLSX。不要收 CSV、PDF、截图或重新制作的表格。

最省事的发送方法：
- 发给主审：只发 {PRIMARY_WORKBOOK}。表内已经写好完整规则；聊天里可直接复制 {PRIMARY_MESSAGE_NAME} 全文。
- 发给每位复核员：只发其编号对应的 AUDITOR_XX_REVIEW_12.xlsx。表内已经写好完整规则；聊天里可直接复制 {AUDITOR_MESSAGE_NAME} 全文。
- 不需要拆成 10 个 ZIP，不要发图片文件夹，也不要把整个 ZIP 发给审核员。
- 不要再发任何旧批次、旧 ZIP 或旧 Excel，避免重复审核。

范围：
- 主审 498 条：369 条 MicroText + 129 条 VisualDiff。
- 10 位复核员各 12 条，共 120 条独立复核。
- 总操作数 618；618 张证据图全部嵌入 Excel。
- 主审 498 条已经包含 Wave39 DOE 的 95 条，不要再发旧 Wave39 ZIP。
{primary_scheme}

重要：本包没有子 ZIP、图片文件夹或外部图片依赖。每个人打开自己的 Excel 就能开始。人工结果返回后仍需机器侧门禁检查；不要直接写入 gold。
"""


def primary_message_text(universal_primary: bool = False) -> str:
    if universal_primary:
        return f"""请完成附件 {PRIMARY_WORKBOOK}，共 498 条：MicroText（文字题）和 VisualDiff（差异题）。图片都在 Excel 里，不需要其他文件。

最快做法：每行通常只按一次数字键。
1. 用桌面版 Excel/WPS 打开文件；它会直接进入唯一的“审核498条”页。
2. 从第 7 行开始：看 B 列图片和 D 列问题/机器内容。
3. 在黄色 E 列输入 1、2、3 或 4，再按 Enter，Excel 会移到下一行。
4. 只有输入 2 时，才按 Tab 到右侧填写修正；文字题类别也错时再填 G 列。其他情况不要打字。
5. 顶部进度到 498/498 并显示“可以交回”后，按 Ctrl+S，只交回原文件名 XLSX。

两类任务永远使用同一套数字：
- 1 = 全对：样本有效，而且 D 列机器文字、类别或变化描述完全正确。
- 2 = 修改：样本有效，但 D 列机器内容有错；只填写错误字段的正确内容。
- 3 = 剔除：文字题缺字、模糊、无效或被删除线划掉；差异题两图相同、仅整体轻微偏移/渲染差异、变化在框外或没有真实工程变化。
- 4 = 看不清：证据不足，必须看更大范围或整页。不要猜。

三个易错点：
- D 列显示 unknown、空文字或空类别：图片若是有效工程标签，选 2 并填写正确文字/类别；图片本身无效才选 3。
- 截图只看到 B-9A，但机器写 MB-9A：截图缺字母，选 3，不算全对。
- 标签被明确删除线划掉：选 3；普通引线、边框或下划线不算删除线。

不要点下拉，不要删除、新增或排序行，不要修改灰色机器内容，不要另存 CSV。正常行只在黄色 E 列输入数字 + Enter。
"""
    return f"""请完成附件 {PRIMARY_WORKBOOK}，共 498 条。图片已全部放在 Excel 里，不需要找任何外部文件。

最快操作：
1. 用桌面版 Excel 或 WPS 打开文件，先看“开始”页。
2. 进入 MicroText，从黄色 D2 开始。每行看图片和机器建议，输入 1 个数字，再按 Enter。
3. MicroText 完成后进入 VisualDiff，同样只在黄色 D 列输入数字 + Enter。
4. “开始”页显示 498/498 且“可以交回”后，按 Ctrl+S。文件名不要改，只交回这个 XLSX。

MicroText 数字：
- 1 = 通过：截图完整，机器文字和类别都正确。通常正常行只按 1。
- 2 = 修改：截图有效，但文字或类别有错。只填写错误的那一项；正确项留空。
- 3 = 剔除：缺字、只有半个字、明显模糊、不是有效工程标签，或文字被删除线划掉。
- 4 = 看不清：仅凭裁剪无法判断，必须看更大范围或整页。不要猜。

VisualDiff 数字：
- 1 = 有真实变化：只看 OLD/NEW 对比区域内的真实工程变化；E 列描述必须准确，错了才修改。
- 2 = 无有效变化：两图相同、仅轻微整体偏移、仅对齐/渲染差异、变化在区域外，或不是工程变化。
- 3 = 看不清：证据不足，无法可靠判断。不要猜。

两个容易出错的例子：
- 截图只看到 B-9A，但机器写 MB-9A：截图缺字母，选 3，不算通过。
- 标签被删除线划掉：选 3，不保留。

不要删除、新增或排序行；不要修改灰色机器数据；不要另存 CSV。只在黄色输入格工作。
"""


def auditor_message_text() -> str:
    return """请用桌面版 Excel/WPS 打开附件。你只独立复核 12 条，图片和全部规则都在表里。

最快做法：每行只看 B 列图片和 C 列问题，在黄色 D 列输入 1、2 或 3，再按 Enter。不要点下拉。
- 1 = 是
- 2 = 否
- 3 = 看不清，不要猜

只回答 C 列写出的那个问题：
- 【文字】图片是否完整，而且机器文字和类别是否都正确？
- 【差异】OLD/NEW 对比区域内是否有真实工程变化？两图相同、轻微整体偏移、仅渲染差异或区域外变化都填 2。

你不需要改文字、改类别、检查变化描述或写备注，也不要参考其他人的答案。总共只需填写 12 个数字。进度到 12 / 12 后按 Ctrl+S，不改文件名，只交回这个 XLSX。
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
    reports: list[dict[str, Any]] = []
    instruction_requirements = {
        OWNER_NAME: ["只做 3 步", "498", "120", "Wave39", "不要直接写入 gold"],
        PRIMARY_MESSAGE_NAME: ["数字", "MicroText", "VisualDiff", "498/498", "Ctrl+S"],
        AUDITOR_MESSAGE_NAME: ["12 条", "1 = 是", "2 = 否", "3 = 看不清", "独立复核"],
    }
    for name, terms in instruction_requirements.items():
        path = delivery_dir / name
        if not path.is_file():
            issues.append(f"missing {name}")
            continue
        content = path.read_text(encoding="utf-8-sig")
        for term in terms:
            if term not in content:
                issues.append(f"{name}: missing {term!r}")

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
        "workflow": "copy-ready numeric-key audit with one workbook per person",
        "primary_rows": 498,
        "primary_microtext_rows": 369,
        "primary_visualdiff_rows": 129,
        "auditors": 10,
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
    universal_primary: bool = False,
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
    (delivery_dir / OWNER_NAME).write_text(
        owner_text(universal_primary), encoding="utf-8-sig"
    )
    (delivery_dir / PRIMARY_MESSAGE_NAME).write_text(
        primary_message_text(universal_primary), encoding="utf-8-sig"
    )
    (delivery_dir / AUDITOR_MESSAGE_NAME).write_text(
        auditor_message_text(), encoding="utf-8-sig"
    )
    for name in workbook_names():
        source = source_dir / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, delivery_dir / name)

    preliminary = verify_delivery(delivery_dir, payload)
    if not preliminary["valid"]:
        return preliminary
    write_zip(delivery_dir, zip_path, overwrite)
    return verify_delivery(delivery_dir, payload, zip_path)


def render_markdown(report: dict[str, Any]) -> str:
    zip_report = report.get("zip") or {}
    return "\n".join(
        [
            "# Minimal Human Audit Delivery Verification",
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
    parser.add_argument("--universal-primary", action="store_true")
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
        args.universal_primary,
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
