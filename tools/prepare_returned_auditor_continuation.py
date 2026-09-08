#!/usr/bin/env python3
"""Advance only returned auditors and assemble the current 12-person payload."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

try:
    from . import prepare_incremental_human_audit_round as incremental
except ImportError:  # Direct script execution from tools/.
    import prepare_incremental_human_audit_round as incremental


STRATA = (
    ("microtext", "train"),
    ("microtext", "dev"),
    ("microtext", "test"),
    ("visualdiff", "test"),
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def assignment_ids(payloads: list[dict[str, Any]]) -> set[str]:
    return {
        incremental.identifier(row)
        for payload in payloads
        for auditor in payload.get("auditors", [])
        for row in auditor.get("rows", [])
        if incremental.identifier(row)
    }


def previous_round(payloads: list[dict[str, Any]], number: int) -> int:
    rounds: list[int] = []
    for payload in payloads:
        for auditor in payload.get("auditors", []):
            if int(auditor.get("number", -1)) != number:
                continue
            rounds.append(int(auditor.get("round") or 1))
    if not rounds:
        raise ValueError(f"auditor {number:02d} has no previous assignment")
    return max(rounds)


def select_new_assignments(
    pool: list[dict[str, Any]],
    used_ids: set[str],
    auditor_numbers: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not auditor_numbers or len(set(auditor_numbers)) != len(auditor_numbers):
        raise ValueError("auditor numbers must be nonempty and unique")
    available = [
        row
        for row in pool
        if incremental.identifier(row)
        and incremental.identifier(row) not in used_ids
    ]
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    needed = len(auditor_numbers) * 3
    for task_name, split in STRATA:
        candidates = [
            row
            for row in available
            if incremental.task(row) == task_name
            and incremental.split_name(row) == split
        ]
        strata[(task_name, split)] = incremental.select_diverse(
            candidates,
            needed,
            incremental.visual_family if task_name == "visualdiff" else incremental.source_group,
        )

    result: dict[int, list[dict[str, Any]]] = {}
    for position, number in enumerate(auditor_numbers):
        rows: list[dict[str, Any]] = []
        for key in STRATA:
            rows.extend(strata[key][position * 3 : position * 3 + 3])
        result[number] = rows

    identifiers = [
        incremental.identifier(row)
        for rows in result.values()
        for row in rows
    ]
    if len(identifiers) != len(auditor_numbers) * 12:
        raise ValueError("continuation assignment count is incomplete")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("continuation assignments contain duplicate row IDs")
    if set(identifiers) & used_ids:
        raise ValueError("continuation assignments overlap previous auditor work")
    return result


def guide_text(auditors: list[dict[str, Any]], advanced: list[int]) -> str:
    assignments = "\n".join(
        f"- 复核员 {row['number']:02d}：{row['workbook']}"
        for row in sorted(auditors, key=lambda value: int(value["number"]))
    )
    advanced_set = set(advanced)
    preserved = sorted(
        int(row["number"])
        for row in auditors
        if int(row["number"]) not in advanced_set
    )
    advanced_text = "、".join(f"{number:02d}" for number in sorted(advanced))
    preserved_text = "、".join(f"{number:02d}" for number in preserved)
    return f"""Eng_Bench Gold v2.0 Global - 当前 12 位独立复核任务

负责人先看：
1. 本包只有 12 个复核 Excel 和本说明，不含主审文件，也没有内嵌 ZIP。
2. 本次仅为已交回且通过机器完整性校验的复核员 {advanced_text} 分配了新样本。
3. 保持不变的复核员是 {preserved_text}；他们必须完成或重做上一版任务，不能领取新轮次。
4. 每位复核员只收到与自己编号一致的一个 XLSX，不要把整个 ZIP 发给单个人。
5. 每个 Excel 有 12 条，每条只在黄色 D 列填 1、2 或 3。不能复制上一轮答案。

文件分配：
{assignments}

复核员操作：
1. 用桌面版 Excel 或 WPS 打开自己的 XLSX。
2. 每行看 B 列证据图和 C 列问题，在黄色 D 列输入：1=是，2=否，3=看不清。
3. 看到完成计数为 12/12 后按 Ctrl+S，保持原文件名，只交回这个 XLSX。

MicroText：
- 1：截图完整，机器文字逐字符正确，而且类别正确。
- 2：缺字/截断、文字或类别错误、不是有效工程标签，或者标签被删除线明确划掉。
- 3：图片模糊或证据不足，确实无法判断。
- 类别例子：尺寸值=11.8°、R10、3'-3\"；设备标签=MOTOR、PUMP；引脚/端子/元件=L1、Q3、GP14；管线标签=DRAIN、PG05001-6\"；房间/区域=DECK、KITCHEN。

VisualDiff：
- 1：OLD/NEW 红框内存在真实工程含义变化，如尺寸、标签、房名、设备、线路、符号或工程注释变化。
- 2：两图相同，或只有轻微整体偏移、字体/位置/清晰度/渲染变化，或真实变化只在红框外。
- 3：OLD/NEW 缺失、太模糊，或证据不足。

