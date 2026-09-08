#!/usr/bin/env python3
"""Build a paired, source-backed audit round for previously unaudited Gold rows.

This is a release-quality recheck, not a promotion path. It selects 144 unique
active benchmark identities, renders compact evidence from the exact benchmark
images and boxes, and assigns every identity to two independent auditors. Prior
assignments, completed decisions, formal-agreement rows, and active audit holds
are excluded. The script never edits active Gold JSONL.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from . import prepare_fresh_auditor_round as fresh
    from . import prepare_paired_auditor_round as paired
    from . import visualdiff_description_finality as finality
except ImportError:  # Direct script execution from tools/.
    import prepare_fresh_auditor_round as fresh
    import prepare_paired_auditor_round as paired
    import visualdiff_description_finality as finality


UNIQUE_ROWS = paired.UNIQUE_ROWS
STRATA = tuple(
    (task, split)
    for task in ("microtext", "visualdiff")
    for split in ("train", "dev", "test")
)
ROWS_PER_STRATUM = UNIQUE_ROWS // len(STRATA)
DEFAULT_ROUND_NAME = "paired_active_gold_recheck_2026_09_07"
PLACEHOLDER_DESCRIPTIONS = {
    "CHANGE_DESC_GT_TODO",
    "CHANGE_DESC_TODO",
    "TODO",
    "TBD",
}
GENERIC_HIGHLIGHT_DESCRIPTION = re.compile(
    r"Highlighted visual content changed(?: near .+)? from .+ to .+\.",
    re.DOTALL,
)
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def active_hashes(root: Path) -> dict[str, str]:
    paths = (
        Path("microtext/annotations/microtext_items.jsonl"),
        Path("microtext/annotations/microtext_questions.jsonl"),
        Path("visualdiff/annotations/visualdiff_pairs.jsonl"),
        Path("visualdiff/annotations/visualdiff_questions.jsonl"),
        Path("eng_bench.jsonl"),
    )
    return {path.as_posix(): fresh.sha256_file(root / path) for path in paths}


def current_recheck_ids(root: Path) -> set[str]:
    pointer = root / "derived/quality/current_auditor_reconciliation.json"
    if not pointer.is_file():
        raise ValueError("current auditor reconciliation pointer is missing")
    current = fresh.read_json(pointer)
    report_path = root / str(current.get("report_path") or "")
    if not report_path.is_file():
        raise ValueError("current auditor reconciliation report is missing")
    if fresh.sha256_file(report_path) != current.get("report_sha256"):
        raise ValueError("current auditor reconciliation pointer hash is stale")
    report = fresh.read_json(report_path)
    if report.get("status") != "PASS" or report.get("active_gold_modified") is not False:
        raise ValueError("current auditor reconciliation is not release-safe")
    holds_path = report_path.parent / "registry_active_audit_rechecks.jsonl"
    return {
        str(row.get("active_gold_identity") or "").strip()
        for row in fresh.read_jsonl(holds_path)
        if str(row.get("active_gold_identity") or "").strip()
    }


def annotation_aliases(root: Path) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = defaultdict(set)
    for relative, identity_key in (
        (Path("microtext/annotations/microtext_items.jsonl"), "item_id"),
        (Path("visualdiff/annotations/visualdiff_pairs.jsonl"), "pair_id"),
    ):
        for row in fresh.read_jsonl(root / relative):
            identity = str(row.get(identity_key) or "").strip()
            if not identity:
                continue
            aliases[identity].add(identity)
            for key in ("source_candidate_id", "candidate_id", "id"):
                value = str(row.get(key) or "").strip()
                if value:
                    aliases[identity].add(value)
    return aliases


def benchmark_rows(root: Path) -> list[dict[str, Any]]:
    aliases = annotation_aliases(root)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for primary_index, unified in enumerate(
        fresh.read_jsonl(root / "eng_bench.jsonl"), start=1
    ):
        task = str(unified.get("task") or "").strip()
        metadata = unified.get("metadata") or {}
        identity = str(
            metadata.get("item_id") if task == "microtext" else metadata.get("pair_id")
        ).strip()
        if task not in {"microtext", "visualdiff"} or not identity or identity in seen:
            continue
        seen.add(identity)
        rows.append(
            {
                "task": task,
                "primary_index": primary_index,
                "record_id": identity,
                "candidate_id": identity if task == "microtext" else "",
                "pair_id": identity if task == "visualdiff" else "",
                "source_aliases": sorted(aliases.get(identity, {identity})),
                "reserved_split": str(unified.get("split") or "").strip(),
                "source_group": str(metadata.get("doc_id") or identity).strip(),
                "document_id": str(metadata.get("doc_id") or "").strip(),
                "category": str(metadata.get("category") or "").strip(),
                "corrected_category": str(metadata.get("category") or "").strip(),
                "corrected_text": str(unified.get("answer") or "") if task == "microtext" else "",
                "change_description": str(unified.get("answer") or "") if task == "visualdiff" else "",
                "machine_suggestion": (
                    f"{unified.get('answer') or ''}\n类别：{metadata.get('category') or ''}"
                    if task == "microtext"
                    else str(unified.get("answer") or "")
                ),
                "images": list(unified.get("images") or []),
                "benchmark_evidence": list(unified.get("evidence") or []),
                "question_ids": [str(unified.get("id") or "")],
                "active_gold_identity": identity,
                "active_gold_recheck": True,
                "evidence_sha256": f"identity:{identity}",
                "safe_to_merge_gold": False,
            }
        )
    return rows


def tentative_visualdiff_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("record_id") or "").strip()
        for row in rows
        if row.get("task") == "visualdiff"
        and finality.tentative_description_details(
            str(row.get("change_description") or "")
        )
        and str(row.get("record_id") or "").strip()
    }


def machine_known_description_issue(description: str) -> str | None:
    """Return debts that are already machine-provable and need no audit vote."""
    text = description.strip()
    if not text:
        return "blank"
    if text.upper() in PLACEHOLDER_DESCRIPTIONS:
        return "placeholder"
    if GENERIC_HIGHLIGHT_DESCRIPTION.fullmatch(text):
        return "generic_highlight"
    if CJK_CHARACTER.search(text):
        return "non_english"
    return None


def machine_known_nonrelease_visualdiff(
    rows: list[dict[str, Any]],
) -> tuple[set[str], Counter[str]]:
    ids: set[str] = set()
    reasons: Counter[str] = Counter()
    for row in rows:
        if row.get("task") != "visualdiff":
            continue
        issue = machine_known_description_issue(
            str(row.get("change_description") or "")
        )
        identity = str(row.get("record_id") or "").strip()
        if issue and identity:
            ids.add(identity)
            reasons[issue] += 1
    return ids, reasons


def eligible_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assigned_ids, _, assignment_files = fresh.historical_assignments(root)
    completed_ids, decision_files = fresh.completed_assignments(root)
    agreement_ids = fresh.agreement_ids(root)
    recheck_ids = current_recheck_ids(root)
    active_rows = benchmark_rows(root)
    tentative_ids = tentative_visualdiff_ids(active_rows)
    nonrelease_ids, nonrelease_reasons = machine_known_nonrelease_visualdiff(
        active_rows
    )
    excluded_ids = (
        assigned_ids
        | completed_ids
        | agreement_ids
        | recheck_ids
        | tentative_ids
        | nonrelease_ids
    )
    eligible = [
        row
        for row in active_rows
        if not (set(row["source_aliases"]) & excluded_ids)
    ]
    context = {
        "historical_assignment_ids": len(assigned_ids),
        "completed_decision_ids": len(completed_ids),
        "formal_agreement_ids": len(agreement_ids),
        "active_audit_recheck_ids": len(recheck_ids),
        "tentative_visualdiff_ids": len(tentative_ids),
        "machine_known_nonrelease_visualdiff_ids": len(nonrelease_ids),
        "machine_known_nonrelease_visualdiff_reasons": dict(
            sorted(nonrelease_reasons.items())
        ),
        "historical_payloads": assignment_files,
        "completed_decision_files": decision_files,
    }
    return eligible, context


def select_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["task"], row["reserved_split"])].append(row)
    missing = {
        f"{task}:{split}": ROWS_PER_STRATUM - len(grouped[(task, split)])
        for task, split in STRATA
        if len(grouped[(task, split)]) < ROWS_PER_STRATUM
    }
    if missing:
        raise ValueError(f"active Gold recheck pool lacks required strata: {missing}")
    selected: list[dict[str, Any]] = []
    source_usage: Counter[str] = Counter()
    for key in STRATA:
        selected.extend(
            fresh.select_diverse(grouped[key], ROWS_PER_STRATUM, set(), source_usage)
        )
    if len({row["record_id"] for row in selected}) != UNIQUE_ROWS:
        raise ValueError("selected active Gold identities are not unique")
    return selected


def font(size: int) -> ImageFont.ImageFont:
    path = Path("C:/Windows/Fonts/arial.ttf")
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def valid_box(box: Any, size: tuple[int, int]) -> list[int]:
    if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box):
        raise ValueError(f"invalid benchmark bbox: {box}")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
        raise ValueError(f"benchmark bbox outside image {size}: {box}")
    return box


def crop_with_context(image: Image.Image, box: list[int], padding: int) -> Image.Image:
    x1, y1, x2, y2 = valid_box(box, image.size)
    scale = max(x2 - x1, y2 - y1)
    pad = max(padding, scale)
    bounds = (max(0, x1 - pad), max(0, y1 - pad), min(image.width, x2 + pad), min(image.height, y2 + pad))
    context = image.crop(bounds).convert("RGB")
    draw = ImageDraw.Draw(context)
    draw.rectangle(
        (x1 - bounds[0], y1 - bounds[1], x2 - bounds[0] - 1, y2 - bounds[1] - 1),
        outline="#DC2626",
        width=max(3, min(context.size) // 90),
    )
    return context


def paste_fit(canvas: Image.Image, source: Image.Image, bounds: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = bounds
    scale = min((x2 - x1) / source.width, (y2 - y1) / source.height)
    fitted = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas.paste(fitted, (x1 + (x2 - x1 - fitted.width) // 2, y1 + (y2 - y1 - fitted.height) // 2))


def render_evidence(root: Path, row: dict[str, Any], path: Path, max_pixels: int) -> None:
    images = row["images"]
    evidence = row["benchmark_evidence"]
    expected = 1 if row["task"] == "microtext" else 2
    if len(images) != expected:
        raise ValueError(f"{row['record_id']}: expected {expected} benchmark images")
    boxes: list[list[int]] = []
    sources: list[Image.Image] = []
    old_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        for index, relative in enumerate(images):
            image_path = (root / str(relative)).resolve()
            if not image_path.is_relative_to(root) or not image_path.is_file():
                raise ValueError(f"missing benchmark image: {relative}")
            matches = [entry.get("bbox") for entry in evidence if entry.get("image_index") == index]
            if len(matches) != 1:
                raise ValueError(f"{row['record_id']}: ambiguous benchmark evidence for image {index}")
            with Image.open(image_path) as opened:
                if opened.width * opened.height > max_pixels:
                    raise ValueError(f"benchmark image exceeds pixel ceiling: {relative}")
                image = opened.convert("RGB")
            boxes.append(valid_box(matches[0], image.size))
            sources.append(image)

        canvas = Image.new("RGB", (1200, 440), "white")
        draw = ImageDraw.Draw(canvas)
        if row["task"] == "microtext":
            exact = sources[0].crop(boxes[0])
            context = crop_with_context(sources[0], boxes[0], 100)
            draw.text((20, 10), "TARGET (exact benchmark box)", fill="#111827", font=font(28))
            draw.text((620, 10), "CONTEXT (red box = target)", fill="#111827", font=font(28))
            paste_fit(canvas, exact, (20, 55, 580, 420))
            paste_fit(canvas, context, (620, 55, 1180, 420))
        else:
            for index, label in enumerate(("OLD", "NEW")):
                left = 20 + index * 600
                context = crop_with_context(sources[index], boxes[index], 80)
                draw.text((left, 10), f"{label} (red box = compared region)", fill="#111827", font=font(28))
                paste_fit(canvas, context, (left, 55, left + 560, 420))
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(path, format="PNG", optimize=True)
    finally:
        Image.MAX_IMAGE_PIXELS = old_limit


def normalize_assignment(row: dict[str, Any], display_index: int) -> dict[str, Any]:
    result = dict(row)
    result.update(
        {
            "display_index": str(display_index),
            "preserved_answer_code": "",
            "auditor_assignment_status": "independent_active_gold_recheck_only",
            "assignment_origin": "paired_active_gold_release_recheck",
            "safe_to_merge_gold": False,
        }
    )
    return result


def build(
    root: Path,
    evidence_dir: Path,
    *,
    round_name: str = DEFAULT_ROUND_NAME,
    max_image_pixels: int = 100_000_000,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    root = root.resolve()
    evidence_dir = evidence_dir.resolve()
    if evidence_dir.exists() or not evidence_dir.is_relative_to(root / "derived/quality"):
        raise ValueError("evidence output must be a new directory under derived/quality")
    before = active_hashes(root)
    eligible, exclusions = eligible_rows(root)
    selected = select_rows(eligible)
    evidence_dir.mkdir(parents=True)
    for index, row in enumerate(selected, start=1):
        path = evidence_dir / f"gold_recheck_{index:03d}.png"
        render_evidence(root, row, path, max_image_pixels)
        row["evidence_path"] = path.as_posix()
        row["evidence_sha256"] = fresh.sha256_file(path)

    assignments = paired.assign_pairs(selected)
    auditors = []
    for number in range(1, paired.AUDITOR_COUNT + 1):
        rows = [
            normalize_assignment(row, display_index)
            for display_index, row in enumerate(assignments[number], start=1)
        ]
        for row in rows:
            row["assignment_origin"] = "paired_active_gold_release_recheck"
        auditors.append(
            {
                "number": number,
                "round": round_name,
                "sheet_name": "审核24条",
                "machine_sheet_name": "机器数据_勿改",
                "workbook": f"AUDITOR_{number:02d}_GOLD_RECHECK_REVIEW_24.xlsx",
                "preserved_rows": 0,
                "fresh_rows": paired.ROWS_PER_AUDITOR,
                "preserved_answers": 0,
                "rows": rows,
            }
        )

    all_rows = [row for auditor in auditors for row in auditor["rows"]]
    ids = Counter(row["record_id"] for row in all_rows)
    hashes = Counter(row["evidence_sha256"] for row in all_rows)
    if set(ids.values()) != {paired.REVIEWERS_PER_ROW} or len(ids) != UNIQUE_ROWS:
        raise ValueError("active Gold identities are not paired exactly twice")
    if set(hashes.values()) != {paired.REVIEWERS_PER_ROW} or len(hashes) != UNIQUE_ROWS:
        raise ValueError("active Gold evidence is not paired exactly twice")
    after = active_hashes(root)
    if before != after:
        raise ValueError("active Gold changed while preparing the audit round")

    counts = {
        "auditors": paired.AUDITOR_COUNT,
        "rows_per_auditor": paired.ROWS_PER_AUDITOR,
        "auditor_assignment_rows": len(all_rows),
        "unique_review_rows": len(ids),
        "reviewers_per_unique_row": paired.REVIEWERS_PER_ROW,
        "prefilled_answers": 0,
        "unique_task_counts": dict(Counter(row["task"] for row in selected)),
        "unique_split_counts": dict(Counter(row["reserved_split"] for row in selected)),
        "unique_task_split_counts": dict(
            Counter(f"{row['task']}:{row['reserved_split']}" for row in selected)
        ),
        "eligible_pool_before_selection": len(eligible),
        "selected_source_groups": len({row["source_group"] for row in selected}),
    }
    safety = {
        "all_rows_active_gold_at_assignment": True,
        "historical_assignment_overlap": 0,
        "completed_decision_overlap": 0,
        "formal_agreement_overlap": 0,
        "active_audit_recheck_overlap": 0,
        "tentative_visualdiff_overlap": 0,
        "machine_known_nonrelease_visualdiff_overlap": 0,
        "intentional_cross_auditor_replication": paired.REVIEWERS_PER_ROW,
        "prefilled_answers": 0,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "paired independent active-Gold release recheck; 24 unanswered rows per auditor",
        "allowed_prefilled_workbooks": {},
        "primary": {"issued": False, "workbook": "PRIMARY_NOT_ISSUED.xlsx", "rows": []},
        "auditors": auditors,
        "counts": counts,
        "exclusions": exclusions,
        "safety": safety,
        "active_gold_hashes": before,
        "gold_rows_modified": 0,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "payload_valid": True,
        "counts": counts,
        "exclusions": exclusions,
        "safety": safety,
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
    }
    guide = f"""Eng_Bench Gold v2.0 Global - 12位独立复审员Gold抽检（每人24题）

