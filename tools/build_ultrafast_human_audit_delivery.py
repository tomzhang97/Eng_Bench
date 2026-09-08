#!/usr/bin/env python3
"""Build and verify the flat ultrafast Eng_Bench human-audit delivery ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

try:
    from . import verify_multi_reviewer_handoff as workbook_io
    from . import verify_simple_multi_reviewer_handoff as simple_io
except ImportError:  # Direct script execution from tools/.
    import verify_multi_reviewer_handoff as workbook_io
    import verify_simple_multi_reviewer_handoff as simple_io


README_NAME = "00_READ_ME_FIRST_CN.txt"
MACHINE_SHEET = "\u673a\u5668\u6570\u636e_\u52ff\u6539"
AUDIT_SHEET = "\u5ba1\u683812\u6761"
START_SHEET = "\u5f00\u59cb"
SINGLE_PRIMARY_SHEET = "\u5ba1\u6838498\u6761"
SINGLE_PRIMARY_DECISION = "\u6309 1/2/3/4"
SINGLE_PRIMARY_CORRECTION = "\u6b63\u786e\u6587\u5b57 / \u53d8\u5316\u63cf\u8ff0\uff08\u4ec5 2\uff09"
SINGLE_PRIMARY_CATEGORY = "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5\u6587\u5b57\u9898\u4e14 2\uff09"
PRIMARY_MICRO_DECISION = "\u7b54\u6848 1/2/3/4"
PRIMARY_VISUAL_DECISION = "\u7b54\u6848 1/2/3"
PRIMARY_VISUAL_DESCRIPTION = "\u53d8\u5316\u63cf\u8ff0\uff08\u9009 1 \u65f6\u5fc5\u987b\u51c6\u786e\uff09"
SIMPLEST_PRIMARY_DECISION = "\u7b54\u6848\uff1a1\u901a\u8fc7 2\u4fee\u6539 3\u5254\u9664 4\u770b\u4e0d\u6e05"
SIMPLEST_VISUAL_DESCRIPTION = "\u673a\u5668\u53d8\u5316\u63cf\u8ff0\uff08\u4ec5 2 \u65f6\u4fee\u6539\uff09"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_child(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"output path must be below {root}: {resolved}")


def expected_names() -> set[str]:
    names = {README_NAME, "PRIMARY_REVIEW_498.xlsx"}
    names.update(f"AUDITOR_{number:02d}_REVIEW_12.xlsx" for number in range(1, 11))
    return names


def unsafe_zip_name(name: str) -> bool:
    value = PurePosixPath(name)
    return value.is_absolute() or ".." in value.parts or bool(value.drive)


def owner_guide() -> str:
    auditor_files = "\n".join(
        f"- \u590d\u6838\u5458 {number:02d}\uff1aAUDITOR_{number:02d}_REVIEW_12.xlsx"
        for number in range(1, 11)
    )
    return f"""Eng_Bench Gold v2.0 Global - 数字键极速人工审核包（当前推荐版）

负责人只做 3 步：
1. 完整解压本 ZIP。不要在微信、邮箱或网盘预览中编辑。
2. 每个人只发 1 个 Excel，不要把整个 ZIP 发给任何审核员：
   - 主审实习生：PRIMARY_REVIEW_498.xlsx（369 条 MicroText + 129 条 VisualDiff）
{auditor_files}
3. 收回文件名不变的 11 个 XLSX。不要收 CSV、PDF、截图，也不要让审核员重新制表。

可直接发给主审实习生：
“请用 Excel/WPS 打开 PRIMARY_REVIEW_498.xlsx，先看‘开始’页。审核时只在黄色 D 列输入数字再按 Enter，不用切换中英文输入法，也不用点下拉。MicroText：1=通过，2=修改，3=剔除，4=需要完整页；VisualDiff：1=有真实变化，2=无有效变化，3=看不清。正常行通常只按 1。只有 MicroText 选 2 时才填写正确文字或正确类别；VisualDiff 选 1 时检查 E 列描述，描述错了才修改。完成数达到 498/498 后按 Ctrl+S，文件名不要改，只交回这个 Excel。”

主审实习生最短操作：
1. 点击 MicroText 页签，从 D2 开始逐行输入 1/2/3/4 + Enter。
2. 点击 VisualDiff 页签，从 D2 开始逐行输入 1/2/3 + Enter。
3. 回到“开始”页，看到“可以交回”后 Ctrl+S。

