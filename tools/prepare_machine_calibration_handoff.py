#!/usr/bin/env python3
"""Prepare and verify a flat human handoff for remaining machine calibration rows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image


REQUIRED_COLUMNS = {
    "sample_index",
    "candidate_id",
    "doc_id",
    "version_id",
    "page_index",
    "bbox",
    "category",
    "proposed_text",
    "crop_path",
    "context_path",
    "reviewer_decision",
    "corrected_text",
    "corrected_category",
    "reviewer_notes",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_relative_path(value: str, expected_dir: str) -> Path:
    path = Path(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe evidence path: {value}")
    if path.parts[0] != expected_dir or len(path.parts) != 2:
        raise ValueError(f"evidence must be directly under {expected_dir}: {value}")
    return path


def copy_evidence(
    *, source_dir: Path, output_dir: Path, rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    seen_crops: set[str] = set()
    seen_contexts: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen_candidates:
            raise ValueError(f"missing or duplicate candidate_id: {candidate_id}")
        seen_candidates.add(candidate_id)
        crop_relative = safe_relative_path(row["crop_path"], "crops")
        context_relative = safe_relative_path(row["context_path"], "contexts")
        if crop_relative.as_posix() in seen_crops:
            raise ValueError(f"duplicate crop path: {crop_relative}")
        if context_relative.as_posix() in seen_contexts:
            raise ValueError(f"duplicate context path: {context_relative}")
        seen_crops.add(crop_relative.as_posix())
        seen_contexts.add(context_relative.as_posix())

        crop_source = source_dir / crop_relative
        context_source = source_dir / context_relative
        if not crop_source.is_file() or not context_source.is_file():
            raise FileNotFoundError(
                f"missing evidence for {candidate_id}: {crop_source} | {context_source}"
            )
        crop_target = output_dir / crop_relative
        context_target = output_dir / context_relative
        crop_target.parent.mkdir(parents=True, exist_ok=True)
        context_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(crop_source, crop_target)
        shutil.copy2(context_source, context_target)
        with Image.open(crop_target) as image:
            crop_width, crop_height = image.size
        with Image.open(context_target) as image:
            context_width, context_height = image.size
        enriched.append(
            {
                **row,
                "crop_path": crop_relative.as_posix(),
                "context_path": context_relative.as_posix(),
                "crop_sha256": file_sha256(crop_target),
                "context_sha256": file_sha256(context_target),
                "crop_width": crop_width,
                "crop_height": crop_height,
                "context_width": context_width,
                "context_height": context_height,
            }
        )
    return enriched


def render_index(rows: list[dict[str, Any]]) -> str:
    row_count = len(rows)
    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row['sample_index']))}</td>"
            f"<td><img src=\"{html.escape(row['crop_path'])}\" alt=\"crop\"></td>"
            f"<td><a href=\"{html.escape(row['context_path'])}\" target=\"_blank\">打开上下文</a></td>"
            f"<td>{html.escape(str(row.get('proposed_text') or ''))}</td>"
            f"<td>{html.escape(str(row.get('category') or ''))}</td>"
            f"<td><code>{html.escape(str(row.get('candidate_id') or ''))}</code></td>"
            "</tr>"
        )
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Eng_Bench 机器校准 __ROW_COUNT__</title>
<style>
body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:20px;color:#17202a;background:#fff}
h1{font-size:24px;margin:0 0 8px}p{margin:6px 0 18px;color:#44515d}
table{border-collapse:collapse;width:100%;table-layout:fixed}th{position:sticky;top:0;background:#17324d;color:#fff;z-index:1}
th,td{border-bottom:1px solid #d8e0e7;padding:8px;vertical-align:middle;text-align:left}
th:nth-child(1),td:nth-child(1){width:52px}th:nth-child(2),td:nth-child(2){width:240px}
th:nth-child(3),td:nth-child(3){width:90px}th:nth-child(4),td:nth-child(4){width:120px}
th:nth-child(5),td:nth-child(5){width:130px}img{max-width:230px;max-height:90px;object-fit:contain}
code{font-size:11px;word-break:break-all}a{color:#005bbb}
</style></head><body>
<h1>Eng_Bench Gold v2.0 机器校准复核</h1>
<p>共 __ROW_COUNT__ 条。请在 Excel 中填写判断；本页面用于快速浏览裁剪图和打开上下文。</p>
<table><thead><tr><th>#</th><th>裁剪图</th><th>上下文</th><th>机器文字</th><th>机器类别</th><th>候选 ID</th></tr></thead>
<tbody>""" + "\n".join(table_rows) + """</tbody></table></body></html>"""
    return page.replace("__ROW_COUNT__", str(row_count))


