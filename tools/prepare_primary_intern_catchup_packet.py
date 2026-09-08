#!/usr/bin/env python3
"""Prepare the 1,500-row primary-review catch-up packet for Gold v2.0."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

try:
    from .prepare_incremental_human_audit_round import (
        evidence_materializable,
        identifier,
        machine_suggestion,
        read_jsonl,
        render_micro_evidence,
        render_visual_evidence,
        sha256_file,
        source_group,
        split_name,
        task,
        visual_family,
    )
except ImportError:
    from prepare_incremental_human_audit_round import (
        evidence_materializable,
        identifier,
        machine_suggestion,
        read_jsonl,
        render_micro_evidence,
        render_visual_evidence,
        sha256_file,
        source_group,
        split_name,
        task,
        visual_family,
    )


Image.MAX_IMAGE_PIXELS = 300_000_000

MICRO_ENGINEERING_QUOTAS = {
    "unknown_microtext": 28,
    "pipe_line_tag": 8,
    "process_value": 13,
    "instrument_tag": 20,
    "equipment_tag": 15,
    "process_label": 16,
}
MICRO_ENGINEERING_REASONS = {
    "unknown_microtext": "判断该区域是否为有效工程标注，并给出规范类别",
    "pipe_line_tag": "区分管线/介质/服务标签与普通说明文字",
    "process_value": "区分工艺运行值、尺寸值与表格数值",
    "instrument_tag": "区分仪表位号、设备位号与元器件标号",
    "equipment_tag": "区分设备标签、部件名称与普通说明",
    "process_label": "区分工艺/区域/设备语义并确认规范类别",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def round_robin(rows: Iterable[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Choose rows across source groups while preserving source-local order."""
    groups: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        groups[source_group(row)].append(row)
    ordered = deque(sorted(groups))
    selected: list[dict[str, Any]] = []
    while ordered and len(selected) < count:
        group = ordered.popleft()
        selected.append(groups[group].popleft())
        if groups[group]:
            ordered.append(group)
    if len(selected) != count:
        raise ValueError(f"could only select {len(selected)} of {count} requested rows")
    return selected


def select_micro_engineering(rows: list[dict[str, Any]]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for category, quota in MICRO_ENGINEERING_QUOTAS.items():
        candidates = [row for row in rows if str(row.get("category") or "unknown_microtext") == category]
        for row in round_robin(candidates, quota):
            selected[identifier(row)] = MICRO_ENGINEERING_REASONS[category]
    if len(selected) != 100:
        raise ValueError(f"expected 100 MicroText engineering rows, got {len(selected)}")
    return selected


def has_visual_review_signal(row: dict[str, Any]) -> bool:
    return any(
        row.get(key) not in (None, "", [], {})
        for key in (
            "description_source",
            "review_confidence",
            "confidence",
            "machine_description_corrected_from",
            "machine_change_type_corrected_from",
        )
    )


def visual_reason(row: dict[str, Any]) -> str:
    if row.get("machine_description_corrected_from") or row.get("machine_change_type_corrected_from"):
        return "机器描述/变化类型曾被纠正；需按工程含义复核"
    if has_visual_review_signal(row):
        return "低置信度机器候选；需判断红框内是否存在真实工程变化"
    change = str(row.get("change_type") or "").lower()
    if "dimension" in change or "geometry" in change:
        return "确认尺寸/几何变化是否改变工程含义"
    if "symbol" in change or "schematic" in change:
        return "确认符号/连接变化及其工程语义"
    if "addition" in change or "deletion" in change:
        return "确认新增/删除发生在红框内且具有工程意义"
    return "确认文字变化是否改变工程含义，而非仅版式或渲染差异"


def select_visual_engineering(rows: list[dict[str, Any]]) -> dict[str, str]:
    seeded = [row for row in rows if has_visual_review_signal(row)]
    if len(seeded) > 100:
        seeded = round_robin(seeded, 100)
    selected_ids = {identifier(row) for row in seeded}
    remaining = [row for row in rows if identifier(row) not in selected_ids]
    selected = seeded + round_robin(remaining, 100 - len(seeded))
    result = {identifier(row): visual_reason(row) for row in selected}
    if len(result) != 100:
        raise ValueError(f"expected 100 VisualDiff engineering rows, got {len(result)}")
    return result


def load_auditor_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("candidate_id") or row.get("pair_id") or "").strip()
        for auditor in payload.get("auditors", [])
        for row in auditor.get("rows", [])
        if row.get("candidate_id") or row.get("pair_id")
    }