主审判断规则：
- MicroText 1：图片完整，机器文字和类别都正确。
- MicroText 2：图片有效，但文字或类别错误；只补错误字段。
- MicroText 3：明确缺字、只剩半个字、模糊到不可用、不是有效工程标签、或被删除线划掉。
- MicroText 4：裁剪本身无法判断，需要查看更大范围或完整页面；不要猜。
- VisualDiff 1：OLD/NEW 所示区域内存在真实工程变化；E 列描述必须准确。
- VisualDiff 2：完全相同、只有轻微整体偏移、仅对齐/渲染差异、变化在区域外、或不是工程变化。
- VisualDiff 3：证据不足，无法可靠判断。

可直接发给每位复核员：
“请用 Excel/WPS 打开附件。你只有 12 条，文件打开后黄色 D7 已选中。每行只读 C 列问题，然后输入一个数字并按 Enter：1=是，2=否，3=看不清。不要修改文字或类别，不要写备注，也不要参考别人的答案。进度达到 12/12 后按 Ctrl+S，文件名不要改，只交回这个 XLSX。”

复核员只需记住：
- 文字题：图片完整，而且机器文字和类别都正确吗？是=1，否=2，看不清=3。
- 差异题：OLD/NEW 所示区域内有真实工程变化吗？是=1，否=2，看不清=3。
- 完全相同、轻微整体偏移、仅渲染差异或区域外变化，差异题都填 2。

范围核对：主审 498 条已包含 Wave39 DOE 的 95 条，不需要旧 Wave39 ZIP。10 位复核员共独立复核 120 条，每人 12 条。

本 ZIP 只有 1 份说明和 11 个 Excel，没有子 ZIP、图片文件夹或外部依赖。618 张证据图全部嵌入 Excel。人工结果不会自动写入 gold，返回后仍由机器侧安全门检查。
"""


def _legacy_one_sheet_owner_guide() -> str:
    return """Eng_Bench Gold v2.0 Global - 唯一当前人工审核包（数字键极速版）

只使用这个包。不要再发任何旧 ZIP，包括 Wave39 DOE；Wave39 的 95 条已在主审表中。

30 秒发包方法：
1. 解压 ZIP。
2. 主审只发 PRIMARY_REVIEW_498.xlsx；复核员 NN 只发同编号的 AUDITOR_NN_REVIEW_12.xlsx。
3. 收回原文件名不变的 11 个 XLSX。

负责人只做 3 步：
1. 完整解压 ZIP，不要在微信、邮箱或网盘预览中编辑。
2. 每个人只发 1 个 Excel，不要把整个 ZIP 发给任何审核员：
   - 主审实习生：PRIMARY_REVIEW_498.xlsx
   - 复核员 01-10：编号 NN 只收 AUDITOR_NN_REVIEW_12.xlsx。
3. 收回文件名不变的 11 个 XLSX；不收 CSV、PDF、截图或重新制作的表格。

可直接发给主审实习生：
“用桌面版 Excel/WPS 打开 PRIMARY_REVIEW_498.xlsx。文件已停在第一个答案格。每行只看 B 列图片和 D 列机器内容，在黄色 E 列按 1/2/3/4 + Enter。正常行通常只按 1；只有选 2 时才填写自动变黄的修改格，灰色格不用填。顶部显示 498 / 498 和‘可以交回’后，按 Ctrl+S，不改文件名，只交回这个 XLSX。”

主审只记 4 个数字：
- 1 = 全对：样本有效，机器文字/类别或变化描述完全正确。
- 2 = 修改：样本有效，但机器内容有错；只填写错误字段的正确内容。
- 3 = 剔除：MicroText 缺字、模糊、无效或被删除线划掉；VisualDiff 两图相同、仅轻微整体偏移/渲染差异、变化在框外或没有真实工程变化。
- 4 = 看不清：证据不足，必须看更大范围才能判断；不要猜。
- VisualDiff 行若写着“机器没有描述”：有真实变化选 2 并在 F 列写一句；没有真实变化选 3。

