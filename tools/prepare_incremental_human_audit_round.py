#!/usr/bin/env python3
"""Prepare a release-safe deferred primary pool and the next auditor round."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageOps


AUDITOR_NUMBERS = [1, 2, 3, 4, 6, 7, 8, 9, 11, 12]
RETURNING_AUDITORS = {1, 2, 3, 4, 6, 7, 8, 9}
UNTOUCHED_AUDITORS = [5, 10]
MICRO_TARGETS = {"train": 300, "dev": 300, "test": 300}
VISUAL_TARGET = 600
PRIMARY_TARGET = 1500


def sha256_file(path: Path) -> str:
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def identifier(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or "").strip()


def task(row: dict[str, Any]) -> str:
    return "visualdiff" if row.get("pair_id") else "microtext"


def split_name(row: dict[str, Any]) -> str:
    return str(row.get("reserved_split") or row.get("split") or "").strip()


def visual_family(row: dict[str, Any]) -> str:
    project_id = str(row.get("project_id") or "").strip()
    if project_id:
        return project_id
    value = identifier(row)
    return value.split("__p", 1)[0] if "__p" in value else value


def source_group(row: dict[str, Any]) -> str:
    if task(row) == "visualdiff":
        return visual_family(row)
    return str(row.get("doc_id") or row.get("source_payload_sha256") or identifier(row))


def bbox_value(row: dict[str, Any], key: str) -> list[int] | None:
    value = row.get(key)
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(round(float(item))) for item in value]
    except (TypeError, ValueError):
        return None


def local_path(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else root / path


def evidence_materializable(root: Path, row: dict[str, Any]) -> bool:
    if task(row) == "microtext":
        return (
            bool(bbox_value(row, "bbox"))
            and local_path(root, row.get("image_path")).is_file()
        )
    return (
        bool(bbox_value(row, "bbox_old"))
        and bool(bbox_value(row, "bbox_new"))
        and local_path(root, row.get("image_old")).is_file()
        and local_path(root, row.get("image_new")).is_file()
    )


def load_capacity_rows(
    root: Path,
    report_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    current: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    for cohort in report.get("cohorts", []):
        cohort_path = root / str(cohort["path"])
        for row in read_jsonl(cohort_path):
            enriched = dict(row)
            enriched["primary_pool_capacity_cohort"] = str(cohort["name"])
            enriched["primary_pool_capacity_path"] = str(cohort["path"])
            target = current if cohort.get("phase") == "current" else future
            target.append(enriched)
    return current, future, report


def select_diverse(
    rows: list[dict[str, Any]],
    count: int,
    group: Callable[[dict[str, Any]], str] = source_group,
) -> list[dict[str, Any]]:
    buckets: dict[str, deque[dict[str, Any]]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[group(row)].append(row)
    for key, values in grouped.items():
        buckets[key] = deque(sorted(values, key=identifier))
    selected: list[dict[str, Any]] = []
    while len(selected) < count and buckets:
        for key in sorted(list(buckets)):
            if len(selected) >= count:
                break
            selected.append(buckets[key].popleft())
            if not buckets[key]:
                del buckets[key]
    if len(selected) != count:
        raise ValueError(f"requested {count} rows but only selected {len(selected)}")
    return selected


def active_gold_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    for path in (
        root / "microtext/annotations/microtext_items.jsonl",
        root / "visualdiff/annotations/visualdiff_pairs.jsonl",
    ):
        for row in read_jsonl(path):
            for key in ("candidate_id", "item_id", "pair_id", "id"):
                value = str(row.get(key) or "").strip()
                if value:
                    ids.add(value)
    return ids


def select_primary_pool(
    root: Path,
    current: list[dict[str, Any]],
    future: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fixed = [row for row in current if evidence_materializable(root, row)]
    if len(fixed) != len(current):
        raise ValueError(f"current cohort has {len(current) - len(fixed)} evidence-blocked rows")
    if len(fixed) != 258:
        raise ValueError(f"expected 258 release-safe current rows, found {len(fixed)}")

    future_ready = [row for row in future if evidence_materializable(root, row)]
    fixed_ids = {identifier(row) for row in fixed}
    future_ready = [row for row in future_ready if identifier(row) not in fixed_ids]
    selected = list(fixed)

    fixed_micro = Counter(
        split_name(row) for row in fixed if task(row) == "microtext"
    )
    for split, target in MICRO_TARGETS.items():
        needed = target - fixed_micro.get(split, 0)
        candidates = [
            row
            for row in future_ready
            if task(row) == "microtext" and split_name(row) == split
        ]
        selected.extend(select_diverse(candidates, needed))

    fixed_visual = [row for row in fixed if task(row) == "visualdiff"]
    needed_visual = VISUAL_TARGET - len(fixed_visual)
    visual_candidates = [
        row
        for row in future_ready
        if task(row) == "visualdiff" and split_name(row) == "test"
    ]
    selected.extend(select_diverse(visual_candidates, needed_visual, visual_family))

    ids = [identifier(row) for row in selected]
    if len(selected) != PRIMARY_TARGET or len(set(ids)) != PRIMARY_TARGET:
        raise ValueError(
            f"primary pool must contain {PRIMARY_TARGET} unique rows; "
            f"found {len(selected)} rows / {len(set(ids))} IDs"
        )
    overlaps = sorted(set(ids) & active_gold_ids(root))
    if overlaps:
        raise ValueError(f"primary pool overlaps active gold: {overlaps[:10]}")
    return selected


def previous_auditor_ids(payload_path: Path) -> set[str]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    return {
        identifier(row)
        for auditor in payload.get("auditors", [])
        for row in auditor.get("rows", [])
        if identifier(row)
    }


def auditor_candidates(
    pool: list[dict[str, Any]],
    previous_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    additions = [
        row
        for row in pool
        if row.get("primary_pool_capacity_cohort") != "primary_rights_cleared_258"
        and identifier(row) not in previous_ids
    ]
    specs = {
        "micro_train": ("microtext", "train", 30),
        "micro_dev": ("microtext", "dev", 30),
        "micro_test": ("microtext", "test", 30),
        "visual_test": ("visualdiff", "test", 30),
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for name, (task_name, split, count) in specs.items():
        candidates = [
            row
            for row in additions
            if task(row) == task_name and split_name(row) == split
        ]
        result[name] = select_diverse(
            candidates,
            count,
            visual_family if task_name == "visualdiff" else source_group,
        )
    ids = [identifier(row) for values in result.values() for row in values]
    if len(ids) != 120 or len(set(ids)) != 120:
        raise ValueError("next auditor assignments must contain 120 unique rows")
    return result


def clamped_bbox(image: Image.Image, bbox: list[int], padding: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(x1, x2) - padding),
        max(0, min(y1, y2) - padding),
        min(image.width, max(x1, x2) + padding),
        min(image.height, max(y1, y2) + padding),
    )


def paste_contained(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    fitted = ImageOps.contain(
        image,
        (max(1, right - left), max(1, bottom - top)),
        method=Image.Resampling.LANCZOS,
    )
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    canvas.paste(fitted, (x, y))


def draw_label(draw: ImageDraw.ImageDraw, x: int, text: str) -> None:
    draw.rectangle((x, 0, x + 125, 30), fill="#155E63")
    draw.text((x + 10, 7), text, fill="white")


def render_micro_evidence(root: Path, row: dict[str, Any], output: Path) -> None:
    page = Image.open(local_path(root, row["image_path"])).convert("RGB")
    bbox = bbox_value(row, "bbox")
    assert bbox is not None
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    target_box = clamped_bbox(page, bbox, max(18, min(48, max(width, height) // 3)))
    context_pad = max(140, min(420, max(width, height) * 3))
    context_box = clamped_bbox(page, bbox, context_pad)
    target = page.crop(target_box)
    context = page.crop(context_box)
    context_draw = ImageDraw.Draw(context)
    context_draw.rectangle(
        (
            bbox[0] - context_box[0],
            bbox[1] - context_box[1],
            bbox[2] - context_box[0],
            bbox[3] - context_box[1],
        ),
        outline="#DC2626",
        width=5,
    )
    canvas = Image.new("RGB", (1100, 320), "white")
    draw = ImageDraw.Draw(canvas)
    draw_label(draw, 20, "TARGET")
    draw_label(draw, 570, "CONTEXT")
    paste_contained(canvas, target, (20, 38, 540, 305))
    paste_contained(canvas, context, (570, 38, 1080, 305))
    draw.rectangle((18, 36, 542, 307), outline="#CBD5E1", width=2)
    draw.rectangle((568, 36, 1082, 307), outline="#CBD5E1", width=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)


def render_visual_evidence(root: Path, row: dict[str, Any], output: Path) -> None:
    panels: list[Image.Image] = []
    for image_key, bbox_key in (("image_old", "bbox_old"), ("image_new", "bbox_new")):
        page = Image.open(local_path(root, row[image_key])).convert("RGB")
        bbox = bbox_value(row, bbox_key)
        assert bbox is not None
        width = max(1, bbox[2] - bbox[0])
        height = max(1, bbox[3] - bbox[1])
        padding = max(56, min(300, max(width, height) * 2))
        crop_box = clamped_bbox(page, bbox, padding)
        panel = page.crop(crop_box)
        panel_draw = ImageDraw.Draw(panel)
        panel_draw.rectangle(
            (
                bbox[0] - crop_box[0],
                bbox[1] - crop_box[1],
                bbox[2] - crop_box[0],
                bbox[3] - crop_box[1],
            ),
            outline="#DC2626",
            width=5,
        )
        panels.append(panel)
    canvas = Image.new("RGB", (1100, 360), "white")
    draw = ImageDraw.Draw(canvas)
    draw_label(draw, 20, "OLD")
    draw_label(draw, 570, "NEW")
    paste_contained(canvas, panels[0], (20, 38, 540, 345))
    paste_contained(canvas, panels[1], (570, 38, 1080, 345))
    draw.rectangle((18, 36, 542, 347), outline="#CBD5E1", width=2)
    draw.rectangle((568, 36, 1082, 347), outline="#CBD5E1", width=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)


def machine_suggestion(row: dict[str, Any]) -> str:
    if task(row) == "microtext":
        text = str(row.get("proposed_text") or row.get("target_text") or "").strip()
        category = str(row.get("category") or "unknown_microtext").strip()
        return f"{text}\n类别：{category}"
    change_type = str(row.get("change_type") or "visual").strip()
    if not change_type.endswith("candidate"):
        change_type = f"{change_type}_change_candidate"
    old_text = str(row.get("old_text") or "").strip() or "(空)"
    new_text = str(row.get("new_text") or "").strip() or "(空)"
    return f"{change_type}\n机器文字：{old_text} -> {new_text}"


def payload_row(row: dict[str, Any], index: int, evidence_path: str = "") -> dict[str, Any]:
    value: dict[str, Any] = {
        "task": task(row),
        "display_index": str(index),
        "primary_index": str(row["primary_pool_index"]),
        "machine_suggestion": machine_suggestion(row),
        "evidence_path": evidence_path,
        "reserved_split": split_name(row),
        "capacity_cohort": row.get("primary_pool_capacity_cohort", ""),
        "safe_to_merge_gold": False,
    }
    if task(row) == "microtext":
        value.update(
            {
                "candidate_id": identifier(row),
                "corrected_text": "",
                "corrected_category": "",
                "full_page_path": str(row.get("image_path") or ""),
            }
        )
    else:
        value.update(
            {
                "pair_id": identifier(row),
                "change_description": str(
                    row.get("human_description")
                    or row.get("change_desc_gt")
                    or row.get("description")
                    or ""
                ),
                "old_page_path": str(row.get("image_old") or ""),
                "new_page_path": str(row.get("image_new") or ""),
            }
        )
    return value


def assign_auditors(
    root: Path,
    pool: list[dict[str, Any]],
    strata: dict[str, list[dict[str, Any]]],
    evidence_dir: Path,
) -> list[dict[str, Any]]:
    by_id = {identifier(row): row for row in pool}
    auditors: list[dict[str, Any]] = []
    for position, number in enumerate(AUDITOR_NUMBERS):
        assigned: list[dict[str, Any]] = []
        for key in ("micro_train", "micro_dev", "micro_test", "visual_test"):
            assigned.extend(strata[key][position * 3 : position * 3 + 3])
        payload_rows: list[dict[str, Any]] = []
        for display_index, row in enumerate(assigned, start=1):
            name = f"auditor_{number:02d}_{display_index:02d}_{identifier(row)}.png"
            evidence_path = evidence_dir / name
            if task(row) == "microtext":
                render_micro_evidence(root, row, evidence_path)
            else:
                render_visual_evidence(root, row, evidence_path)
            payload_rows.append(payload_row(row, display_index, evidence_path.resolve().as_posix()))
            by_id[identifier(row)]["auditor_assignment"] = number
            by_id[identifier(row)]["auditor_evidence_path"] = evidence_path.relative_to(root).as_posix()
        round_number = 2 if number in RETURNING_AUDITORS else 1
        auditors.append(
            {
                "number": number,
                "round": round_number,
                "workbook": f"AUDITOR_{number:02d}_ROUND{round_number}_REVIEW_12.xlsx",
                "rows": payload_rows,
            }
        )
    return auditors


def guide_text(auditors: list[dict[str, Any]]) -> str:
    assignments = "\n".join(
        f"- 复核员 {row['number']:02d}：{row['workbook']}" for row in auditors
    )
    return f"""Eng_Bench Gold v2.0 Global - 新一轮独立复核说明

