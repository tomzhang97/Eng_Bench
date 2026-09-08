#!/usr/bin/env python3
"""Build and finalize a self-contained review batch for provenance replacement rows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import export_review_packs


EVIDENCE_FOLDERS = {
    "crop_path": "crops",
    "page_path": "pages",
    "old_crop_path": "old",
    "new_crop_path": "new",
    "panel_path": "panels",
    "old_page_path": "pages_old",
    "new_page_path": "pages_new",
}

GENERIC_VISUAL_DESCRIPTIONS = {
    "a localized graphic or schematic-symbol difference may be present.",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def task_for(row: dict[str, Any]) -> str:
    task = str(row.get("replacement_for_task") or row.get("task") or "").strip().lower()
    if task in {"microtext", "visualdiff"}:
        return task
    return "visualdiff" if row.get("pair_id") else "microtext"


def visual_description_rewrite_required(row: dict[str, Any]) -> bool:
    description = str(row.get("description") or row.get("change_desc_gt") or "").strip()
    normalized = description.casefold()
    return (
        not description
        or "todo" in normalized
        or normalized in GENERIC_VISUAL_DESCRIPTIONS
    )


def absolute_output_under_root(root: Path, output_dir: Path) -> tuple[Path, Path]:
    root = root.resolve()
    absolute = output_dir.resolve() if output_dir.is_absolute() else (root / output_dir).resolve()
    required_parent = (root / "derived" / "human_adjudication").resolve()
    if not absolute.is_relative_to(required_parent):
        raise ValueError(f"output directory must stay under {required_parent}")
    return absolute, absolute.relative_to(root)


def localize_manifest(pack_root: Path) -> list[dict[str, Any]]:
    manifest_path = pack_root / "manifest.jsonl"
    rows = read_jsonl(manifest_path)
    localized: list[dict[str, Any]] = []
    for row in rows:
        output = dict(row)
        for field, folder in EVIDENCE_FOLDERS.items():
            value = str(output.get(field) or "").replace("\\", "/").strip()
            if value:
                output[field] = f"{folder}/{Path(value).name}"
        localized.append(output)
    write_jsonl(manifest_path, localized)
    return localized


def audit_source_rows(
    root: Path,
    rows: list[dict[str, Any]],
    assigned_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ids = [identity(row) for row in rows]
    assigned_ids = {identity(row) for row in assigned_rows if identity(row)}
    evidence_fingerprints = [
        str(row.get("replacement_evidence_fingerprint") or "").strip() for row in rows
    ]
    assigned_fingerprints = {
        str(row.get("replacement_evidence_fingerprint") or "").strip()
        for row in assigned_rows
        if str(row.get("replacement_evidence_fingerprint") or "").strip()
    }
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if key and count > 1)
    overlap_ids = sorted(set(ids) & assigned_ids)
    duplicate_evidence = sorted(
        key for key, count in Counter(evidence_fingerprints).items() if key and count > 1
    )
    assigned_evidence_overlap = sorted(set(evidence_fingerprints) & assigned_fingerprints)
    missing_ids = sum(not value for value in ids)
    missing_evidence_fingerprints = sum(not value for value in evidence_fingerprints)
    missing_images: list[str] = []
    wrong_safety: list[str] = []
    wrong_status: list[str] = []
    wrong_split: list[str] = []
    for row, row_id in zip(rows, ids):
        task = task_for(row)
        image_fields = ("image_path",) if task == "microtext" else ("image_old", "image_new")
        for field in image_fields:
            value = str(row.get(field) or "").strip()
            if not value or not (root / value).exists():
                missing_images.append(f"{row_id}:{field}:{value}")
        if row.get("safe_to_merge_gold") is not False:
            wrong_safety.append(row_id)
        if str(row.get("review_status") or "") != "needs_review":
            wrong_status.append(row_id)
        if str(row.get("replacement_for_split") or "") not in {"train", "dev", "test"}:
            wrong_split.append(row_id)
    issues: list[str] = []
    for label, values in (
        ("missing identities", [str(missing_ids)] if missing_ids else []),
        ("duplicate identities", duplicate_ids),
        ("already-assigned overlap", overlap_ids),
        ("missing evidence fingerprints", [str(missing_evidence_fingerprints)] if missing_evidence_fingerprints else []),
        ("duplicate evidence fingerprints", duplicate_evidence),
        ("already-assigned evidence overlap", assigned_evidence_overlap),
        ("missing source images", missing_images),
        ("safe_to_merge_gold must be false", wrong_safety),
        ("review_status must be needs_review", wrong_status),
        ("invalid replacement split", wrong_split),
    ):
        if values:
            preview = ", ".join(values[:10])
            issues.append(f"{label}: {preview}" + (" ..." if len(values) > 10 else ""))
    return {
        "rows": len(rows),
        "tasks": dict(Counter(task_for(row) for row in rows)),
        "splits": dict(Counter(str(row.get("replacement_for_split") or "") for row in rows)),
        "source_units": len({str(row.get("replacement_source_unit") or "") for row in rows}),
        "duplicate_identities": len(duplicate_ids),
        "already_assigned_overlap": len(overlap_ids),
        "missing_evidence_fingerprints": missing_evidence_fingerprints,
        "duplicate_evidence_fingerprints": len(duplicate_evidence),
        "already_assigned_evidence_overlap": len(assigned_evidence_overlap),
        "missing_source_images": len(missing_images),
        "wrong_safety_rows": len(wrong_safety),
        "wrong_review_status_rows": len(wrong_status),
        "invalid_split_rows": len(wrong_split),
        "issues": issues,
        "valid": not issues,
    }


def microtext_checklist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for index, row in enumerate(rows, start=1):
        output.append(
            {
                "index": index,
                "candidate_id": identity(row),
                "replacement_for_split": row.get("replacement_for_split", ""),
                "source_unit": row.get("replacement_source_unit", ""),
                "crop_path": row.get("crop_path", ""),
                "page_path": row.get("page_path", ""),
                "proposed_text": row.get("proposed_text") or row.get("target_text") or "",
                "proposed_category": row.get("category", ""),
                "decision": "",
                "corrected_text": "",
                "corrected_category": "",
                "review_notes": "",
                "reviewer_name": "",
                "reviewed_at": "",
            }
        )
    return output


def visualdiff_checklist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for index, row in enumerate(rows, start=1):
        output.append(
            {
                "index": index,
                "pair_id": identity(row),
                "replacement_for_split": row.get("replacement_for_split", ""),
                "source_unit": row.get("replacement_source_unit", ""),
                "panel_path": row.get("panel_path", ""),
                "old_crop_path": row.get("old_crop_path", ""),
                "new_crop_path": row.get("new_crop_path", ""),
                "old_page_path": row.get("old_page_path", ""),
                "new_page_path": row.get("new_page_path", ""),
                "proposed_change_type": row.get("change_type", ""),
                "proposed_description": row.get("description") or row.get("change_desc_gt") or "",
                "description_rewrite_required": bool(
                    row.get("description_rewrite_required")
                ),
                "description_task": (
                    "确有工程变化时选择 2 并重写；无变化选择 3；位置不对应或证据不足选择 4"
                    if row.get("description_rewrite_required")
                    else ""
                ),
                "decision": "",
                "corrected_change_type": "",
                "corrected_description": "",
                "review_notes": "",
                "reviewer_name": "",
                "reviewed_at": "",
            }
        )
    return output


def human_steps(
    *,
    total_rows: int,
    micro_rows: int,
    visual_rows: int,
    micro_workbook: str,
    visual_workbook: str,
    visual_rewrite_rows: int,
) -> str:
    if micro_rows and visual_rows:
        workbook_instruction = f"先完成 `{micro_workbook}`，再完成 `{visual_workbook}`。"
    elif micro_rows:
        workbook_instruction = f"完成 `{micro_workbook}`。"
    elif visual_rows:
        workbook_instruction = f"完成 `{visual_workbook}`。"
    else:
        raise ValueError("review batch must contain at least one row")
    workbook_count = int(bool(micro_rows)) + int(bool(visual_rows))
    rule_sections: list[str] = []
    if micro_rows:
        rule_sections.append(
            """## MicroText 规则