可直接发给每位复核员：
“用桌面版 Excel/WPS 打开附件。文件已停在第一个答案格，你只有 12 条。每行看 B 列图片和 C 列问题，在黄色 D 列按 1/2/3 + Enter：1=是，2=否，3=看不清。不改其他内容，不写备注，不参考别人答案。进度到 12 / 12 后按 Ctrl+S，不改文件名，只交回这个 XLSX。”

范围核对：主审 498 条（369 MicroText + 129 VisualDiff）；10 位复核员共独立复核 120 条，每人 12 条，彼此不重复。

代码核对：主审使用 1/2/3/4，复核员使用 1/2/3。

省时设计：每个审核员只打开一个 Excel；所有证据图都在表内；只有一个主要黄色答案列；主审仅在答案 2 时补修正；复核员从不改文字或写备注。

本 ZIP 只有 1 份说明 + 11 个 Excel，没有子 ZIP、图片文件夹或外部依赖。618 张证据图已嵌入 Excel。人工结果不会自动写入 gold，返回后仍由机器侧安全门检查。
"""


def one_sheet_owner_guide() -> str:
    return """Eng_Bench Gold v2.0 Global - 唯一当前人工审核包（一人一表版）

只使用本 ZIP，不要再发送 Wave39 DOE 或其他旧 ZIP。Wave39 DOE 的 95 条已包含在主审 498 条中。

负责人只做 3 步（不要把整个 ZIP 发给审核员）：
1. 完整解压 ZIP。
2. 主审实习生只发 PRIMARY_REVIEW_498.xlsx；复核员 NN 只发 AUDITOR_NN_REVIEW_12.xlsx。
3. 收回原文件名不变的 11 个 XLSX。不收 CSV、PDF、截图或重做表格。

可直接发给主审实习生：
“请用桌面版 Excel/WPS 打开 PRIMARY_REVIEW_498.xlsx。从黄色 D7 开始，每行只看 B 列图片和 C 列问题，输入一个数字后按 Enter：1=全对，2=需修改，3=剔除/无真实变化，4=看不清。只有选 2 时才填 E/F 修正栏。进度到 498/498 且交回检查显示‘可以交回’后，按 Ctrl+S；不要改文件名，只交回这个 XLSX。”

主审只记 4 个数字：
- 1 = 全对：图片有效，文字/类别或差异描述完全正确。
- 2 = 修改：样本有效，但机器内容有错；只填写错误字段的正确内容。
- 3 = 剔除：MicroText 截图缺字、模糊、无效或被删除线划掉；VisualDiff 两图相同、仅轻微整体偏移/渲染差异、变化在框外或没有真实工程变化。
- 4 = 看不清：证据不足，必须看更大范围才能判断；不要猜，也不要用 3 代替。

可直接发给每位复核员：
“请用桌面版 Excel/WPS 打开附件。你只有 12 条，从黄色 D7 开始。每行只看 B 列图片和 C 列问题，在 D 列输入 1/2/3 后按 Enter：1=是，2=否，3=看不清。不要改其他格，不参考别人答案。进度到 12/12 后按 Ctrl+S；不要改文件名，只交回这个 XLSX。”

复核员不需要懂项目：
- 文字题：图片完整，且问题中的文字和类别都正确才选 1；否则选 2。
- 差异题：OLD/NEW 框内有真实工程变化才选 1。完全相同、轻微整体偏移、仅渲染差异或框外变化都选 2。

本轮范围：主审 498 条（369 MicroText + 129 VisualDiff）；10 位复核员各 12 条，共 120 个互不重复的独立复核样本。共 618 次人工判断。
快捷键核对：主审使用 1/2/3/4；复核员使用 1/2/3。

