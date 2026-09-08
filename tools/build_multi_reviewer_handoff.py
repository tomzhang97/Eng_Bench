#!/usr/bin/env python3
"""Build role assignments and instructions around an existing review batch.

The source review packs are treated as immutable evidence. This tool only adds
primary-review and independent-auditor control files; it never edits gold JSONL.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


MICROTEXT_STATUS = ("accepted", "edited", "rejected", "needs_full_page")
VISUALDIFF_STATUS = ("edit", "valid", "reject_unclear", "needs_full_page")
MICROTEXT_REVIEW_FIELDS = (
    "review_status",
    "corrected_text",
    "corrected_category",
    "review_notes",
)
VISUALDIFF_REVIEW_FIELDS = (
    "human_status",
    "human_description",
    "human_notes",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(fields)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stable_key(seed: str, *values: Any) -> str:
    text = ":".join(str(value or "") for value in values)
    return hashlib.sha256(f"{seed}:{text}".encode("utf-8")).hexdigest()


def record_id(row: dict[str, Any], task: str) -> str:
    field = "candidate_id" if task == "microtext" else "pair_id"
    return str(row.get(field) or "").strip()


def local_evidence_path(pack_name: str, value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    return f"review_packs/{pack_name}/{Path(text).name}" if "/" not in text else f"review_packs/{pack_name}/{text}"


def role_relative_path(package_path: str, role: str) -> str:
    if not package_path:
        return ""
    prefix = "../" if role == "primary" else "../../"
    return f"{prefix}{package_path}"


def canonicalize_pack(
    batch_dir: Path,
    pack_meta: dict[str, str],
    primary_start: int,
) -> list[dict[str, Any]]:
    pack_name = str(pack_meta["pack_name"])
    task = str(pack_meta["kind"])
    pack_dir = batch_dir / "review_packs" / pack_name
    checklist_path = pack_dir / str(pack_meta["checklist"])
    checklist = read_csv(checklist_path)
    manifest = read_jsonl(pack_dir / "manifest.jsonl")
    manifest_by_id = {record_id(row, task): row for row in manifest}
    rows: list[dict[str, Any]] = []
    for offset, source in enumerate(checklist):
        identifier = record_id(source, task)
        if not identifier:
            raise ValueError(f"{checklist_path}: row {offset + 2} missing record ID")
        details = manifest_by_id.get(identifier)
        if details is None:
            raise ValueError(f"{checklist_path}: {identifier} missing from manifest.jsonl")
        row = dict(source)
        row.update(
            {
                "primary_index": primary_start + offset,
                "record_id": identifier,
                "task": task,
                "pack_name": pack_name,
                "source_jsonl": str(pack_meta.get("source_jsonl") or ""),
                "source_candidate_id": str(details.get("source_candidate_id") or ""),
                "old_text": str(details.get("old_text") or ""),
                "new_text": str(details.get("new_text") or ""),
            }
        )
        path_fields = (
            "crop_path",
            "page_path",
            "old_crop_path",
            "new_crop_path",
            "panel_path",
            "old_page_path",
            "new_page_path",
        )
        for field in path_fields:
            row[f"package_{field}"] = local_evidence_path(pack_name, row.get(field))
        for field in MICROTEXT_REVIEW_FIELDS + VISUALDIFF_REVIEW_FIELDS:
            if field in row:
                row[field] = ""
        rows.append(row)
    if len(rows) != int(pack_meta["rows"]):
        raise ValueError(
            f"{pack_name}: expected {pack_meta['rows']} checklist rows, found {len(rows)}"
        )
    return rows


def load_primary_rows(batch_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    pack_manifest = read_csv(batch_dir / "NEXT_REVIEW_BATCH_MANIFEST.csv")
    rows: list[dict[str, Any]] = []
    next_index = 1
    for pack_meta in pack_manifest:
        pack_rows = canonicalize_pack(batch_dir, pack_meta, next_index)
        rows.extend(pack_rows)
        next_index += len(pack_rows)
    identifiers = [row["record_id"] for row in rows]
    duplicates = sorted(key for key, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate primary record IDs: {duplicates[:10]}")
    return rows, pack_manifest


def group_key(row: dict[str, Any]) -> str:
    if row["task"] == "microtext":
        return f"{row['pack_name']}|{row.get('category') or 'unknown'}"
    return f"{row.get('project_id') or 'unknown'}|{row.get('change_type') or 'unknown'}"


def balanced_sample(rows: list[dict[str, Any]], count: int, seed: str) -> list[dict[str, Any]]:
    if count > len(rows):
        raise ValueError(f"cannot sample {count} rows from a pool of {len(rows)}")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[group_key(row)].append(row)
    for key, group in groups.items():
        group.sort(key=lambda row: stable_key(seed, key, row["record_id"]))
    ordered_groups = sorted(groups, key=lambda key: stable_key(seed, "group", key))
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < count:
        added = False
        for key in ordered_groups:
            group = groups[key]
            if depth < len(group):
                selected.append(group[depth])
                added = True
                if len(selected) == count:
                    break
        if not added:
            raise ValueError("balanced sampling exhausted the pool unexpectedly")
        depth += 1
    return selected


def assign_auditors(
    primary_rows: list[dict[str, Any]],
    auditor_count: int,
    microtext_per_auditor: int,
    visualdiff_per_auditor: int,
    seed: str,
) -> list[dict[str, Any]]:
    microtext = [row for row in primary_rows if row["task"] == "microtext"]
    visualdiff = [row for row in primary_rows if row["task"] == "visualdiff"]
    micro_sample = balanced_sample(
        microtext, auditor_count * microtext_per_auditor, f"{seed}:microtext"
    )
    visual_sample = balanced_sample(
        visualdiff, auditor_count * visualdiff_per_auditor, f"{seed}:visualdiff"
    )
    assignments: list[dict[str, Any]] = []
    for task_rows in (micro_sample, visual_sample):
        task = str(task_rows[0]["task"])
        for index, row in enumerate(task_rows):
            auditor_number = (index % auditor_count) + 1
            assignment = dict(row)
            assignment["auditor_id"] = f"auditor_{auditor_number:02d}"
            assignment["audit_index"] = (
                sum(1 for item in assignments if item["auditor_id"] == assignment["auditor_id"])
                + 1
            )
            assignment["assignment_id"] = (
                f"{assignment['auditor_id']}__{task}__{assignment['record_id']}"
            )
            assignments.append(assignment)
    assignments.sort(key=lambda row: (row["auditor_id"], int(row["audit_index"])))
    expected = auditor_count * (microtext_per_auditor + visualdiff_per_auditor)
    if len(assignments) != expected:
        raise ValueError(f"expected {expected} assignments, found {len(assignments)}")
    ids = [row["record_id"] for row in assignments]
    if len(ids) != len(set(ids)):
        raise ValueError("auditor assignments overlap each other")
    return assignments


def microtext_export(row: dict[str, Any], role: str) -> dict[str, Any]:
    result = {
        "primary_index": row["primary_index"],
        "candidate_id": row["candidate_id"],
        "pack_name": row["pack_name"],
        "doc_id": row.get("doc_id", ""),
        "version_id": row.get("version_id", ""),
        "page_index": row.get("page_index", ""),
        "category": row.get("category", ""),
        "proposed_text": row.get("proposed_text", ""),
        "crop_path": role_relative_path(row.get("package_crop_path", ""), role),
        "page_path": role_relative_path(row.get("package_page_path", ""), role),
        "question_text": row.get("question_text", ""),
        "review_status": "",
        "corrected_text": "",
        "corrected_category": "",
        "review_notes": "",
    }
    if role == "auditor":
        result = {
            "audit_index": row["audit_index"],
            "auditor_id": row["auditor_id"],
            **result,
        }
    return result


def visualdiff_export(row: dict[str, Any], role: str) -> dict[str, Any]:
    result = {
        "primary_index": row["primary_index"],
        "pair_id": row["pair_id"],
        "pack_name": row["pack_name"],
        "project_id": row.get("project_id", ""),
        "split": row.get("split", ""),
        "page_old": row.get("page_old", ""),
        "page_new": row.get("page_new", ""),
        "change_type": row.get("change_type", ""),
        "current_description": row.get("current_description", ""),
        "old_text": row.get("old_text", ""),
        "new_text": row.get("new_text", ""),
        "panel_path": role_relative_path(row.get("package_panel_path", ""), role),
        "old_crop_path": role_relative_path(row.get("package_old_crop_path", ""), role),
        "new_crop_path": role_relative_path(row.get("package_new_crop_path", ""), role),
        "old_page_path": role_relative_path(row.get("package_old_page_path", ""), role),
        "new_page_path": role_relative_path(row.get("package_new_page_path", ""), role),
        "human_status": "",
        "human_description": "",
        "human_notes": "",
    }
    if role == "auditor":
        result = {
            "audit_index": row["audit_index"],
            "auditor_id": row["auditor_id"],
            **result,
        }
    return result


def write_auditor_index(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Eng_Bench 独立复核</title>",
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:24px;color:#172126}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #cfd8dc;padding:8px;vertical-align:top}"
        "th{background:#174c4f;color:white;position:sticky;top:0}img{max-width:620px;max-height:300px}"
        "code{font-size:11px;word-break:break-all}.meta{color:#52656a;font-size:12px}</style></head><body>",
        f"<h1>{html.escape(rows[0]['auditor_id'])} 独立复核</h1>",
        "<p>请独立判断，不要查看或复制主审核员的结果。只填写本文件夹中的 XLSX。</p>",
        f"<p>共 {len(rows)} 条：9 条 MicroText + 3 条 VisualDiff。</p><table>",
        "<tr><th>#</th><th>证据</th><th>ID / 类型</th><th>机器建议（不是标准答案）</th><th>整页</th></tr>",
    ]
    for row in rows:
        if row["task"] == "microtext":
            image = role_relative_path(row["package_crop_path"], "auditor")
            page = role_relative_path(row["package_page_path"], "auditor")
            proposal = f"{row.get('category', '')}: {row.get('proposed_text', '')}"
            page_links = f'<a href="{html.escape(page)}">打开整页</a>'
        else:
            image = role_relative_path(row["package_panel_path"], "auditor")
            old_page = role_relative_path(row["package_old_page_path"], "auditor")
            new_page = role_relative_path(row["package_new_page_path"], "auditor")
            proposal = (
                f"{row.get('change_type', '')}: {row.get('current_description', '')}"
                f"<br>old_text={html.escape(str(row.get('old_text', '')))}"
                f"<br>new_text={html.escape(str(row.get('new_text', '')))}"
            )
            page_links = (
                f'<a href="{html.escape(old_page)}">旧版整页</a><br>'
                f'<a href="{html.escape(new_page)}">新版整页</a>'
            )
        lines.append(
            f"<tr><td>{row['audit_index']}</td>"
            f'<td><a href="{html.escape(image)}"><img src="{html.escape(image)}"></a></td>'
            f"<td><code>{html.escape(row['record_id'])}</code><br>{html.escape(row['task'])}</td>"
            f"<td>{proposal}</td><td>{page_links}</td></tr>"
        )
    lines.extend(["</table></body></html>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_primary_dashboard(path: Path, pack_manifest: list[dict[str, str]]) -> None:
    lines = [
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Eng_Bench 主审核导航</title>",
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:32px;color:#172126}"
        "table{border-collapse:collapse;width:100%;max-width:1000px}th,td{border:1px solid #cfd8dc;padding:10px}"
        "th{background:#174c4f;color:white}a{color:#0b6670}</style></head><body>",
        "<h1>Eng_Bench Gold v2.0 Global 主审核导航</h1>",
        "<p>主审核员需要审核全部 498 条。先打开工作簿，再按 pack_name 打开对应图片索引。</p>",
        "<table><tr><th>Pack</th><th>任务</th><th>数量</th><th>图片索引</th></tr>",
    ]
    for pack in pack_manifest:
        name = str(pack["pack_name"])
        lines.append(
            f"<tr><td>{html.escape(name)}</td><td>{html.escape(str(pack['kind']))}</td>"
            f"<td>{html.escape(str(pack['rows']))}</td>"
            f'<td><a href="../review_packs/{html.escape(name)}/index.html">打开</a></td></tr>'
        )
    lines.extend(["</table></body></html>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def root_instructions(primary_count: int, auditor_count: int, rows_per_auditor: int) -> str:
    return f"""# 从这里开始：Eng_Bench Gold v2.0 Global 人工审核包