- 图片必须完整显示答案。截图少字母、只显示半个字符，但机器文字含截图外内容时，不能填 1；通常填 3，若可从完整页可靠修正则填 2。
- `pin_label`：引脚、端子、元件或连接器标签，例如 `GPIO14`、`J3`、`Q1`、`SCL`。
- `dimension_value`：尺寸、角度、半径、公差，例如 `10'-0\"`、`R0.8`、`4.00°`。
- `instrument_tag`：仪表回路标签，例如 `FT-101`、`PIT-202`。
- 被划掉或删除线文字只有在图纸语义上仍需要识读时才保留；纯修订痕迹或无效残留填 3。
"""
        )
    if visual_rows:
        rule_sections.append(
            """## VisualDiff 规则

- 每张证据图顶部固定标出红色 `OLD` 和青色 `NEW`。某一边空白可以表示该版本确实没有内容，不等于缺图。
- `1` 要求框内存在真实工程变化，并且机器类型和描述都正确。
- 两图完全相同、只有轻微整体偏移、抗锯齿/字体/清晰度变化、变化只在框外：填 `3`。
- 只有文字改变也可以是有效工程变化，例如尺寸、标签、房间名、工程注释含义改变。
- `layout` 只用于某个具体对象相对周围对象真实移动；整张图统一偏移不是 layout。
- OLD/NEW 不是同一位置或同一工程对象时，不能推断变化，填 `4` 并在备注写“位置不对应”。红框位置不同本身不算工程变化。
- 常用类型：`text` 文字替换，`addition` 新增，`deletion` 删除，`symbol` 符号/元件变化，`geometry` 几何/连线变化，`layout` 对象位置变化，`value` 数值变化。
"""
        )
    rules = "\n".join(rule_sections)
    return f"""# Eng_Bench Gold v2.0 Global 来源替换优先审核说明

