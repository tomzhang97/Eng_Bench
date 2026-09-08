#!/usr/bin/env python3
"""Build a flat human-review packet for VisualDiff English descriptions."""
from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
from pathlib import Path
from typing import Any


CSV_COLUMNS = [
    "row_number",
    "pair_id",
    "project_id",
    "old_text",
    "new_text",
    "chinese_description",
    "panel_file",
    "english_description",
    "translation_status",
    "review_notes",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_packet(root: Path, input_path: Path, output_dir: Path) -> dict[str, Any]:
    rows = read_jsonl(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    checklist_rows: list[dict[str, str]] = []
    missing_panels: list[str] = []
    for index, row in enumerate(rows, start=1):
        pair_id = str(row.get("pair_id") or "")
        review_pack = str(row.get("_review_pack") or "")
        panel_path = str(row.get("panel_path") or "")
        source_panel = root / "derived" / "review_packs" / review_pack / panel_path
        panel_name = f"{index:03d}__{Path(panel_path).name}"
        target_panel = evidence_dir / panel_name
        if source_panel.is_file():
            shutil.copy2(source_panel, target_panel)
        else:
            missing_panels.append(source_panel.as_posix())

        checklist_rows.append(
            {
                "row_number": str(index),
                "pair_id": pair_id,
                "project_id": str(row.get("project_id") or ""),
                "old_text": str(row.get("old_text") or ""),
                "new_text": str(row.get("new_text") or ""),
                "chinese_description": str(row.get("human_description") or ""),
                "panel_file": f"evidence/{panel_name}",
                "english_description": "",
                "translation_status": "",
                "review_notes": "",
            }
        )

    checklist_path = output_dir / "visualdiff_english_confirmation_checklist.csv"
    with checklist_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(checklist_rows)

    instructions = """# VisualDiff 英文描述确认（77 条）

## 目标

这些行已经完成中文人工判断，但 Gold 数据需要清晰、可复核的英文变化描述。
你需要对照左右图片和中文描述，填写英文描述，并确认描述与图中变化一致。

## 操作步骤

1. 先打开 `index.html` 浏览图片，也可以直接打开 `evidence/` 中的 panel。
2. 在 `visualdiff_english_confirmation_checklist.csv` 中逐行处理。
3. 查看左图（旧版本）、右图（新版本）、`old_text`、`new_text` 和中文描述。
4. 在 `english_description` 填写完整英文句子，明确说明从旧版本到新版本发生了什么。
5. 在 `translation_status` 只填写以下一种：
   - `accepted`：中文判断正确，英文描述已填写且与图片一致。
   - `edited`：中文描述有小问题，但图片中的真实变化可以确认；填写修正后的英文描述，并在备注说明。
   - `rejected`：图片不支持该变化、左右图相同、裁剪无法判断或变化不属于有效工程差异。
6. `review_notes` 仅在 `edited` 或 `rejected` 时必填。
7. 不要修改 `pair_id`、`project_id`、图片路径、旧文字或新文字。

## 英文描述要求

- 使用一句简洁、具体的英文。
- 说明对象和变化方向，例如：`The fiscal-year label changed from FY 2023-24 to FY 2024-25.`
- 不要只写 `text changed`、`layout changed` 或 `different`。
- 只描述证据图片中可以看到的变化，不做设计意图推断。

## 完成检查

- 77 行全部填写 `translation_status`。
- 所有 `accepted` / `edited` 行都填写 `english_description`。
- 所有 `edited` / `rejected` 行都填写 `review_notes`。
- 返回修改后的 CSV；不要改文件名，也不要删除图片。
"""
    (output_dir / "README_FIRST_ZH.md").write_text(instructions, encoding="utf-8")

    body_rows = []
    for row in checklist_rows:
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(row['row_number'])}</td>"
            f"<td><img src=\"{html.escape(row['panel_file'])}\" loading=\"lazy\"></td>"
            f"<td><code>{html.escape(row['old_text'])}</code></td>"
            f"<td><code>{html.escape(row['new_text'])}</code></td>"
            f"<td>{html.escape(row['chinese_description'])}</td>"
            f"<td><code>{html.escape(row['pair_id'])}</code></td>"
            "</tr>"
        )
    index_html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>VisualDiff 英文描述确认</title>
<style>
body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:20px;color:#1f2937}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #d1d5db;padding:8px;vertical-align:top}
th{background:#e5eef8;position:sticky;top:0}img{max-width:720px;height:auto}code{white-space:normal}
</style></head><body>
<h1>VisualDiff 英文描述确认</h1>
<p>共 77 条。请在 CSV 或 Excel 中填写，图片用于证据核对。</p>
<table><thead><tr><th>#</th><th>旧/新对比</th><th>旧文字</th><th>新文字</th><th>中文描述</th><th>Pair ID</th></tr></thead>
<tbody>""" + "\n".join(body_rows) + """</tbody></table></body></html>"""
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    report = {
        "input": input_path.as_posix(),
        "rows": len(rows),
        "panels_copied": len(rows) - len(missing_panels),
        "missing_panels": missing_panels,
        "checklist": checklist_path.as_posix(),
    }
    (output_dir / "packet_build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a VisualDiff English-description confirmation packet."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = root / input_path
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    report = build_packet(root, input_path, output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["missing_panels"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