本 ZIP 是一份完整、扁平、可直接分发的人工审核包。请完整解压后再工作，不要在压缩包预览窗口里编辑。

## 人员分工

- 主审核员 1 人：独立完成全部 `{primary_count}` 条，填写 `01_PRIMARY_REVIEWER/PRIMARY_REVIEW_498.xlsx`。
- 独立复核员 10 人：每人只完成自己编号文件夹中的 `{rows_per_auditor}` 条（9 条 MicroText + 3 条 VisualDiff）。
- 十位复核员的样本彼此不重复，合计复核 `{auditor_count * rows_per_auditor}` 条；这些样本都来自主审核员的 498 条。
- 所有人必须独立判断。主审核员和复核员不得互看、讨论或复制彼此的答案，直到所有文件都交回。

## 分发方法

1. 把同一个 ZIP 发给 11 个人。
2. 告诉主审核员只打开 `01_PRIMARY_REVIEWER`。
3. 给十位复核员分别指定 `auditor_01` 至 `auditor_10`，每人只能打开自己的文件夹。
4. `review_packs` 是共用证据库，不需要改动。
5. `03_MACHINE_CONTROL` 只供项目维护者使用，审核员不要修改。

## 必须交回

- 主审核员：只交回未改名的 `PRIMARY_REVIEW_498.xlsx`。
- 复核员 N：只交回未改名的 `AUDITOR_NN_REVIEW_12.xlsx`。
- 不要交回图片、CSV、HTML 或整个 ZIP；不要删除、排序、插行或改列名。