每个人只打开 1 个 Excel，所有证据图都已嵌入表内。本 ZIP 没有子 ZIP、没有图片文件夹、没有外部依赖。人工结果返回后仍必须通过机器门禁，不会自动写入 gold。
"""


def sheet_states(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path, "r") as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = root.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        return {}
    return {
        sheet.get("name", ""): sheet.get("state", "visible")
        for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet")
    }


def set_sheet_state(path: Path, sheet_name: str, state: str) -> None:
    """Rewrite one sheet tag while preserving Excel's namespace declarations."""
    if sheet_states(path).get(sheet_name) == state:
        return
    temporary = path.with_name(f".{path.name}.sheet-state.tmp")
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            temporary, "w"
        ) as target:
            found = False
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == "xl/workbook.xml":
                    root = ET.fromstring(data)
                    sheets = root.find(f"{{{MAIN_NS}}}sheets")
                    if sheets is None:
                        raise ValueError(f"{path}: workbook has no sheets collection")
                    sheet_id = ""
                    for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet"):
                        if sheet.get("name") == sheet_name:
                            sheet_id = sheet.get("sheetId", "")
                            found = True
                    if not sheet_id:
                        raise KeyError(f"{path}: missing sheet {sheet_name}")

                    has_bom = data.startswith(b"\xef\xbb\xbf")
                    text = data.decode("utf-8-sig")
                    sheet_pattern = re.compile(
                        rf"(<(?:[A-Za-z_][\w.-]*:)?sheet\b"
                        rf"(?=[^>]*\bsheetId=(['\"]){re.escape(sheet_id)}\2)"
                        rf"[^>]*?)(\s*/?>)"
                    )
                    matches = list(sheet_pattern.finditer(text))
                    if len(matches) != 1:
                        raise ValueError(
                            f"{path}: expected one raw sheet tag for sheetId {sheet_id}, "
                            f"found {len(matches)}"
                        )

                    def update_tag(match: re.Match[str]) -> str:
                        prefix, ending = match.group(1), match.group(3)
                        prefix = re.sub(
                            r"\s+state=(['\"])[^'\"]*\1", "", prefix, count=1
                        )
                        if state != "visible":
                            prefix += f' state="{state}"'
                        return prefix + ending

                    text = sheet_pattern.sub(update_tag, text, count=1)
                    data = (("\ufeff" if has_bom else "") + text).encode("utf-8")
                    updated_root = ET.fromstring(data)
                    updated_sheets = updated_root.find(f"{{{MAIN_NS}}}sheets")
                    updated_state = ""
                    if updated_sheets is not None:
                        for sheet in updated_sheets.findall(f"{{{MAIN_NS}}}sheet"):
                            if sheet.get("name") == sheet_name:
                                updated_state = sheet.get("state", "visible")
                    if updated_state != state:
                        raise ValueError(
                            f"{path}: failed to set {sheet_name} state to {state}"
                        )
                target.writestr(entry, data)
        if not found:
            raise KeyError(f"{path}: missing sheet {sheet_name}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def matrix_records(path: Path, sheet_name: str, header_name: str) -> tuple[list[dict[str, str]], int, bool]:
    rows, formulas, validation = workbook_io.read_xlsx_sheet(path, sheet_name)
    header_index = next(
        (index for index, values in enumerate(rows) if header_name in values),
        -1,
    )
    if header_index < 0:
        return [], formulas, validation
    headers = rows[header_index]
    records: list[dict[str, str]] = []
    for values in rows[header_index + 1 :]:
        padded = values + [""] * max(0, len(headers) - len(values))
        record = {
            str(header): str(padded[index])
            for index, header in enumerate(headers)
            if str(header).strip()
        }
        if any(value.strip() for value in record.values()):
            records.append(record)
    return records, formulas, validation


def picture_anchor_groups(path: Path) -> tuple[list[list[dict[str, int]]], list[str]]:
    groups: list[list[dict[str, int]]] = []
    issues: list[str] = []
    with zipfile.ZipFile(path, "r") as archive:
        drawing_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml")
        )
        for drawing_name in drawing_names:
            root = ET.fromstring(archive.read(drawing_name))
            anchors: list[dict[str, int]] = []
            for index, anchor in enumerate(list(root), start=1):
                if anchor.find(f"{{{DRAWING_NS}}}pic") is None:
                    continue
                start = anchor.find(f"{{{DRAWING_NS}}}from")
                if start is None:
                    issues.append(f"{drawing_name} anchor {index}: missing start marker")
                    continue

                def marker_value(name: str) -> int:
                    node = start.find(f"{{{DRAWING_NS}}}{name}")
                    return int(node.text or "0") if node is not None else -1

                anchor_type = anchor.tag.rsplit("}", 1)[-1]
                record = {
                    "row": marker_value("row"),
                    "col": marker_value("col"),
                    "row_offset": marker_value("rowOff"),
                    "col_offset": marker_value("colOff"),
                    "type": 1 if anchor_type == "oneCellAnchor" else 2,
                }
                if record["row"] < 0 or record["col"] < 0:
                    issues.append(f"{drawing_name} anchor {index}: invalid start marker")
                if record["col"] != 1:
                    issues.append(f"{drawing_name} anchor {index}: picture leaves evidence column B")
                if record["row_offset"] < 0 or record["col_offset"] < 0:
                    issues.append(f"{drawing_name} anchor {index}: negative image offset")
                anchors.append(record)
            groups.append(anchors)
    return groups, issues