本批次共 {total_rows} 条（MicroText {micro_rows} 条，VisualDiff {visual_rows} 条），只用于替换当前 Gold 中来源授权不足的行。**任何一条在人工审核和机器门禁完成前都不能进入 Gold。**

## 最快操作

1. {workbook_instruction}
2. 每行只在黄色“决定”列填数字：`1`、`2`、`3` 或 `4`。
3. 只有填 `2` 时，才填写右侧改正内容；其他情况不要改机器字段。
4. 不排序、不删除行、不改 ID。完成后按 `Ctrl+S`，原文件名交回。
5. 需要看更大范围时，打开对应 `review_packs/.../index.html`，点击 full page。

## 四个决定

- `1 = accepted`：样本有效，图片证据完整，机器文字/类别或差异类型/描述全部正确。
- `2 = edited`：样本有效，但机器内容有错。必须填写改正文字、类别、差异类型或描述。
- `3 = rejected`：样本本身无效，例如裁剪不完整、不是工程标签、OLD/NEW 没有真实工程变化。
- `4 = unclear`：现有证据无法可靠判断，需要项目负责人二次裁决。

{rules}

## VisualDiff 强制描述任务

VisualDiff 中有 `{visual_rewrite_rows}` 条机器描述为占位符或通用提示。这些行在工作簿中标为
`【必须人工重写】`。如果证据显示确有工程变化，选择 `2`，并同时填写规范变化类型和具体工程变化描述；
如果没有工程变化，选择 `3`；如果 OLD/NEW 位置不对应或证据不足，选择 `4` 并说明原因。由于机器描述不是可直接接受的最终答案，选择 `1` 不会显示完成。
描述必须说明红框内具体对象发生了什么变化，不能只写“有变化”或“符号不同”。

## 交回文件