收到 11 个 XLSX 后，由项目维护者进行一致性比较和冲突裁决。任何未复核、冲突或证据不足的行都不会直接进入 gold。
"""


def primary_instructions() -> str:
    return """# 主审核员详细说明（498 条）

## 开始前

1. 完整解压 ZIP。
2. 打开 `PRIMARY_INDEX.html`，确认七个图片索引都能显示。
3. 打开 `PRIMARY_REVIEW_498.xlsx`。只填写黄色列；灰色信息和公式列不要修改。
4. `MICROTEXT` 共 369 条，`VISUALDIFF` 共 129 条。

## MicroText 判断

先看 crop，再看 proposed_text 和 category；机器建议不是标准答案。必要时打开 page_path 看整页。

- `accepted`：截图完整、清晰，proposed_text 每个字符都能在截图中看到，类别也正确；修正列必须留空。
- `edited`：截图本身有效，但文字或类别有误；填写 `corrected_text` 和/或 `corrected_category`。
- `rejected`：截图缺字/截断、模糊、不是独立有效的工程标签、只是正文/页眉噪声，或有明确作废删除标记；在 notes 写原因。
- `needs_full_page`：只能临时使用。看完整页后必须改成 accepted、edited 或 rejected 才能交回。

关键规则：即使 OCR 猜出了完整文字，只要 crop 中缺少字母或字符，也必须 rejected。普通图线穿过文字不自动代表作废；只有明确删除/划销语义才 rejected。大小写、空格、连字符、小数点、英尺英寸和角度符号必须逐字符准确。

