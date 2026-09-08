#!/usr/bin/env python3
"""Prepare a conservative 12-auditor extension from 12 to 24 rows each.

The current assignments and any entered answers are preserved. Twelve fresh,
primary-reviewed staging rows are added per auditor after excluding every known
historical audit assignment, completed audit decision, formal agreement row,
record duplicate, and exact evidence-image duplicate. This tool never edits
gold JSONL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

try:
    from . import normalize_easiest_human_audit_returns as normalizer
except ImportError:  # Direct script execution from tools/.
    import normalize_easiest_human_audit_returns as normalizer


CURRENT_PAYLOAD = Path(
    "derived/quality/incremental_human_audit_payload_12_auditors_2026-08-17-wave215.json"
)
CURRENT_DELIVERY = Path(
    "derived/human_adjudication/Eng_Bench_Gold_v2_AUDITORS_12_NEXT_2026-08-17"
)
PRIMARY_PAYLOAD = Path(
    "derived/quality/primary_intern_payload_4000_provenance_complete_engineering_specialist_compact_2026-08-21-wave292.json"
)
STAGED_FILES = (
    Path(
        "derived/quality/primary_continuation_preflight_2026-08-26-wave835/"
        "microtext_reviewed_pending_gates.jsonl"
    ),
    Path(
        "derived/quality/primary_continuation_preflight_2026-08-26-wave835/"
        "visualdiff_reviewed_pending_gates.jsonl"
    ),
)
AGREEMENT_BRIDGE = Path(
    "derived/review_queues/v2_0_agreement_visualdiff_bridge_2026-08-21-wave300.jsonl"
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def row_identifier(row: dict[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or row.get("pair_id")
        or row.get("record_id")
        or ""
    ).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def collect_assigned_ids(root: Path) -> tuple[set[str], list[str]]:
    patterns = ("*audit*payload*.json",)
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update((root / "derived/quality").glob(pattern))
    assigned: set[str] = set()
    used_files: list[str] = []
    for path in sorted(paths):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        auditors = payload.get("auditors") if isinstance(payload, dict) else None
        if not isinstance(auditors, list):
            continue
        if str(payload.get("workflow") or "").startswith(
            "extended current independent audit"
        ):
            continue
        used_files.append(path.relative_to(root).as_posix())
        for auditor in auditors:
            for row in auditor.get("rows") or []:
                identifier = row_identifier(row)
                if identifier:
                    assigned.add(identifier)
    return assigned, used_files


def collect_completed_ids(root: Path) -> tuple[set[str], list[str]]:
    paths = sorted(
        path
        for path in (root / "derived/human_adjudication/processed_returns").glob(
            "**/*auditor_decisions*.jsonl"
        )
        if path.is_file()
    )
    completed: set[str] = set()
    for path in paths:
        for row in read_jsonl(path):
            identifier = row_identifier(row)
            if identifier:
                completed.add(identifier)
    return completed, [path.relative_to(root).as_posix() for path in paths]


def staged_ids(root: Path) -> set[str]:
    result: set[str] = set()
    for relative in STAGED_FILES:
        for row in read_jsonl(root / relative):
            identifier = row_identifier(row)
            if identifier:
                result.add(identifier)
    return result


def preserved_answers(workbook: Path) -> list[str]:
    rows = normalizer._auditor_records(workbook)
    raw_answers = [
        normalizer._first_value(
            row,
            normalizer.ULTRA_VISUAL_DECISION,
            normalizer.ULTRA_VISUAL_DECISION_LEGACY,
        ).strip()
        for row in rows
    ]
    answers = [
        value[:-2] if value.endswith(".0") and value[:-2] in {"1", "2", "3"} else value
        for value in raw_answers
    ]
    if len(answers) != 12:
        raise ValueError(f"{workbook.name}: expected 12 current visible rows")
    for answer in answers:
        if answer and answer not in {"1", "2", "3"}:
            raise ValueError(f"{workbook.name}: invalid preserved answer {answer!r}")
    return answers


def source_group(row: dict[str, Any]) -> str:
    return str(
        row.get("source_group")
        or row.get("project_id")
        or row.get("document_id")
        or row_identifier(row)
    )


def select_diverse(
    rows: Iterable[dict[str, Any]],
    count: int,
    used_hashes: set[str],
    source_usage: Counter[str],
) -> list[dict[str, Any]]:
    groups: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in sorted(rows, key=lambda item: stable_key(row_identifier(item))):
        groups[source_group(row)].append(row)
    selected: list[dict[str, Any]] = []
    while len(selected) < count:
        candidates = sorted(
            (group for group, values in groups.items() if values),
            key=lambda group: (source_usage[group], stable_key(group)),
        )
        if not candidates:
            break
        made_progress = False
        for group in candidates:
            while groups[group]:
                row = groups[group].popleft()
                evidence_hash = str(row["evidence_sha256"])
                if evidence_hash in used_hashes:
                    continue
                selected.append(row)
                used_hashes.add(evidence_hash)
                source_usage[group] += 1
                made_progress = True
                break
            if len(selected) >= count:
                break
        if not made_progress:
            break
    if len(selected) != count:
        raise ValueError(f"only selected {len(selected)} of {count} required rows")
    return selected


def normalized_row(row: dict[str, Any], display_index: int) -> dict[str, Any]:
    result = dict(row)
    identifier = row_identifier(row)
    result["display_index"] = str(display_index)
    result["record_id"] = identifier
    result["source_group"] = source_group(row)
    result["safe_to_merge_gold"] = False
    result["auditor_assignment_status"] = "independent_audit_only"
    if result.get("task") == "microtext":
        result["candidate_id"] = identifier
        result.setdefault("corrected_text", "")
        result.setdefault("corrected_category", "")
    elif result.get("task") == "visualdiff":
        result["pair_id"] = identifier
    else:
        raise ValueError(f"unknown task for {identifier}: {result.get('task')!r}")
    return result


def build(root: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    current = read_json(root / CURRENT_PAYLOAD)
    primary = read_json(root / PRIMARY_PAYLOAD)
    assigned_ids, assignment_files = collect_assigned_ids(root)
    completed_ids, decision_files = collect_completed_ids(root)
    staged = staged_ids(root)
    agreement_ids = {
        row_identifier(row)
        for row in read_jsonl(root / AGREEMENT_BRIDGE)
        if row_identifier(row)
    }

    current_ids: set[str] = set()
    used_hashes: set[str] = set()
    current_hash_counts: Counter[str] = Counter()
    current_by_number: dict[int, dict[str, Any]] = {}
    preserved_answer_total = 0
    for auditor in current["auditors"]:
        number = int(auditor["number"])
        workbook = (root / CURRENT_DELIVERY / auditor["workbook"]).resolve()
        answers = preserved_answers(workbook)
        rows: list[dict[str, Any]] = []
        for index, (source, answer) in enumerate(zip(auditor["rows"], answers), start=1):
            row = normalized_row(source, index)
            evidence = Path(str(row["evidence_path"]))
            if not evidence.is_file():
                raise FileNotFoundError(evidence)
            evidence_hash = sha256_file(evidence)
            identifier = row_identifier(row)
            if identifier in current_ids:
                raise ValueError(f"duplicate current assignment: {identifier}")
            current_ids.add(identifier)
            used_hashes.add(evidence_hash)
            current_hash_counts[evidence_hash] += 1
            row["evidence_sha256"] = evidence_hash
            row["preserved_answer_code"] = answer
            row["assignment_origin"] = "preserved_current_assignment"
            preserved_answer_total += int(bool(answer))
            rows.append(row)
        current_by_number[number] = {
            "source": auditor,
            "source_workbook": workbook,
            "rows": rows,
        }

    excluded_ids = assigned_ids | completed_ids | agreement_ids | current_ids
    eligible: list[dict[str, Any]] = []
    seen_eligible: set[str] = set()
    for source in primary["rows"]:
        identifier = row_identifier(source)
        if (
            not identifier
            or identifier not in staged
            or identifier in excluded_ids
            or identifier in seen_eligible
        ):
            continue
        evidence = Path(str(source.get("evidence_path") or ""))
        if not evidence.is_file():
            continue
        row = dict(source)
        row["evidence_path"] = evidence.resolve().as_posix()
        row["evidence_sha256"] = sha256_file(evidence)
        row["assignment_origin"] = "fresh_primary_reviewed_pending_gates"
        eligible.append(row)
        seen_eligible.add(identifier)

    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        strata[(str(row["task"]), str(row.get("reserved_split") or ""))].append(row)

    source_usage: Counter[str] = Counter(
        source_group(row)
        for data in current_by_number.values()
        for row in data["rows"]
    )
    selected_by_stratum = {
        ("microtext", split): select_diverse(
            strata[("microtext", split)], 36, used_hashes, source_usage
        )
        for split in ("train", "dev", "test")
    }
    selected_by_stratum[("visualdiff", "test")] = select_diverse(
        strata[("visualdiff", "test")], 36, used_hashes, source_usage
    )

    auditors: list[dict[str, Any]] = []
    fresh_ids: list[str] = []
    for number in range(1, 13):
        existing = current_by_number[number]
        fresh: list[dict[str, Any]] = []
        for key in (
            ("microtext", "train"),
            ("microtext", "dev"),
            ("microtext", "test"),
            ("visualdiff", "test"),
        ):
            start = (number - 1) * 3
            fresh.extend(selected_by_stratum[key][start : start + 3])
        fresh_rows = [
            normalized_row(row, display_index)
            for display_index, row in enumerate(fresh, start=13)
        ]
        for row in fresh_rows:
            row["preserved_answer_code"] = ""
            fresh_ids.append(row_identifier(row))
        source_round = int(existing["source"].get("round") or 1)
        auditors.append(
            {
                "number": number,
                "round": source_round,
                "sheet_name": "审核24条",
                "machine_sheet_name": "机器数据_勿改",
                "workbook": f"AUDITOR_{number:02d}_CURRENT_REVIEW_24.xlsx",
                "source_workbook": existing["source_workbook"].as_posix(),
                "preserved_rows": 12,
                "fresh_rows": 12,
                "preserved_answers": sum(
                    bool(row["preserved_answer_code"]) for row in existing["rows"]
                ),
                "rows": existing["rows"] + fresh_rows,
            }
        )

    all_rows = [row for auditor in auditors for row in auditor["rows"]]
    all_ids = [row_identifier(row) for row in all_rows]
    all_hashes = [str(row["evidence_sha256"]) for row in all_rows]
    fresh_hashes = [
        str(row["evidence_sha256"])
        for auditor in auditors
        for row in auditor["rows"][12:]
    ]
    if len(all_ids) != 288 or len(set(all_ids)) != 288:
        raise ValueError("extended assignments are not 288 unique records")
    if len(set(fresh_hashes)) != 144:
        raise ValueError("fresh assignments contain exact evidence duplicates")
    if set(fresh_hashes) & set(current_hash_counts):
        raise ValueError("fresh evidence duplicates a preserved current image")
    if set(fresh_ids) & assigned_ids:
        raise ValueError("fresh assignments overlap historical assignments")
    if set(fresh_ids) & agreement_ids:
        raise ValueError("fresh assignments overlap agreement bridge")

    counts = {
        "primary": 0,
        "auditors": 12,
        "rows_per_auditor": 24,
        "auditor_rows": 288,
        "preserved_rows": 144,
        "fresh_rows": 144,
        "preserved_answer_cells": preserved_answer_total,
        "task_counts": dict(sorted(Counter(row["task"] for row in all_rows).items())),
        "split_counts": dict(
            sorted(Counter(str(row.get("reserved_split") or "") for row in all_rows).items())
        ),
        "fresh_task_counts": dict(
            sorted(
                Counter(
                    row["task"]
                    for auditor in auditors
                    for row in auditor["rows"][12:]
                ).items()
            )
        ),
        "fresh_split_counts": dict(
            sorted(
                Counter(
                    str(row.get("reserved_split") or "")
                    for auditor in auditors
                    for row in auditor["rows"][12:]
                ).items()
            )
        ),
        "unique_record_ids": len(set(all_ids)),
        "unique_evidence_sha256": len(set(all_hashes)),
        "inherited_current_duplicate_image_rows": sum(
            count - 1 for count in current_hash_counts.values() if count > 1
        ),
        "eligible_pool_before_selection": len(eligible),
        "eligible_source_groups": len({source_group(row) for row in eligible}),
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": (
            "extended current independent audit; preserve existing 12 rows and answers, "
            "append 12 fresh primary-reviewed pending-gate rows"
        ),
        "allowed_prefilled_workbooks": {
            auditor["workbook"]: auditor["preserved_answers"]
            for auditor in auditors
            if auditor["preserved_answers"]
        },
        "primary": {"issued": False, "workbook": "PRIMARY_NOT_ISSUED.xlsx", "rows": []},
        "auditors": auditors,
        "counts": counts,
        "exclusions": {
            "historical_assignment_ids": len(assigned_ids),
            "completed_decision_ids": len(completed_ids),
            "agreement_bridge_ids": len(agreement_ids),
            "historical_payloads": assignment_files,
            "completed_decision_files": decision_files,
        },
        "safety": {
            "fresh_rows_primary_reviewed_pending_gates": True,
            "historical_assignment_overlap": 0,
            "formal_agreement_overlap": 0,
            "duplicate_record_ids": 0,
            "fresh_duplicate_evidence_sha256": 0,
            "fresh_vs_preserved_evidence_overlap": 0,
            "inherited_current_duplicate_image_rows": counts[
                "inherited_current_duplicate_image_rows"
            ],
            "safe_to_merge_gold": False,
            "gold_rows_modified": 0,
        },
        "gold_rows_modified": 0,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "payload_valid": True,
        "counts": counts,
        "exclusions": payload["exclusions"],
        "safety": payload["safety"],
    }
    guide = """Eng_Bench Gold v2.0 Global - 12位独立复审员（每人24题）\n\n\