负责人先看：
1. 本包只发给 10 位复核员：01、02、03、04、06、07、08、09、11、12。
2. 05 和 10 尚未交回上一轮，本包没有他们的新任务，也不要催促他们改做本轮。
3. 每人只发与编号相同的一个 XLSX，不要把整个 ZIP 转发给单个复核员。
4. 本轮全部是新样本，不能复制上一轮答案。

文件分配：
{assignments}

给复核员的最短操作：
1. 用桌面版 Excel/WPS 打开自己的 XLSX。
2. 每行只看 B 列证据图和 C 列问题，在黄色 D 列输入：1=是，2=否，3=看不清。
3. 完成 12/12 后 Ctrl+S，保持原文件名，只交回这个 XLSX。

MicroText 判断：
- 选 1：截图完整，文字逐字符正确，类别也正确。
- 选 2：缺字/截断、文字错误、类别错误、不是有效工程标签，或标签被明确删除线划掉。
- 选 3：图像模糊或证据不足，确实无法判断。不要猜。
- 类别例子：尺寸值=11.8°、R10、3'-3"；设备标签=MOTOR、PUMP；引脚/端子/元件=L1、Q3、GP14；管线标签=DRAIN、PG05001-6"；房间/区域=DECK、KITCHEN。
- R10 必须结合上下文：在尺寸线/圆弧旁通常是尺寸值，在电气元件旁通常是元件标签。