def chinese_instructions(row_count: int) -> str:
    return f"""# Eng_Bench Gold v2.0 机器校准复核说明

## 你的任务

这是一批 **{row_count} 条独立校准样本**，用于判断机器能否批量处理训练集 MicroText。
这不是让你盲目确认机器结果。每一条都必须独立看图，并填写一次判断。

只需要返回 `MACHINE_CALIBRATION_{row_count}.xlsx`，不要修改文件名，不要删除工作表或行。

## 操作步骤

1. 打开 `MACHINE_CALIBRATION_{row_count}.xlsx`，进入 `审核{row_count}条`。
2. 查看每行嵌入的裁剪图。需要更多信息时，点击“打开上下文”；也可以打开 `index.html` 快速浏览。
3. 同时核对机器文字和机器类别。
4. 在“判断”列只选 `correct`、`incorrect` 或 `unclear`。
5. 全部完成后确认顶部“剩余”为 0，然后只返回 Excel 文件。

## 三种判断

- `correct`：裁剪中的完整目标文字与“机器文字”完全一致，且类别正确。
- `incorrect`：文字或类别至少一个确定错误。请填写“纠正文字”和/或“纠正类别”，并在备注中简要说明。
- `unclear`：裁剪或上下文不足以可靠判断，例如目标被截断、严重模糊、遮挡或无法确定工程含义。请在备注说明原因。

`incorrect` 和 `unclear` 都会阻止整批机器认证，这是预期的安全机制，不要为了让批次通过而选择 `correct`。

## 类别速查

- `component_value`：元件参数，如 `10k`、`100nF`、`2.2uH`。
- `dimension_value`：工程尺寸，如 `25 mm`、`R3`、`45°`。
- `equipment_tag`：设备编号或名称，如 `P-101`、`MOTOR`。
- `instrument_tag`：仪表回路标签，如 `PT-101`、`FIC-22`。
- `pipe_line_tag`：管线编号，如 `6\"-CW-101`。
- `process_label`：工艺介质或流程名称，如 `COOLING WATER`。
- `process_value`：压力、温度、流量等工艺数值，如 `2.5 bar`。
- `room_label`：房间或区域名称，如 `ELECTRICAL ROOM`。
- `tolerance_value`：公差，如 `±0.05`、`+0.2/-0.1`。

## 边界规则

- 只判断目标工程标签，不要求图片中所有文字都与机器结果一致。
- 目标明显少字、半个字符或被裁掉时，不算 `correct`；确定缺失则选 `incorrect`，无法确定则选 `unclear`。
- 删除线或修订线穿过文字不自动作废：若目标仍清楚且机器文字/类别正确，可选 `correct`。
- 大小写、连字符、单位、希腊字母和小数点都属于文字内容，必须核对。
- 不要根据候选 ID 猜答案，也不要复制其他人的判断。

## 为什么要做

这 {row_count} 条是冻结的统计校准样本。只有全部完成且错误率满足预设门槛，机器才可能接管其余重复训练样本；任何批量进入 Gold 的操作仍需通过来源、分割、泄漏、重复和严格验证。
"""