【发放方法】\n\
1. 每位复审员只收到与自己编号相同的一个 XLSX 文件。\n\
2. 不要把其他人的文件、旧 ZIP 或项目资料发给复审员。\n\
3. 复审员使用桌面版 Excel 或 WPS 打开，完成后原文件名保存并交回。\n\n\
【复审员只需要做一件事】\n\
逐行查看 B 列图片和 C 列问题，在黄色 D 列输入 1、2 或 3，然后按 Enter。\n\
- 1 = 是/通过\n\
- 2 = 否/存在问题\n\
- 3 = 图片看不清或证据不足\n\n\
【MicroText 判断】\n\
1 表示：图片里确实是有效工程标签，并且机器文字和类别都正确。\n\
2 表示：不是有效工程标签，或文字有错，或类别有错。任一项不对都选 2。\n\
3 表示：过于模糊或边界不清，无法只凭图片可靠判断；完整目标明确缺字选2，不要猜测补全。\n\
类别示例：pin_label=引脚/端子/网络名/器件位号（GPIO12/J5/3V3/R101）；equipment_tag=设备名称或位号（SUMP PUMP/P-101）；instrument_tag=仪表/回路；\n\
dimension_value=几何尺寸；room_label=房间/区域；process_label=完整工艺步骤/物料流名称；\n\
process_value=工艺参数；component_value=完整无源器件数值（10uF/4K7），不是芯片型号；unknown_microtext=机器未分类，需特别判断。\n\n\
【VisualDiff 判断】\n\
1 表示：新旧图存在真实工程含义变化，例如器件、连接、尺寸、位号或技术要求改变。\n\
2 表示：完全相同，或只有轻微偏移、排版、线宽、字体、扫描/渲染差异，没有工程含义变化。\n\
3 表示：图片不全或太模糊，无法可靠判断。\n\n\
【重要】\n\
- 不要修改 B/C/E 列，不要添加备注，不要查看隐藏工作表。\n\
- 前12题是当前仍需保留的任务；后12题是本次新增任务。\n\
- 如果文件里已有答案，不要清空；只继续填写空白黄色格。\n\
- 不能复制上一轮答案到本轮新增题；每一题都按当前图片独立判断。\n\
- E5 显示完成数。达到 24 / 24 后 Ctrl+S 保存，不改文件名。\n\
- 速记：1=是，2=否，3=看不清。\n\
- 复审结果只用于质量控制，不会自动写入 Gold。\n\
"""
    return payload, report, guide


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--guide", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    payload, report, guide = build(root)
    for path, content in (
        (args.output_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
        (args.report_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (args.guide, guide),
    ):
        target = (root / path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8-sig" if target.suffix == ".txt" else "utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