def validate_picture_layout(
    path: Path,
    role: str,
    single_sheet_primary: bool = False,
    expected_auditor_rows: int = 12,
) -> tuple[int, list[str]]:
    groups, issues = picture_anchor_groups(path)
    if role == "primary":
        expected_specs = [(498, 6)] if single_sheet_primary else [(369, 1), (129, 1)]
    else:
        expected_specs = [(expected_auditor_rows, 6)]
    actual_specs = sorted((len(group), min((row["row"] for row in group), default=-1)) for group in groups)
    if actual_specs != sorted(expected_specs):
        issues.append(f"picture groups {actual_specs} do not match expected {sorted(expected_specs)}")
    for group in groups:
        rows = [anchor["row"] for anchor in group]
        if len(rows) != len(set(rows)):
            issues.append("picture group contains duplicate start rows")
        if rows:
            expected_rows = list(range(min(rows), min(rows) + len(rows)))
            if sorted(rows) != expected_rows:
                issues.append(
                    f"picture group rows are not contiguous: {min(rows)}..{max(rows)}"
                )
    return sum(len(group) for group in groups), issues


def expected_machine_rows(payload_rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    return [
        (
            str(row["primary_index"]),
            str(row["task"]),
            str(row.get("candidate_id") or row.get("pair_id") or ""),
            str(row["display_index"]),
        )
        for row in payload_rows
    ]


def actual_machine_rows(path: Path) -> list[tuple[str, str, str, str]]:
    records, _ = workbook_io.table_records(path, MACHINE_SHEET)
    return [
        (
            row.get("primary_index", ""),
            row.get("task", ""),
            row.get("record_id", ""),
            row.get("display_index", ""),
        )
        for row in records
    ]


def validate_workbook(
    path: Path,
    role: str,
    payload_rows: list[dict[str, Any]],
    auditor_sheet_name: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                issues.append(f"{path.name}: XLSX CRC failure at {corrupt}")
        states = sheet_states(path)
        if states.get(MACHINE_SHEET) != "veryHidden":
            issues.append(f"{path.name}: machine sheet is not veryHidden")
        if actual_machine_rows(path) != expected_machine_rows(payload_rows):
            issues.append(f"{path.name}: machine identities/order differ from payload")

        single_sheet_primary = role == "primary" and SINGLE_PRIMARY_SHEET in states
        if single_sheet_primary:
            expected_states = {
                SINGLE_PRIMARY_SHEET: "visible",
                MACHINE_SHEET: "veryHidden",
            }
            if states != expected_states:
                issues.append(f"{path.name}: sheet layout/state mismatch: {states}")
            records, formulas, validation = matrix_records(
                path, SINGLE_PRIMARY_SHEET, SINGLE_PRIMARY_DECISION
            )
            records = records[:498]
            if len(records) != 498:
                issues.append(
                    f"{path.name}: expected 498 primary rows, found {len(records)}"
                )
            if any(row.get(SINGLE_PRIMARY_DECISION, "").strip() for row in records):
                issues.append(f"{path.name}: primary answers are prefilled")
            if formulas < 500:
                issues.append(f"{path.name}: progress/completion formulas are missing")
            if not validation:
                issues.append(f"{path.name}: decision dropdown is missing")
            primary_choice_columns = {
                column: simple_io.validation_values(path, SINGLE_PRIMARY_SHEET, column)
                for column in ("D", "E")
            }
            if {"1", "2", "3", "4"} not in primary_choice_columns.values():
                issues.append(f"{path.name}: primary decision choices mismatch")
            rows = len(records)
            visible_sheets = 1
        elif role == "primary":
            expected_states = {
                START_SHEET: "visible",
                "MicroText": "visible",
                "VisualDiff": "visible",
                MACHINE_SHEET: "veryHidden",
            }
            if states != expected_states:
                issues.append(f"{path.name}: sheet layout/state mismatch: {states}")
            micro, micro_meta = workbook_io.table_records(path, "MicroText")
            visual, visual_meta = workbook_io.table_records(path, "VisualDiff")
            if len(micro) != 369 or len(visual) != 129:
                issues.append(
                    f"{path.name}: expected 369/129 rows, found {len(micro)}/{len(visual)}"
                )
            micro_decision = (
                SIMPLEST_PRIMARY_DECISION
                if micro and SIMPLEST_PRIMARY_DECISION in micro[0]
                else PRIMARY_MICRO_DECISION
            )
            visual_decision = (
                SIMPLEST_PRIMARY_DECISION
                if visual and SIMPLEST_PRIMARY_DECISION in visual[0]
                else PRIMARY_VISUAL_DECISION
            )
            visual_description = (
                SIMPLEST_VISUAL_DESCRIPTION
                if visual and SIMPLEST_VISUAL_DESCRIPTION in visual[0]
                else PRIMARY_VISUAL_DESCRIPTION
            )
            simplest = visual_decision == SIMPLEST_PRIMARY_DECISION
            if any(row.get(micro_decision, "").strip() for row in micro):
                issues.append(f"{path.name}: MicroText answers are prefilled")
            if any(row.get(visual_decision, "").strip() for row in visual):
                issues.append(f"{path.name}: VisualDiff answers are prefilled")
            if micro_meta["formula_count"] < 369 or visual_meta["formula_count"] < 129:
                issues.append(f"{path.name}: completion formulas are missing")
            if not micro_meta["data_validation"] or not visual_meta["data_validation"]:
                issues.append(f"{path.name}: decision dropdowns are missing")
            if simple_io.validation_values(path, "MicroText", "D") != {"1", "2", "3", "4"}:
                issues.append(f"{path.name}: MicroText decision choices mismatch")
            expected_visual_choices = {"1", "2", "3", "4"} if simplest else {"1", "2", "3"}
            if simple_io.validation_values(path, "VisualDiff", "D") != expected_visual_choices:
                issues.append(f"{path.name}: VisualDiff decision choices mismatch")
            descriptions = sum(
                bool(row.get(visual_description, "").strip())
                for row in visual
            )
            if descriptions != 48:
                issues.append(f"{path.name}: expected 48 prefilled descriptions, found {descriptions}")
            rows = len(micro) + len(visual)
            formulas = micro_meta["formula_count"] + visual_meta["formula_count"]
            visible_sheets = 3
        else:
            expected_auditor_rows = len(payload_rows)
            sheet_name = auditor_sheet_name or AUDIT_SHEET
            expected_states = {sheet_name: "visible", MACHINE_SHEET: "veryHidden"}
            if states != expected_states:
                issues.append(f"{path.name}: sheet layout/state mismatch: {states}")
            records, formulas, validation = matrix_records(
                path, sheet_name, PRIMARY_VISUAL_DECISION
            )
            records = records[:expected_auditor_rows]
            if len(records) != expected_auditor_rows:
                issues.append(
                    f"{path.name}: expected {expected_auditor_rows} audit rows, "
                    f"found {len(records)}"
                )
            if any(row.get(PRIMARY_VISUAL_DECISION, "").strip() for row in records):
                issues.append(f"{path.name}: answers are prefilled")
            if formulas < expected_auditor_rows + 1:
                issues.append(
                    f"{path.name}: expected progress plus "
                    f"{expected_auditor_rows} result formulas"
                )
            if not validation:
                issues.append(f"{path.name}: decision dropdown is missing")
            if simple_io.validation_values(path, sheet_name, "D") != {"1", "2", "3"}:
                issues.append(f"{path.name}: decision choices mismatch")
            rows = len(records)
            visible_sheets = 1

        media_parts = sum(simple_io.workbook_media_hashes(path).values())
        expected_images = 498 if role == "primary" else len(payload_rows)
        anchor_count, anchor_issues = validate_picture_layout(
            path,
            role,
            single_sheet_primary,
            expected_auditor_rows=len(payload_rows),
        )
        if anchor_count != expected_images:
            issues.append(f"{path.name}: expected {expected_images} picture anchors, found {anchor_count}")
        issues.extend(f"{path.name}: {issue}" for issue in anchor_issues)
        image_count = anchor_count
        cached_errors = simple_io.invalid_error_cells(path)
        issues.extend(f"{path.name}: {issue}" for issue in cached_errors)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        issues.append(f"{path.name}: {exc}")
        rows = 0
        formulas = 0
        image_count = 0
        media_parts = 0
        anchor_count = 0
        visible_sheets = 0
        states = {}

    report = {
        "workbook": path.name,
        "role": role,
        "rows": rows,
        "visible_sheets": visible_sheets,
        "machine_sheet_state": states.get(MACHINE_SHEET, "missing"),
        "formulas": formulas,
        "embedded_images": image_count,
        "media_parts": media_parts,
        "picture_anchors": anchor_count,
        "sha256": sha256(path) if path.exists() else "",
        "issues": issues,
        "valid": not issues,
    }
    return report, issues


def verify_delivery(
    delivery_dir: Path,
    payload: dict[str, Any],
    zip_path: Path | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    reports: list[dict[str, Any]] = []
    guide_path = delivery_dir / README_NAME
    if not guide_path.exists():
        issues.append(f"missing {README_NAME}")
    else:
        guide = guide_path.read_text(encoding="utf-8-sig")
        for term in (
            "Gold v2.0 Global",
            "PRIMARY_REVIEW_498.xlsx",
            "1/2/3/4",
            "1/2/3",
            "618",
            "\u6ca1\u6709\u5b50 ZIP",
        ):
            if term not in guide:
                issues.append(f"{README_NAME}: missing {term!r}")

    primary_path = delivery_dir / payload["primary"]["workbook"]
    report, workbook_issues = validate_workbook(
        primary_path, "primary", payload["primary"]["rows"]
    )
    reports.append(report)
    issues.extend(workbook_issues)
    auditor_indexes: list[str] = []
    for auditor in payload["auditors"]:
        report, workbook_issues = validate_workbook(
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
                unsafe = [name for name in names if unsafe_zip_name(name)]
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
        "workflow": "numeric-key primary review with exception-only correction; auditors 1/2/3",
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


def write_zip(delivery_dir: Path, zip_path: Path, overwrite: bool) -> None:
    if zip_path.exists():
        if not overwrite:
            raise FileExistsError(zip_path)
        zip_path.unlink()
    temporary = zip_path.with_name(f".{zip_path.name}.tmp")
    if temporary.exists():
        temporary.unlink()
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
        if temporary.exists():
            temporary.unlink()


def build_delivery(
    root: Path,
    workbook_dir: Path,
    payload_json: Path,
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
    single_sheet_primary: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    workbook_dir = workbook_dir.resolve()
    payload_json = payload_json.resolve()
    delivery_dir = delivery_dir.resolve()
    zip_path = zip_path.resolve()
    allowed_root = root / "derived" / "human_adjudication"
    ensure_child(delivery_dir, allowed_root)
    ensure_child(zip_path, allowed_root)
    if not workbook_dir.is_dir():
        raise FileNotFoundError(workbook_dir)
    if not payload_json.is_file():
        raise FileNotFoundError(payload_json)
    payload = json.loads(payload_json.read_text(encoding="utf-8"))
    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)
    guide = one_sheet_owner_guide() if single_sheet_primary else owner_guide()
    (delivery_dir / README_NAME).write_text(guide, encoding="utf-8-sig")
    for name in expected_names() - {README_NAME}:
        source = workbook_dir / name
        if not source.exists():
            raise FileNotFoundError(source)
        destination = delivery_dir / name
        shutil.copy2(source, destination)
        set_sheet_state(destination, MACHINE_SHEET, "veryHidden")

    preliminary = verify_delivery(delivery_dir, payload)
    if not preliminary["valid"]:
        return preliminary
    write_zip(delivery_dir, zip_path, overwrite)
    return verify_delivery(delivery_dir, payload, zip_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--workbook-dir", type=Path, required=True)
    parser.add_argument("--payload-json", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--single-sheet-primary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_delivery(
        args.root,
        args.workbook_dir,
        args.payload_json,
        args.delivery_dir,
        args.zip_path,
        args.overwrite,
        args.single_sheet_primary,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