def prepare(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_calibration_dir).resolve()
    checklist_path = Path(args.remaining_checklist).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    fields, rows = read_csv(checklist_path)
    missing_columns = sorted(REQUIRED_COLUMNS - set(fields))
    if missing_columns:
        raise ValueError(f"missing checklist columns: {missing_columns}")
    if len(rows) != args.expected_rows:
        raise ValueError(f"expected {args.expected_rows} rows, found {len(rows)}")
    if any(str(row.get("reviewer_decision") or "").strip() for row in rows):
        raise ValueError("remaining checklist unexpectedly contains decisions")

    enriched = copy_evidence(source_dir=source_dir, output_dir=output_dir, rows=rows)
    output_fields = fields + [
        "crop_sha256",
        "context_sha256",
        "crop_width",
        "crop_height",
        "context_width",
        "context_height",
    ]
    write_csv(output_dir / "calibration_checklist.csv", output_fields, enriched)
    manifest = {
        "schema": "eng_bench_machine_calibration_handoff_v1",
        "goal": "Gold v2.0 Global",
        "date_label": args.date_label,
        "rows": len(enriched),
        "decision_values": ["correct", "incorrect", "unclear"],
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "source_calibration_dir": source_dir.as_posix(),
        "source_remaining_checklist": checklist_path.as_posix(),
        "source_remaining_checklist_sha256": file_sha256(checklist_path),
        "records": enriched,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "index.html").write_text(render_index(enriched), encoding="utf-8")
    (output_dir / "README_中文.md").write_text(
        chinese_instructions(len(enriched)), encoding="utf-8"
    )
    (output_dir / "RETURN_THIS_FILE_ONLY.txt").write_text(
        f"Only return MACHINE_CALIBRATION_{len(enriched)}.xlsx\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "rows": len(enriched),
                "crops": len(list((output_dir / "crops").glob("*.png"))),
                "contexts": len(list((output_dir / "contexts").glob("*.png"))),
                "safe_to_merge_gold": False,
            },
            indent=2,
        )
    )
    return 0


def finalize(args: argparse.Namespace) -> int:
    packet_dir = Path(args.packet_dir).resolve()
    workbook = Path(args.workbook).resolve()
    zip_path = Path(args.zip_path).resolve()
    manifest = json.loads((packet_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_rows = int(manifest["rows"])
    if not workbook.is_file():
        raise FileNotFoundError(workbook)
    workbook_name = f"MACHINE_CALIBRATION_{expected_rows}.xlsx"
    shutil.copy2(workbook, packet_dir / workbook_name)
    files = sorted(path for path in packet_dir.rglob("*") if path.is_file())
    crop_count = sum(path.parent.name == "crops" for path in files)
    context_count = sum(path.parent.name == "contexts" for path in files)
    if crop_count != expected_rows or context_count != expected_rows:
        raise ValueError(
            f"evidence count mismatch: crops={crop_count}, contexts={context_count}, expected={expected_rows}"
        )
    if any(path.suffix.lower() == ".zip" for path in files):
        raise ValueError("packet directory contains a nested ZIP")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(packet_dir).as_posix())
    with zipfile.ZipFile(zip_path) as archive:
        bad_entry = archive.testzip()
        names = archive.namelist()
    if bad_entry:
        raise ValueError(f"ZIP CRC failure: {bad_entry}")
    if any(name.lower().endswith(".zip") for name in names):
        raise ValueError("ZIP contains a nested ZIP")
    report = {
        "goal": "Gold v2.0 Global",
        "rows": expected_rows,
        "workbook": workbook_name,
        "workbook_sha256": file_sha256(packet_dir / workbook_name),
        "crop_files": crop_count,
        "context_files": context_count,
        "zip_entries": len(names),
        "zip_sha256": file_sha256(zip_path),
        "zip_bad_entry": bad_entry,
        "nested_zips": 0,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }
    report_path = zip_path.with_suffix(".verification.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source-calibration-dir", required=True)
    prepare_parser.add_argument("--remaining-checklist", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--date-label", required=True)
    prepare_parser.add_argument("--expected-rows", type=int, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--packet-dir", required=True)
    finalize_parser.add_argument("--workbook", required=True)
    finalize_parser.add_argument("--zip-path", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return prepare(args) if args.command == "prepare" else finalize(args)


if __name__ == "__main__":
    raise SystemExit(main())