【管理员发放】
1. 每位复审员只收到与自己编号相同的一个 XLSX 文件。
2. 例如：复审员01只收到 AUDITOR_01_GOLD_RECHECK_REVIEW_24.xlsx。
3. 不要把其他人的文件、旧ZIP或项目资料一起发出。
4. 图片已经完整嵌入Excel，不需要另外发送图片文件夹。
5. 完成后保持原文件名交回。

【复审员操作】
1. 使用桌面版Excel或WPS打开，只看可见工作表“审核24条”。
2. 逐行查看B列证据图片和C列判断问题。
3. 在黄色D列输入1、2或3，然后按Enter。
4. E5达到24 / 24后按Ctrl+S保存并交回。
5. 不要修改B/C/E列，不要添加备注，不要打开隐藏工作表。

【统一答案】
- 1=是/通过
- 2=否/存在问题
- 3=图片看不清/证据不足
- 速记：1=是，2=否，3=看不清。

【MicroText】
- 1：红框目标是完整有效工程标签，且机器文字和类别都正确。
- 2：不是有效工程标签，或目标明确缺字，或文字/类别任一错误。
- 3：图片太模糊或边界不清，无法可靠判断。
- 只判断红框目标；背景中的其他文字、线条和划线不决定答案。
{fresh.category_guide()}