只交回上述 {workbook_count} 个填写后的 XLSX。CSV 是备用记录，不需要同时填写。不要把 `manifest.jsonl` 当作人工答案文件。
"""


def build_batch(
    *,
    root: Path,
    input_path: Path,
    already_assigned_path: Path,
    output_dir: Path,
    date_label: str,
    pad_px: int,
) -> dict[str, Any]:
    root = root.resolve()
    output_root, output_rel = absolute_output_under_root(root, output_dir)
    rows = read_jsonl(input_path if input_path.is_absolute() else root / input_path)
    assigned = read_jsonl(
        already_assigned_path if already_assigned_path.is_absolute() else root / already_assigned_path
    )
    audit = audit_source_rows(root, rows, assigned)
    if not audit["valid"]:
        raise ValueError("; ".join(audit["issues"]))

    micro = [row for row in rows if task_for(row) == "microtext"]
    visual = [row for row in rows if task_for(row) == "visualdiff"]
    for row in visual:
        row["description_rewrite_required"] = visual_description_rewrite_required(row)
    visual_rewrite_rows = sum(
        bool(row.get("description_rewrite_required")) for row in visual
    )
    output_root.mkdir(parents=True, exist_ok=True)
    review_root = output_root / "review_packs"
    micro_name = f"microtext_{len(micro)}"
    visual_name = f"visualdiff_{len(visual)}"
    micro_rel = output_rel / "review_packs" / micro_name
    visual_rel = output_rel / "review_packs" / visual_name

    micro_stats = (
        export_review_packs.export_microtext_pack(
            root, micro, micro_rel, pad_px=pad_px, max_image_pixels=1_000_000_000
        )
        if micro
        else {"rows": 0, "skipped_empty_task": True}
    )
    visual_stats = (
        export_review_packs.export_visualdiff_pack(
            root, visual, visual_rel, pad_px=pad_px, max_image_pixels=1_000_000_000
        )
        if visual
        else {"rows": 0, "skipped_empty_task": True}
    )
    if micro_stats["rows"] != len(micro) or visual_stats["rows"] != len(visual):
        raise RuntimeError(
            f"evidence export incomplete: micro {micro_stats['rows']}/{len(micro)}, "
            f"visual {visual_stats['rows']}/{len(visual)}"
        )

    micro_manifest = localize_manifest(review_root / micro_name) if micro else []
    visual_manifest = localize_manifest(review_root / visual_name) if visual else []
    micro_checklist_name = "microtext_validation_checklist.csv"
    visual_checklist_name = "visualdiff_validation_checklist.csv"
    if micro:
        write_csv(
            review_root / micro_name / micro_checklist_name,
            [
                "index", "candidate_id", "replacement_for_split", "source_unit", "crop_path",
                "page_path", "proposed_text", "proposed_category", "decision", "corrected_text",
                "corrected_category", "review_notes", "reviewer_name", "reviewed_at",
            ],
            microtext_checklist(micro_manifest),
        )
    if visual:
        write_csv(
            review_root / visual_name / visual_checklist_name,
            [
                "index", "pair_id", "replacement_for_split", "source_unit", "panel_path",
                "old_crop_path", "new_crop_path", "old_page_path", "new_page_path",
                "proposed_change_type", "proposed_description", "decision", "corrected_change_type",
                "description_rewrite_required", "description_task", "corrected_description",
                "review_notes", "reviewer_name", "reviewed_at",
            ],
            visualdiff_checklist(visual_manifest),
        )

    workbook_payload = {
        "goal": "Eng_Bench Gold v2.0 Global",
        "date_label": date_label,
        "batch_root": output_root.as_posix(),
        "microtext": {
            "rows": len(micro_manifest),
            "manifest": (review_root / micro_name / "manifest.jsonl").as_posix(),
            "workbook": f"01_MicroText_{len(micro_manifest)}.xlsx",
            "sheet": f"MicroText审核{len(micro_manifest)}条",
        },
        "visualdiff": {
            "rows": len(visual_manifest),
            "manifest": (review_root / visual_name / "manifest.jsonl").as_posix(),
            "workbook": f"02_VisualDiff_{len(visual_manifest)}.xlsx",
            "sheet": f"VisualDiff审核{len(visual_manifest)}条",
        },
    }
    write_json(output_root / "workbook_build_payload.json", workbook_payload)

    input_abs = input_path if input_path.is_absolute() else root / input_path
    source_jsonl = input_abs.relative_to(root).as_posix()
    manifest_rows = []
    if micro:
        manifest_rows.append({
            "pack_name": micro_name,
            "kind": "microtext",
            "task": "microtext",
            "rows": len(micro_manifest),
            "checklist": micro_checklist_name,
            "source_jsonl": source_jsonl,
            "workbook": workbook_payload["microtext"]["workbook"],
            "split_counts": json.dumps(Counter(row["replacement_for_split"] for row in micro), sort_keys=True),
            "safe_to_merge_gold": "false",
        })
    if visual:
        manifest_rows.append({
            "pack_name": visual_name,
            "kind": "visualdiff",
            "task": "visualdiff",
            "rows": len(visual_manifest),
            "checklist": visual_checklist_name,
            "source_jsonl": source_jsonl,
            "workbook": workbook_payload["visualdiff"]["workbook"],
            "split_counts": json.dumps(Counter(row["replacement_for_split"] for row in visual), sort_keys=True),
            "safe_to_merge_gold": "false",
        })
    write_csv(
        output_root / "NEXT_REVIEW_BATCH_MANIFEST.csv",
        [
            "pack_name", "kind", "task", "rows", "checklist", "source_jsonl",
            "workbook", "split_counts", "safe_to_merge_gold",
        ],
        manifest_rows,
    )

    active_hashes = {
        name: sha256(root / name)
        for name in ("eng_bench.jsonl", "manifest.jsonl")
        if (root / name).exists()
    }
    report = {
        "date_label": date_label,
        "goal": "Eng_Bench Gold v2.0 Global",
        "input": input_abs.relative_to(root).as_posix(),
        "input_sha256": sha256(input_abs),
        "audit": audit,
        "counts": {
            "total": len(rows),
            "microtext": len(micro),
            "visualdiff": len(visual),
            "microtext_splits": dict(Counter(row["replacement_for_split"] for row in micro)),
            "visualdiff_splits": dict(Counter(row["replacement_for_split"] for row in visual)),
            "match_levels": dict(Counter(str(row.get("replacement_match_level") or "") for row in rows)),
            "visualdiff_description_rewrite_required": visual_rewrite_rows,
        },
        "pack_stats": {
            "microtext": dict(micro_stats),
            "visualdiff": dict(visual_stats),
        },
        "active_release_hashes_before_and_after_build": active_hashes,
        "promotion_policy": {
            "human_review_required": True,
            "safe_to_merge_gold": False,
            "active_gold_modified": False,
        },
        "workbooks_pending": True,
    }
    write_json(output_root / "next_review_batch_build_report.json", report)
    report_md = "\n".join(
        [
            "# Provenance Replacement Review Batch",
            "",
            f"- Goal: `Eng_Bench Gold v2.0 Global`",
            f"- Rows: `{len(rows)}` (`{len(micro)}` MicroText, `{len(visual)}` VisualDiff)",
            f"- New/assigned overlap: `{audit['already_assigned_overlap']}`",
            f"- Missing source images: `{audit['missing_source_images']}`",
            "- Gold promotion: `blocked until human review and strict machine gates pass`",
            "- Active Gold modified by this build: `false`",
            "",
        ]
    )
    (output_root / "next_review_batch_build_report.md").write_text(report_md, encoding="utf-8")
    (output_root / "HUMAN_REVIEW_STEPS.md").write_text(
        human_steps(
            total_rows=len(rows),
            micro_rows=len(micro),
            visual_rows=len(visual),
            micro_workbook=workbook_payload["microtext"]["workbook"],
            visual_workbook=workbook_payload["visualdiff"]["workbook"],
            visual_rewrite_rows=visual_rewrite_rows,
        ),
        encoding="utf-8",
    )
    workbook_count = int(bool(micro)) + int(bool(visual))
    (output_root / "README.md").write_text(
        "\n".join(
            [
                "# Eng_Bench Gold v2.0 Provenance Replacement Priority Review",
                "",
                "Open `HUMAN_REVIEW_STEPS.md` first.",
                f"Review the {workbook_count} XLSX workbook(s) after the workbook build step completes.",
                "The `review_packs/` folders contain portable crop, panel, and full-page evidence.",
                f"All {len(rows)} rows remain unreviewed and non-mergeable until strict post-return validation.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return report


def workbook_media_count(path: Path) -> int:
    with zipfile.ZipFile(path, "r") as archive:
        if archive.testzip() is not None:
            raise ValueError(f"invalid XLSX ZIP member in {path.name}")
        return sum(name.startswith("xl/media/") for name in archive.namelist())


def finalize_batch(*, root: Path, output_dir: Path, zip_output: Path) -> dict[str, Any]:
    root = root.resolve()
    output_root, _ = absolute_output_under_root(root, output_dir)
    payload = json.loads((output_root / "workbook_build_payload.json").read_text(encoding="utf-8"))
    expected = {
        task_payload["workbook"]: int(task_payload["rows"])
        for task_payload in (payload.get("microtext"), payload.get("visualdiff"))
        if task_payload and int(task_payload.get("rows") or 0) > 0
    }
    if not expected:
        raise ValueError("review batch contains no non-empty workbooks")
    media_counts = {}
    for workbook_name, expected_rows in expected.items():
        workbook_path = output_root / workbook_name
        if not workbook_path.exists():
            raise FileNotFoundError(f"missing workbook {workbook_path}")
        media_counts[workbook_name] = workbook_media_count(workbook_path)
        if media_counts[workbook_name] != expected_rows:
            raise ValueError(
                f"{workbook_name}: expected {expected_rows} embedded images, "
                f"found {media_counts[workbook_name]}"
            )
    if any(path.suffix.lower() == ".zip" for path in output_root.rglob("*")):
        raise ValueError("nested ZIP found inside batch folder")

    finalization = {
        "goal": "Eng_Bench Gold v2.0 Global",
        "workbook_media_counts": media_counts,
        "nested_zips": 0,
        "safe_to_merge_gold": False,
    }
    write_json(output_root / "FINALIZATION_REPORT.json", finalization)

    inventory_rows = []
    excluded = {"FILE_INVENTORY.csv", "SHA256SUMS.txt"}
    for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
        relative = path.relative_to(output_root).as_posix()
        if relative in excluded:
            continue
        inventory_rows.append(
            {"relative_path": relative, "size_bytes": path.stat().st_size, "sha256": sha256(path)}
        )
    write_csv(
        output_root / "FILE_INVENTORY.csv",
        ["relative_path", "size_bytes", "sha256"],
        inventory_rows,
    )
    (output_root / "SHA256SUMS.txt").write_text(
        "".join(f"{row['sha256']}  {row['relative_path']}\n" for row in inventory_rows),
        encoding="ascii",
    )

    zip_path = zip_output.resolve() if zip_output.is_absolute() else (root / zip_output).resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
            archive.write(path, (Path(output_root.name) / path.relative_to(output_root)).as_posix())
    with zipfile.ZipFile(zip_path, "r") as archive:
        bad_member = archive.testzip()
        entries = archive.namelist()
    if bad_member is not None:
        raise ValueError(f"ZIP CRC failed at {bad_member}")
    if any(name.lower().endswith(".zip") for name in entries):
        raise ValueError("nested ZIP entry found after finalization")
    return {
        **finalization,
        "zip_path": zip_path.as_posix(),
        "zip_sha256": sha256(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "zip_entries": len(entries),
        "zip_crc_valid": True,
        "inventory_files": len(inventory_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build portable evidence packs and workbook payload")
    build.add_argument("--root", default=".")
    build.add_argument(
        "--input",
        default="derived/review_queues/v2_0_provenance_replacement_new_review_2026-08-11-wave100.jsonl",
    )
    build.add_argument(
        "--already-assigned",
        default="derived/review_queues/v2_0_provenance_replacement_already_assigned_2026-08-11-wave100.jsonl",
    )
    build.add_argument(
        "--output-dir",
        default="derived/human_adjudication/2026-08-11_v2_0_provenance_replacement_priority_670",
    )
    build.add_argument("--date-label", default="2026-08-11-wave100")
    build.add_argument("--pad-px", type=int, default=28)

    finalize = subparsers.add_parser("finalize", help="Verify workbooks and create a flat delivery ZIP")
    finalize.add_argument("--root", default=".")
    finalize.add_argument(
        "--output-dir",
        default="derived/human_adjudication/2026-08-11_v2_0_provenance_replacement_priority_670",
    )
    finalize.add_argument(
        "--zip-output",
        default="derived/human_adjudication/Eng_Bench_v2_0_Provenance_Replacement_Priority_670_2026-08-11.zip",
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        report = build_batch(
            root=Path(args.root),
            input_path=Path(args.input),
            already_assigned_path=Path(args.already_assigned),
            output_dir=Path(args.output_dir),
            date_label=args.date_label,
            pad_px=args.pad_px,
        )
    else:
        report = finalize_batch(
            root=Path(args.root),
            output_dir=Path(args.output_dir),
            zip_output=Path(args.zip_output),
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
