#!/usr/bin/env python3
"""Build a human packet for manual microtext region mining from rendered pages."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
import sys
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from tools.build_local_source_conversion_actions import (
    MACHINE_STEPS,
    is_release_safe,
    latest_readiness_json,
    planning_score,
)


CHECKLIST_FIELDS = [
    "row_id",
    "doc_id",
    "domain",
    "page_index",
    "region_slot",
    "page_image",
    "source_path",
    "category",
    "transcribed_text",
    "bbox_xyxy",
    "status",
    "notes",
]

RIGHTS_BLOCKING_MARKERS = (
    "unknown",
    "restricted",
    "proprietary",
    "reference_only",
    "internal_only",
    "rights_uncertain",
    "release_review_needed",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_int(value: Any) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return 0


def resolve_under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def rel_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def page_image_paths(root: Path, doc_id: str, dpi: int) -> list[tuple[int, Path]]:
    base = root / "derived" / f"pages_{dpi}dpi" / doc_id
    pages: list[tuple[int, Path]] = []
    for path in sorted(base.glob("page_*.png")):
        stem = path.stem.removeprefix("page_")
        if stem.isdigit():
            pages.append((int(stem), path))
    return pages


def selected_source_rows(
    readiness: dict[str, Any],
    *,
    max_docs: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pool: list[dict[str, Any]] = []
    rights_blocked: list[dict[str, Any]] = []
    for raw in readiness.get("local_sources", []):
        row = dict(raw)
        if str(row.get("next_step") or "") not in MACHINE_STEPS:
            continue
        if as_int(row.get("rendered_pages")) <= 0:
            continue
        if not is_release_safe(row):
            rights_blocked.append(row)
            continue
        row["planning_score"] = planning_score(row)
        pool.append(row)

    pool.sort(
        key=lambda row: (
            as_int(row.get("planning_score")),
            as_int(row.get("priority_score")),
            str(row.get("doc_id") or ""),
        ),
        reverse=True,
    )
    return pool[: max(0, max_docs)], rights_blocked


def copy_selected_pages(
    *,
    root: Path,
    output_dir: Path,
    selected_rows: list[dict[str, Any]],
    pages_per_doc: int,
    regions_per_page: int,
    dpi: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_records: list[dict[str, Any]] = []
    checklist_rows: list[dict[str, Any]] = []

    for source in selected_rows:
        doc_id = str(source.get("doc_id") or "")
        domain = str(source.get("domain") or "")
        source_path = str(source.get("source_path") or "").replace("\\", "/")
        available_pages = page_image_paths(root, doc_id, dpi=dpi)[: max(0, pages_per_doc)]
        for page_index, page_path in available_pages:
            dest_name = f"{doc_id}_p{page_index:04d}.png"
            dest_path = pages_dir / dest_name
            shutil.copy2(page_path, dest_path)
            page_rel = dest_path.relative_to(output_dir).as_posix()
            page_records.append(
                {
                    "doc_id": doc_id,
                    "domain": domain,
                    "page_index": page_index,
                    "page_image": page_rel,
                    "source_path": source_path,
                    "source_page_image": rel_to(page_path, root),
                }
            )
            for slot in range(1, regions_per_page + 1):
                checklist_rows.append(
                    {
                        "row_id": f"mrm__{doc_id}__p{page_index:04d}__r{slot:02d}",
                        "doc_id": doc_id,
                        "domain": domain,
                        "page_index": page_index,
                        "region_slot": slot,
                        "page_image": page_rel,
                        "source_path": source_path,
                        "category": "",
                        "transcribed_text": "",
                        "bbox_xyxy": "",
                        "status": "",
                        "notes": "",
                    }
                )
    return page_records, checklist_rows


def write_checklist(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CHECKLIST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CHECKLIST_FIELDS})


def write_source_pages(path: Path, page_records: list[dict[str, Any]]) -> None:
    fields = ["doc_id", "domain", "page_index", "page_image", "source_path", "source_page_image"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in page_records:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_readme(output_dir: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Eng_Bench Manual Region Mining Packet",
        "",
        "This packet is for proposing new microtext candidate regions from rendered engineering pages.",
        "It is not gold data, and it must not be merged until a maintainer converts and validates the returned CSV.",
        "",
        "## Contents",
        "",
        "- `INTERN_INSTRUCTIONS_ZH.md`: Chinese reviewer instructions.",
        "- `manual_region_mining_checklist.csv`: fillable checklist for proposed regions.",
        "- `source_pages.csv`: page-level source index.",
        "- `pages/`: copied full-page PNGs for review.",
        "- `index.html`: browsable page index.",
        "- `packet_manifest.json`: machine-readable packet manifest.",
        "",
        "## Counts",
        "",
        f"- Selected docs: `{totals['selected_docs']}`",
        f"- Selected pages: `{totals['selected_pages']}`",
        f"- Checklist rows: `{totals['checklist_rows']}`",
        f"- Rights-blocked docs skipped: `{totals['rights_blocked_docs']}`",
        "",
        "Return the completed CSV and keep file names unchanged.",
        "",
    ]
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_chinese_instructions(output_dir: Path) -> None:
    (output_dir / "INTERN_INSTRUCTIONS_ZH.md").write_text(
        "\n".join(
            [
                "# Eng_Bench 手工区域挖掘说明",
                "",
                "这个包的目标是从完整工程图页面里找出适合作为 microtext benchmark 的小文字区域。这里不是让你直接改 gold 数据，也不是让你判断模型结果；你只需要提出清晰、可验证的新候选区域。",
                "",
                "## 你需要打开哪些文件",
                "",
                "1. 先打开 `index.html`，它会列出每一页图片。",
                "2. 再打开 `manual_region_mining_checklist.csv` 填写结果。",
                "3. 如果表格里图片路径打不开，可以直接到 `pages/` 文件夹找对应 PNG。",
                "",
                "## 每一行怎么填",
                "",
                "- `status`: 如果这一行填了一个有效候选，写 `accepted`；如果这一页没有更多合适区域，写 `skipped`；不确定写 `needs_help`。",
                "- `category`: 选择最接近的类别，例如 `dimension_value`、`room_label`、`instrument_tag`、`equipment_tag`、`pin_label`、`table_cell`、`drawing_note`、`unknown_microtext`。",
                "- `transcribed_text`: 按图片中实际可见文字逐字填写。不要凭上下文补全图片里看不到的字母或数字。",
                "- `bbox_xyxy`: 填区域框坐标，格式是 `x1,y1,x2,y2`，单位是当前 PNG 图片像素。框要尽量只包住目标文字，允许少量空白。",
                "- `notes`: 写不确定原因、遮挡、模糊、类别不确定、或者为什么 skipped。",
                "",
                "## 什么样的区域应该 accepted",
                "",
                "- 工程图里真实存在的小文字：尺寸、房间名、设备编号、仪表位号、管线/引脚/端子标签、表格里的关键数值等。",
                "- 图片清晰到可以独立读出文字。",
                "- 框内文字不要太长，最好是一个短标签、短数值或一个表格单元。",
                "",
                "## 什么样的区域不要收",
                "",
                "- 页眉页脚、网页导航、版权说明、目录链接、普通段落说明文字。",
                "- 截图不完整、文字被切掉、太模糊、需要猜测才能读出来。",
                "- 纯图形符号但没有可读文字。",
                "- 重复的同一个标签，除非它出现在不同工程上下文且确实有价值。",
                "",
                "## 重要要求",
                "",
                "- 不要修改文件名，不要删除图片，不要改变 CSV 列名。",
                "- 如果不会量坐标，可以先写 `needs_help` 并在 `notes` 里描述位置；但能填 bbox_xyxy 的尽量填写。",
                "- 完成后返回整个文件夹或至少返回填写后的 `manual_region_mining_checklist.csv`。",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_index(output_dir: Path, page_records: list[dict[str, Any]]) -> None:
    rows = []
    for page in page_records:
        page_image = html.escape(page["page_image"])
        doc_id = html.escape(str(page["doc_id"]))
        domain = html.escape(str(page["domain"]))
        page_index = html.escape(str(page["page_index"]))
        source_path = html.escape(str(page.get("source_path") or ""))
        rows.append(
            "<tr>"
            f"<td>{doc_id}</td>"
            f"<td>{domain}</td>"
            f"<td>{page_index}</td>"
            f"<td><a href=\"{page_image}\"><img src=\"{page_image}\" alt=\"{doc_id}\" /></a></td>"
            f"<td><code>{source_path}</code></td>"
            "</tr>"
        )
    document = "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\" />",
            "<title>Eng_Bench Manual Region Mining Packet</title>",
            "<style>",
            "body{font-family:Arial,sans-serif;margin:24px;color:#222}",
            "table{border-collapse:collapse;width:100%}",
            "th,td{border:1px solid #ccc;padding:8px;vertical-align:top}",
            "img{max-width:360px;max-height:260px;border:1px solid #999}",
            "code{white-space:pre-wrap}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Eng_Bench Manual Region Mining Packet</h1>",
            "<p>Fill <code>manual_region_mining_checklist.csv</code>. See <code>INTERN_INSTRUCTIONS_ZH.md</code>.</p>",
            "<table>",
            "<thead><tr><th>Doc ID</th><th>Domain</th><th>Page</th><th>Image</th><th>Source</th></tr></thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
            "</body>",
            "</html>",
            "",
        ]
    )
    (output_dir / "index.html").write_text(document, encoding="utf-8")


def zip_directory(source_dir: Path, zip_output: Path) -> int:
    zip_output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir).as_posix())
                count += 1
    return count


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_packet(
    *,
    root: Path,
    output_dir: Path,
    readiness_json: Path | None = None,
    zip_output: Path | None = None,
    date_label: str | None = None,
    max_docs: int = 20,
    pages_per_doc: int = 1,
    regions_per_page: int = 8,
    dpi: int = 300,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = resolve_under_root(root, output_dir)
    date_label = date_label or date.today().isoformat()
    readiness_path = readiness_json or latest_readiness_json(root)
    readiness_path = resolve_under_root(root, readiness_path)
    readiness = read_json(readiness_path)

    selected_rows, rights_blocked = selected_source_rows(readiness, max_docs=max_docs)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    page_records, checklist_rows = copy_selected_pages(
        root=root,
        output_dir=output_dir,
        selected_rows=selected_rows,
        pages_per_doc=pages_per_doc,
        regions_per_page=regions_per_page,
        dpi=dpi,
    )
    write_checklist(output_dir / "manual_region_mining_checklist.csv", checklist_rows)
    write_source_pages(output_dir / "source_pages.csv", page_records)

    totals = {
        "date_label": date_label,
        "readiness_json": rel_to(readiness_path, root),
        "selected_docs": len({page["doc_id"] for page in page_records}),
        "selected_source_rows": len(selected_rows),
        "selected_pages": len(page_records),
        "checklist_rows": len(checklist_rows),
        "rights_blocked_docs": len(rights_blocked),
        "pages_per_doc": pages_per_doc,
        "regions_per_page": regions_per_page,
        "dpi": dpi,
    }
    report = {
        "date_label": date_label,
        "totals": totals,
        "domain_coverage": dict(sorted(Counter(page["domain"] for page in page_records).items())),
        "selected_sources": [
            {
                "doc_id": row.get("doc_id", ""),
                "domain": row.get("domain", ""),
                "next_step": row.get("next_step", ""),
                "source_path": str(row.get("source_path") or "").replace("\\", "/"),
                "rendered_pages": as_int(row.get("rendered_pages")),
                "textlayer_spans": as_int(row.get("textlayer_spans")),
                "public_status": row.get("public_status", ""),
                "planning_score": as_int(row.get("planning_score")),
            }
            for row in selected_rows
        ],
        "page_records": page_records,
        "skipped_rights_status_counts": dict(
            sorted(Counter(str(row.get("public_status") or "") for row in rights_blocked).items())
        ),
        "rights_blocking_markers": list(RIGHTS_BLOCKING_MARKERS),
    }

    write_readme(output_dir, report)
    write_chinese_instructions(output_dir)
    write_index(output_dir, page_records)
    write_json(output_dir / "packet_manifest.json", report)

    if zip_output is not None:
        zip_path = resolve_under_root(root, zip_output)
        zip_entries = zip_directory(output_dir, zip_path)
        report["zip"] = {
            "path": rel_to(zip_path, root),
            "entries": zip_entries,
            "sha256": sha256_file(zip_path),
        }
        write_json(output_dir / "packet_manifest.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--readiness-json", type=Path)
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip-output", type=Path)
    parser.add_argument("--max-docs", type=int, default=20)
    parser.add_argument("--pages-per-doc", type=int, default=1)
    parser.add_argument("--regions-per-page", type=int, default=8)
    parser.add_argument("--dpi", type=int, default=300)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_packet(
        root=args.root,
        readiness_json=args.readiness_json,
        output_dir=args.output_dir,
        zip_output=args.zip_output,
        date_label=args.date_label,
        max_docs=args.max_docs,
        pages_per_doc=args.pages_per_doc,
        regions_per_page=args.regions_per_page,
        dpi=args.dpi,
    )
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    if "zip" in report:
        print(json.dumps(report["zip"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