【VisualDiff】
- 1：OLD/NEW红框区域有真实工程变化，并且机器给出的Gold描述准确。
- 2：完全相同、只有偏移/排版/渲染差异，或机器Gold描述不准确。
- 3：OLD/NEW缺失、裁剪不全或太模糊，无法可靠判断。

【独立复审要求】
- 每题由两位复审员独立作答，用于一致性与发布风险检查；这是有意配对，不是重复错误。
- 复审员不能互看答案、讨论或复制，只能按自己文件里的证据独立判断。
- 24题都是本人未作答的新任务，不能复制上一轮答案。
- 本轮抽检当前Gold，但任何答案都不会自动写入 Gold、删除Gold或绕过发布门禁。
"""
    return payload, report, guide


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--guide", type=Path, required=True)
    parser.add_argument("--round-name", default=DEFAULT_ROUND_NAME)
    parser.add_argument("--max-image-pixels", type=int, default=100_000_000)
    args = parser.parse_args()
    root = args.root.resolve()
    payload, report, guide = build(
        root,
        root / args.evidence_dir,
        round_name=args.round_name,
        max_image_pixels=args.max_image_pixels,
    )
    for relative, content in (
        (args.output_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
        (args.report_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (args.guide, guide),
    ):
        target = (root / relative).resolve()
        if target.exists() or not target.is_relative_to(root / "derived/quality"):
            raise ValueError(f"output must be a new file under derived/quality: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8-sig" if target.suffix == ".txt" else "utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