重要：独立复核结果不会自动写入 Gold。必须等待主审结论并通过全部机器门禁。
"""


def prepare(
    root: Path,
    pool_path: Path,
    assignment_payload_paths: list[Path],
    active_payload_path: Path,
    auditor_numbers: list[int],
    evidence_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    pool = incremental.read_jsonl(pool_path)
    payloads = [read_json(path) for path in assignment_payload_paths]
    active_payload = read_json(active_payload_path)
    used_ids = assignment_ids(payloads)
    selected = select_new_assignments(pool, used_ids, auditor_numbers)

    addendum_auditors: list[dict[str, Any]] = []
    for number in auditor_numbers:
        payload_rows: list[dict[str, Any]] = []
        for display_index, row in enumerate(selected[number], start=1):
            name = f"auditor_{number:02d}_{display_index:02d}_{incremental.identifier(row)}.png"
            evidence_path = evidence_dir / name
            if incremental.task(row) == "microtext":
                incremental.render_micro_evidence(root, row, evidence_path)
            else:
                incremental.render_visual_evidence(root, row, evidence_path)
            payload_rows.append(
                incremental.payload_row(
                    row,
                    display_index,
                    evidence_path.resolve().as_posix(),
                )
            )
        round_number = previous_round(payloads, number) + 1
        addendum_auditors.append(
            {
                "number": number,
                "round": round_number,
                "workbook": f"AUDITOR_{number:02d}_ROUND{round_number}_REVIEW_12.xlsx",
                "rows": payload_rows,
            }
        )

    addendum_payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "returned-auditor continuation only",
        "primary": {"issued": False, "workbook": "PRIMARY_NOT_ISSUED.xlsx", "rows": []},
        "auditors": addendum_auditors,
        "counts": {
            "primary": 0,
            "auditors": len(addendum_auditors),
            "auditor_rows": len(addendum_auditors) * 12,
        },
        "gold_rows_modified": 0,
    }
    active_auditors = list(active_payload.get("auditors") or [])
    active_numbers = {int(row["number"]) for row in active_auditors}
    if active_numbers & set(auditor_numbers):
        raise ValueError("active payload already contains an advanced auditor")
    combined_auditors = sorted(
        active_auditors + addendum_auditors,
        key=lambda value: int(value["number"]),
    )
    combined_numbers = [int(row["number"]) for row in combined_auditors]
    if combined_numbers != list(range(1, 13)):
        raise ValueError(f"combined auditor numbers must be 01-12, found {combined_numbers}")
    combined_ids = [
        incremental.identifier(row)
        for auditor in combined_auditors
        for row in auditor.get("rows", [])
    ]
    if len(combined_ids) != 144 or len(set(combined_ids)) != 144:
        raise ValueError("combined payload must contain 144 unique assignments")
    combined_payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "current 12-person independent audit; no primary workbook",
        "primary": {"issued": False, "workbook": "PRIMARY_NOT_ISSUED.xlsx", "rows": []},
        "auditors": combined_auditors,
        "counts": {"primary": 0, "auditors": 12, "auditor_rows": 144},
        "advanced_auditors": auditor_numbers,
        "preserved_auditors": sorted(active_numbers),
        "gold_rows_modified": 0,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "advanced_auditors": auditor_numbers,
        "preserved_auditors": sorted(active_numbers),
        "previous_assignment_ids_excluded": len(used_ids),
        "new_rows": len(addendum_auditors) * 12,
        "combined_auditors": 12,
        "combined_rows": 144,
        "new_task_counts": dict(
            sorted(
                Counter(
                    incremental.task(row)
                    for rows in selected.values()
                    for row in rows
                ).items()
            )
        ),
        "new_split_counts": dict(
            sorted(
                Counter(
                    incremental.split_name(row)
                    for rows in selected.values()
                    for row in rows
                ).items()
            )
        ),
        "evidence_images": len(list(evidence_dir.glob("*.png"))),
        "primary_workbook_included": False,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "valid": len(list(evidence_dir.glob("*.png"))) == len(addendum_auditors) * 12,
    }
    return addendum_payload, combined_payload, report, guide_text(combined_auditors, auditor_numbers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pool-jsonl", type=Path, required=True)
    parser.add_argument("--assignment-payload", type=Path, action="append", required=True)
    parser.add_argument("--active-payload", type=Path, required=True)
    parser.add_argument("--auditor-number", type=int, action="append", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--addendum-payload", type=Path, required=True)
    parser.add_argument("--combined-payload", type=Path, required=True)
    parser.add_argument("--guide-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--max-image-pixels", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    absolute = lambda value: value if value.is_absolute() else root / value
    evidence_dir = absolute(args.evidence_dir)
    incremental.ensure_child(evidence_dir, root / "derived/tmp")
    if evidence_dir.exists():
        if not args.overwrite:
            raise FileExistsError(evidence_dir)
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)
    if args.max_image_pixels is not None:
        if args.max_image_pixels <= 0:
            raise ValueError("--max-image-pixels must be positive")
        Image.MAX_IMAGE_PIXELS = args.max_image_pixels

    addendum, combined, report, guide = prepare(
        root,
        absolute(args.pool_jsonl),
        [absolute(path) for path in args.assignment_payload],
        absolute(args.active_payload),
        args.auditor_number,
        evidence_dir,
    )
    for path, value in (
        (absolute(args.addendum_payload), addendum),
        (absolute(args.combined_payload), combined),
        (absolute(args.report_json), report),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    guide_path = absolute(args.guide_output)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(guide, encoding="utf-8-sig")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