VisualDiff 判断：
- 选 1：OLD/NEW 红框内有真实工程含义变化，包括尺寸、标签、房名、设备、线路、符号或工程注释含义改变。
- 选 2：两图相同、只有轻微整体偏移、字体/位置/清晰度/渲染变化，或真正变化只在红框外。
- 选 3：OLD 或 NEW 缺失、太模糊，或看不出是否为工程变化。

重要：这些是独立复核结果，不会自动写入 Gold。主审结论和所有机器门禁通过后才可晋级。
"""


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Incremental Human Audit Round",
            "",
            "- Goal: **Gold v2.0 Global**",
            f"- Valid: `{str(report['valid']).lower()}`",
            f"- Deferred primary pool: `{report['primary_pool_rows']}/1500`",
            f"- Primary workbook issued: `{str(report['primary_workbook_issued']).lower()}`",
            f"- Auditor workbooks planned: `{report['auditors']}`",
            f"- New audit rows: `{report['auditor_rows']}`",
            f"- Unreturned auditors left untouched: `{report['untouched_auditors']}`",
            f"- Gold rows modified: `{report['gold_rows_modified']}`",
            "",
        ]
    )


def prepare(
    root: Path,
    capacity_report_path: Path,
    previous_payload_path: Path,
    evidence_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str]:
    current, future, capacity_report = load_capacity_rows(root, capacity_report_path)
    pool = select_primary_pool(root, current, future)
    for index, row in enumerate(pool, start=1):
        row["primary_pool_index"] = index
        row["primary_pool_status"] = "deferred_not_issued"
        row["primary_pool_target"] = "Gold v2.0 Global"
        row["safe_to_merge_gold"] = False
    previous_ids = previous_auditor_ids(previous_payload_path)
    strata = auditor_candidates(pool, previous_ids)
    auditors = assign_auditors(root, pool, strata, evidence_dir)
    primary_rows = [payload_row(row, int(row["primary_pool_index"])) for row in pool]

    task_counts = Counter(task(row) for row in pool)
    split_counts = Counter(split_name(row) for row in pool)
    pool_ids = {identifier(row) for row in pool}
    auditor_ids = {
        row_identifier
        for auditor in auditors
        for row_identifier in (identifier(row) for row in auditor["rows"])
    }
    source_payloads = {
        str(row.get("source_payload_sha256") or row.get("replacement_evidence_fingerprint") or "")
        for row in pool
        if row.get("source_payload_sha256") or row.get("replacement_evidence_fingerprint")
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "deferred release-safe primary pool plus incremental independent audit",
        "primary": {
            "workbook": "PRIMARY_REVIEW_1500_NOT_ISSUED.xlsx",
            "issued": False,
            "rows": primary_rows,
        },
        "auditors": auditors,
        "counts": {
            "primary": len(primary_rows),
            "primary_microtext": task_counts["microtext"],
            "primary_visualdiff": task_counts["visualdiff"],
            "auditors": len(auditors),
            "auditor_rows": sum(len(auditor["rows"]) for auditor in auditors),
            "unique_auditor_rows": len(auditor_ids),
        },
        "returned_auditors_advanced": sorted(RETURNING_AUDITORS),
        "new_auditors": [11, 12],
        "untouched_unreturned_auditors": UNTOUCHED_AUDITORS,
        "previous_auditor_assignments_excluded": len(previous_ids),
        "gold_rows_modified": 0,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "capacity_report": capacity_report_path.relative_to(root).as_posix(),
        "capacity_report_clean": bool(capacity_report.get("capacity_input_clean")),
        "primary_pool_rows": len(pool),
        "primary_pool_unique_ids": len(pool_ids),
        "primary_pool_task_counts": dict(sorted(task_counts.items())),
        "primary_pool_split_counts": dict(sorted(split_counts.items())),
        "primary_pool_source_docs": len(
            {
                str(row.get("doc_id") or row.get("old_doc_id") or "")
                for row in pool
                if row.get("doc_id") or row.get("old_doc_id")
            }
        ),
        "primary_pool_source_payload_keys": len(source_payloads),
        "primary_pool_visualdiff_families": len(
            {visual_family(row) for row in pool if task(row) == "visualdiff"}
        ),
        "current_release_safe_rows_retained": len(current),
        "future_rows_added": len(pool) - len(current),
        "primary_workbook_issued": False,
        "auditors": len(auditors),
        "auditor_rows": sum(len(auditor["rows"]) for auditor in auditors),
        "unique_auditor_rows": len(auditor_ids),
        "auditor_task_counts": {
            "microtext": sum(
                row["task"] == "microtext" for auditor in auditors for row in auditor["rows"]
            ),
            "visualdiff": sum(
                row["task"] == "visualdiff" for auditor in auditors for row in auditor["rows"]
            ),
        },
        "auditor_split_counts": dict(
            sorted(
                Counter(
                    row["reserved_split"] for auditor in auditors for row in auditor["rows"]
                ).items()
            )
        ),
        "returned_auditors_advanced": sorted(RETURNING_AUDITORS),
        "new_auditors": [11, 12],
        "untouched_auditors": UNTOUCHED_AUDITORS,
        "previous_auditor_rows_excluded": len(previous_ids),
        "evidence_images": len(list(evidence_dir.glob("*.png"))),
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "valid": (
            len(pool) == PRIMARY_TARGET
            and len(pool_ids) == PRIMARY_TARGET
            and task_counts == Counter({"microtext": 900, "visualdiff": 600})
            and len(auditor_ids) == 120
            and len(list(evidence_dir.glob("*.png"))) == 120
        ),
        "interpretation": (
            "The primary pool is selection-ready but intentionally not issued. Auditor rows "
            "are independent QC assignments and cannot directly promote gold."
        ),
    }
    return pool, payload, report, guide_text(auditors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--capacity-report", required=True, type=Path)
    parser.add_argument("--previous-payload", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--pool-jsonl", required=True, type=Path)
    parser.add_argument("--payload-json", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--report-md", required=True, type=Path)
    parser.add_argument("--guide-output", required=True, type=Path)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        default=None,
        help="Opt-in Pillow safety ceiling for audited engineering raster pages.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.max_image_pixels is not None:
        if args.max_image_pixels <= 0:
            raise ValueError("--max-image-pixels must be positive")
        Image.MAX_IMAGE_PIXELS = args.max_image_pixels
    capacity_report = (root / args.capacity_report).resolve()
    previous_payload = (root / args.previous_payload).resolve()
    evidence_dir = (root / args.evidence_dir).resolve()
    ensure_child(evidence_dir, root / "derived/tmp")
    if evidence_dir.exists():
        if not args.overwrite:
            raise FileExistsError(evidence_dir)
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)

    pool, payload, report, guide = prepare(
        root, capacity_report, previous_payload, evidence_dir
    )
    pool_path = root / args.pool_jsonl
    payload_path = root / args.payload_json
    report_path = root / args.report_json
    report_md_path = root / args.report_md
    guide_path = root / args.guide_output
    write_jsonl(pool_path, pool)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report.update(
        {
            "pool_jsonl": pool_path.relative_to(root).as_posix(),
            "pool_sha256": sha256_file(pool_path),
            "payload_json": payload_path.relative_to(root).as_posix(),
            "payload_sha256": sha256_file(payload_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(guide, encoding="utf-8-sig")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