类别常用含义：`dimension_value` 尺寸/角度/公差数值；`room_label` 房间或空间名；`equipment_tag` 设备标识；`instrument_tag` 仪表位号；`pipe_line_tag` 管线编号；`pin_label` 引脚/电路网络/元件短标签。确实是有效工程微文本但无法归入已有类时才用 `unknown_microtext`。

## VisualDiff 判断

先看 panel 中 OLD/NEW，再按需打开旧版和新版整页。只判断裁剪区域内有证据的真实变化。

- `edit`：裁剪内存在真实、有意义的变化；必须在 `human_description` 写一句准确、简洁、可从图中验证的描述。
- `valid`：只有当 `current_description` 已经完全正确时使用。本批多数是 TODO，所以通常应使用 edit 写描述。
- `reject_unclear`：两图完全一样、只有整体裁剪/对齐偏移、变化只在框外、内容模糊或无法可靠说明。
- `needs_full_page`：临时状态；看完旧/新整页后必须改成最终状态。

关键规则：两个图完全相同不是 layout，填 `reject_unclear`。只有一丁点整体偏移也填 `reject_unclear`。若某个具体对象相对周围图纸内容移动，才算真实 layout change，并用 `edit` 说明对象和方向。

## 交回前自检

1. 两个任务表都完成；没有空状态，也没有 `needs_full_page`。
2. 所有 `row_check` 均为 `OK`。
3. accepted 不留修正；edited 填必要修正；rejected 写原因；VisualDiff edit 写描述。
4. 不排序、不删行、不插行、不改 ID、路径、列名或公式。
5. 只交回未改名的 `PRIMARY_REVIEW_498.xlsx`。
"""


def auditor_instructions(rows_per_auditor: int) -> str:
    return f"""# 独立复核员详细说明（每人 {rows_per_auditor} 条）