def payload_record(
    row: dict[str, Any],
    evidence_path: Path,
    engineering_reason: str,
    auditor_overlap: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "primary_index": int(row["primary_pool_index"]),
        "task": task(row),
        "record_id": identifier(row),
        "machine_suggestion": machine_suggestion(row),
        "evidence_path": str(evidence_path.resolve()),
        "reserved_split": split_name(row),
        "source_group": source_group(row),
        "capacity_cohort": str(row.get("primary_pool_capacity_cohort") or ""),
        "retained_from_previous_primary": row.get("primary_pool_capacity_cohort") == "primary_rights_cleared_258",
        "engineering_required": bool(engineering_reason),
        "engineering_reason": engineering_reason,
        "auditor_overlap": auditor_overlap,
        "safe_to_merge_gold": False,
    }
    if task(row) == "microtext":
        record.update(
            {
                "candidate_id": identifier(row),
                "proposed_text": str(row.get("proposed_text") or row.get("target_text") or "").strip(),
                "category": str(row.get("category") or "unknown_microtext").strip(),
                "document_id": str(row.get("doc_id") or ""),
                "page_index": row.get("page_index"),
                "full_page_path": str(row.get("image_path") or ""),
            }
        )
    else:
        record.update(
            {
                "pair_id": identifier(row),
                "change_type": str(row.get("change_type") or "visual").strip(),
                "change_description": str(
                    row.get("human_description")
                    or row.get("change_desc_gt")
                    or row.get("description")
                    or ""
                ).strip(),
                "old_text": str(row.get("old_text") or "").strip(),
                "new_text": str(row.get("new_text") or "").strip(),
                "project_id": visual_family(row),
                "old_page_path": str(row.get("image_old") or ""),
                "new_page_path": str(row.get("image_new") or ""),
            }
        )
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--pool", required=True)
    parser.add_argument("--auditor-payload", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--payload-json", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    pool_path = (root / args.pool).resolve()
    auditor_path = (root / args.auditor_payload).resolve()
    evidence_dir = (root / args.evidence_dir).resolve()
    payload_path = (root / args.payload_json).resolve()
    report_path = (root / args.report_json).resolve()
    report_md_path = (root / args.report_md).resolve()

    rows = read_jsonl(pool_path)
    if len(rows) != 1500 or len({identifier(row) for row in rows}) != 1500:
        raise ValueError("primary pool must contain exactly 1,500 unique rows")
    rows.sort(key=lambda row: int(row["primary_pool_index"]))
    micro = [row for row in rows if task(row) == "microtext"]
    visual = [row for row in rows if task(row) == "visualdiff"]
    if (len(micro), len(visual)) != (900, 600):
        raise ValueError(f"expected 900/600 task mix, got {len(micro)}/{len(visual)}")
    missing_evidence = [identifier(row) for row in rows if not evidence_materializable(root, row)]
    if missing_evidence:
        raise ValueError(f"{len(missing_evidence)} rows cannot materialize evidence")

    micro_engineering = select_micro_engineering(micro)
    visual_engineering = select_visual_engineering(visual)
    engineering = {**micro_engineering, **visual_engineering}
    auditor_ids = load_auditor_ids(auditor_path)
    pool_ids = {identifier(row) for row in rows}
    missing_auditor = sorted(auditor_ids - pool_ids)
    if missing_auditor:
        raise ValueError(f"auditor rows missing from primary pool: {missing_auditor[:5]}")

    if evidence_dir.exists():
        raise FileExistsError(f"evidence directory already exists: {evidence_dir}")
    evidence_dir.mkdir(parents=True)
    payload_rows: list[dict[str, Any]] = []
    for position, row in enumerate(rows, 1):
        suffix = identifier(row).replace("/", "_")[-48:]
        output = evidence_dir / f"primary_{position:04d}_{task(row)}_{suffix}.png"
        if task(row) == "microtext":
            render_micro_evidence(root, row, output)
        else:
            render_visual_evidence(root, row, output)
        payload_rows.append(
            payload_record(
                row,
                output,
                engineering.get(identifier(row), ""),
                identifier(row) in auditor_ids,
            )
        )
        if position % 100 == 0:
            print(f"rendered {position}/1500")

    for engineering_index, record in enumerate(
        [item for item in payload_rows if item["engineering_required"]], 1
    ):
        record["engineering_index"] = engineering_index

    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "primary review catch-up plus mandatory engineering adjudication",
        "rows": payload_rows,
        "counts": {
            "total": len(payload_rows),
            "microtext": len(micro),
            "visualdiff": len(visual),
            "engineering_total": len(engineering),
            "engineering_microtext": len(micro_engineering),
            "engineering_visualdiff": len(visual_engineering),
            "auditor_overlap": len(auditor_ids),
            "retained_from_previous_primary": sum(
                bool(record["retained_from_previous_primary"]) for record in payload_rows
            ),
        },
        "inputs": {
            "pool": str(pool_path),
            "pool_sha256": sha256_file(pool_path),
            "auditor_payload": str(auditor_path),
            "auditor_payload_sha256": sha256_file(auditor_path),
        },
        "safety": {
            "gold_rows_modified": 0,
            "all_rows_safe_to_merge_gold": False,
            "promotion_requires_completed_human_return_and_machine_gates": True,
        },
    }
    write_json(payload_path, payload)
    report = {
        "status": "PASS",
        "payload": str(payload_path),
        "payload_sha256": sha256_file(payload_path),
        "evidence_dir": str(evidence_dir),
        "evidence_png_count": len(list(evidence_dir.glob("*.png"))),
        "counts": payload["counts"],
        "task_counts": dict(Counter(record["task"] for record in payload_rows)),
        "split_counts": dict(Counter(record["reserved_split"] for record in payload_rows)),
        "engineering_category_counts": dict(
            Counter(record.get("category", "visualdiff") for record in payload_rows if record["engineering_required"])
        ),
        "engineering_visual_family_count": len(
            {record.get("project_id") for record in payload_rows if record["engineering_required"] and record["task"] == "visualdiff"}
        ),
        "unique_record_ids": len({record["record_id"] for record in payload_rows}),
        "auditor_ids_all_covered": auditor_ids <= pool_ids,
        "gold_rows_modified": 0,
    }
    write_json(report_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(
        "# Primary Intern Catch-up Packet Preparation\n\n"
        f"- Status: **{report['status']}**\n"
        f"- Rows: **{len(payload_rows)}** (MicroText {len(micro)}, VisualDiff {len(visual)})\n"
        f"- Mandatory engineering review: **{len(engineering)}** (100 + 100)\n"
        f"- Current auditor rows covered: **{len(auditor_ids)}/{len(auditor_ids)}**\n"
        f"- Evidence PNGs: **{report['evidence_png_count']}**\n"
        "- Gold rows modified: **0**\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