每位复核员只处理自己编号文件夹中的 XLSX 和 `index.html`。请独立判断，不要查看、索要、讨论或复制主审核员及其他复核员的答案。本工作不是“同意/不同意主审核员”，而是重新做一次盲审。

## 操作步骤

1. 完整解压 ZIP，进入分配给你的 `auditor_NN` 文件夹。
2. 打开 `index.html` 看 12 张证据；必要时点击整页链接。
3. 打开 `AUDITOR_NN_REVIEW_12.xlsx`，只填写黄色列。
4. `MICROTEXT` 9 条、`VISUALDIFF` 3 条，规则与主审核完全相同。

MicroText：accepted=截图完整且文字/类别全对；edited=截图有效但文字或类别需改；rejected=缺字、截断、模糊、无效标签或明确作废；needs_full_page 只能临时使用。OCR 猜对截图外缺失字符也不能 accepted。

VisualDiff：edit=裁剪内有真实变化并写描述；valid=现有描述已准确；reject_unclear=相同、仅整体偏移、变化在框外或不清楚；needs_full_page 看整页后必须改成最终状态。完全相同或只有轻微整体偏移都不是 layout。

交回前确认 `row_check` 全部为 OK、没有空状态和 needs_full_page；不排序、不删行、不改 ID/路径/列名。只交回未改名的 `AUDITOR_NN_REVIEW_12.xlsx`。
"""


def build_handoff(
    batch_dir: Path,
    auditor_count: int = 10,
    rows_per_auditor: int = 12,
    microtext_per_auditor: int = 9,
    seed: str = "engbench-multireviewer-2026-08-07-v1",
) -> dict[str, Any]:
    batch_dir = batch_dir.resolve()
    visualdiff_per_auditor = rows_per_auditor - microtext_per_auditor
    if auditor_count <= 0 or rows_per_auditor <= 0:
        raise ValueError("auditor_count and rows_per_auditor must be positive")
    if not 0 < microtext_per_auditor < rows_per_auditor:
        raise ValueError("microtext_per_auditor must be between 1 and rows_per_auditor - 1")

    primary_rows, pack_manifest = load_primary_rows(batch_dir)
    assignments = assign_auditors(
        primary_rows,
        auditor_count,
        microtext_per_auditor,
        visualdiff_per_auditor,
        seed,
    )

    primary_dir = batch_dir / "01_PRIMARY_REVIEWER"
    auditors_dir = batch_dir / "02_INDEPENDENT_AUDITORS"
    control_dir = batch_dir / "03_MACHINE_CONTROL"
    for path in (primary_dir, auditors_dir, control_dir):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)

    primary_micro = [microtext_export(row, "primary") for row in primary_rows if row["task"] == "microtext"]
    primary_visual = [visualdiff_export(row, "primary") for row in primary_rows if row["task"] == "visualdiff"]
    write_csv(primary_dir / "PRIMARY_MICROTEXT_SOURCE.csv", primary_micro, primary_micro[0].keys())
    write_csv(primary_dir / "PRIMARY_VISUALDIFF_SOURCE.csv", primary_visual, primary_visual[0].keys())
    (primary_dir / "PRIMARY_INSTRUCTIONS_ZH.md").write_text(primary_instructions(), encoding="utf-8")
    (primary_dir / "RETURN_ONLY_THIS_FILE.txt").write_text(
        "Return only PRIMARY_REVIEW_498.xlsx without renaming it.\n", encoding="ascii"
    )
    write_primary_dashboard(primary_dir / "PRIMARY_INDEX.html", pack_manifest)

    assignment_rows: list[dict[str, Any]] = []
    return_rows = [
        {
            "role": "primary",
            "assignee": "primary_reviewer",
            "rows": len(primary_rows),
            "return_file": "PRIMARY_REVIEW_498.xlsx",
            "folder": "01_PRIMARY_REVIEWER",
        }
    ]
    payload_auditors: list[dict[str, Any]] = []
    for number in range(1, auditor_count + 1):
        auditor_id = f"auditor_{number:02d}"
        rows = [row for row in assignments if row["auditor_id"] == auditor_id]
        micro = [microtext_export(row, "auditor") for row in rows if row["task"] == "microtext"]
        visual = [visualdiff_export(row, "auditor") for row in rows if row["task"] == "visualdiff"]
        folder = auditors_dir / auditor_id
        folder.mkdir(parents=True)
        write_csv(folder / f"AUDITOR_{number:02d}_MICROTEXT_SOURCE.csv", micro, micro[0].keys())
        write_csv(folder / f"AUDITOR_{number:02d}_VISUALDIFF_SOURCE.csv", visual, visual[0].keys())
        write_auditor_index(folder / "index.html", rows)
        (folder / "RETURN_ONLY_THIS_FILE.txt").write_text(
            f"Return only AUDITOR_{number:02d}_REVIEW_12.xlsx without renaming it.\n",
            encoding="ascii",
        )
        for row in rows:
            assignment_rows.append(
                {
                    "assignment_id": row["assignment_id"],
                    "auditor_id": auditor_id,
                    "audit_index": row["audit_index"],
                    "primary_index": row["primary_index"],
                    "record_id": row["record_id"],
                    "task": row["task"],
                    "pack_name": row["pack_name"],
                    "stratum": group_key(row),
                }
            )
        payload_auditors.append({"auditor_id": auditor_id, "microtext": micro, "visualdiff": visual})
        return_rows.append(
            {
                "role": "independent_auditor",
                "assignee": auditor_id,
                "rows": len(rows),
                "return_file": f"AUDITOR_{number:02d}_REVIEW_12.xlsx",
                "folder": f"02_INDEPENDENT_AUDITORS/{auditor_id}",
            }
        )

    (auditors_dir / "AUDITOR_INSTRUCTIONS_ZH.md").write_text(
        auditor_instructions(rows_per_auditor), encoding="utf-8"
    )
    write_csv(control_dir / "audit_assignments.csv", assignment_rows, assignment_rows[0].keys())
    write_csv(control_dir / "return_manifest.csv", return_rows, return_rows[0].keys())

    payload = {
        "goal": "Gold v2.0 Global",
        "seed": seed,
        "primary": {"microtext": primary_micro, "visualdiff": primary_visual},
        "auditors": payload_auditors,
    }
    write_json(control_dir / "workbook_payload.json", payload)

    task_counts = Counter(row["task"] for row in primary_rows)
    category_counts = Counter(
        str(row.get("category") or row.get("change_type") or "unknown") for row in primary_rows
    )
    report = {
        "goal": "Gold v2.0 Global",
        "batch_dir": batch_dir.as_posix(),
        "seed": seed,
        "primary_rows": len(primary_rows),
        "primary_task_counts": dict(sorted(task_counts.items())),
        "primary_category_or_change_counts": dict(sorted(category_counts.items())),
        "auditor_count": auditor_count,
        "rows_per_auditor": rows_per_auditor,
        "microtext_per_auditor": microtext_per_auditor,
        "visualdiff_per_auditor": visualdiff_per_auditor,
        "audited_rows": len(assignments),
        "audited_unique_rows": len({row["record_id"] for row in assignments}),
        "cross_auditor_overlap": len(assignments) - len({row["record_id"] for row in assignments}),
        "all_audits_subset_of_primary": {row["record_id"] for row in assignments}.issubset(
            {row["record_id"] for row in primary_rows}
        ),
        "auditor_distribution": {
            auditor["auditor_id"]: {
                "rows": len(auditor["microtext"]) + len(auditor["visualdiff"]),
                "microtext": len(auditor["microtext"]),
                "visualdiff": len(auditor["visualdiff"]),
            }
            for auditor in payload_auditors
        },
        "workbooks_expected": 1 + auditor_count,
        "gold_rows_modified": 0,
        "valid": True,
    }
    write_json(control_dir / "multi_reviewer_build_report.json", report)
    (control_dir / "multi_reviewer_build_report.md").write_text(
        "\n".join(
            [
                "# Multi-reviewer Handoff Build Report",
                "",
                f"- Goal: `{report['goal']}`",
                f"- Primary rows: `{report['primary_rows']}`",
                f"- Primary mix: `{report['primary_task_counts']}`",
                f"- Independent auditors: `{auditor_count}`",
                f"- Rows per auditor: `{rows_per_auditor}`",
                f"- Unique double-reviewed rows: `{report['audited_unique_rows']}`",
                f"- Cross-auditor overlap: `{report['cross_auditor_overlap']}`",
                f"- Gold rows modified: `{report['gold_rows_modified']}`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (batch_dir / "00_START_HERE_ZH.md").write_text(
        root_instructions(len(primary_rows), auditor_count, rows_per_auditor), encoding="utf-8"
    )
    (batch_dir / "WHO_DOES_WHAT_ZH.md").write_text(
        "\n".join(
            [
                "# 分工表",
                "",
                "| 人员 | 工作量 | 打开的文件夹 | 必须交回 |",
                "|---|---:|---|---|",
                "| 主审核员 | 498 | `01_PRIMARY_REVIEWER` | `PRIMARY_REVIEW_498.xlsx` |",
                *[
                    f"| 复核员 {number:02d} | 12 | `02_INDEPENDENT_AUDITORS/auditor_{number:02d}` | `AUDITOR_{number:02d}_REVIEW_12.xlsx` |"
                    for number in range(1, auditor_count + 1)
                ],
                "",
                "十位复核员必须独立工作，且不要查看主审核员结果。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (batch_dir / "README.md").write_text(
        """# Eng_Bench Primary + Independent Audit Handoff

Start with `00_START_HERE_ZH.md`.

- One primary reviewer completes all 498 rows.
- Ten independent auditors complete 12 disjoint overlap rows each.
- Evidence is shared under `review_packs`.
- Only assigned XLSX files are editable and returned.
- This handoff does not modify or promote gold data.
""",
        encoding="utf-8",
    )
    (batch_dir / "HUMAN_REVIEW_STEPS.md").write_text(
        """# Human Review Steps

The complete novice-facing workflow is in `00_START_HERE_ZH.md`.

1. Extract the ZIP fully.
2. Assign the primary reviewer and auditors 01-10.
3. Reviewers work independently using only their assigned XLSX and shared evidence.
4. Return the 11 unchanged XLSX filenames listed in `03_MACHINE_CONTROL/return_manifest.csv`.
5. Maintainers compare the 120 overlap decisions and adjudicate disagreements before promotion.

No unreviewed row is merged into gold by this packet.
""",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--auditors", type=int, default=10)
    parser.add_argument("--rows-per-auditor", type=int, default=12)
    parser.add_argument("--microtext-per-auditor", type=int, default=9)
    parser.add_argument("--seed", default="engbench-multireviewer-2026-08-07-v1")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_handoff(
        args.batch_dir,
        auditor_count=args.auditors,
        rows_per_auditor=args.rows_per_auditor,
        microtext_per_auditor=args.microtext_per_auditor,
        seed=args.seed,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
