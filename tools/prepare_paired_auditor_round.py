#!/usr/bin/env python3
"""Prepare a 12-auditor paired agreement round from untouched reviewed rows.

The output contains 144 unique primary-reviewed pending-gate rows. Each row is
assigned to exactly two different auditors, giving every auditor 24 unanswered
questions. Historical assignments, completed decisions, active Gold, the
formal agreement bridge, reused evidence images, and unsafe rows are excluded.
This tool never edits Gold JSONL.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

try:
    import prepare_fresh_auditor_round as fresh
except ModuleNotFoundError:  # Imported as tools.prepare_paired_auditor_round.
    from tools import prepare_fresh_auditor_round as fresh


AUDITOR_COUNT = 12
ROWS_PER_AUDITOR = 24
REVIEWERS_PER_ROW = 2
UNIQUE_ROWS = AUDITOR_COUNT * ROWS_PER_AUDITOR // REVIEWERS_PER_ROW
ROUND_NAME = "paired_round3_2026_09_01"


def stratum(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("task") or "").strip(),
        str(row.get("reserved_split") or "").strip(),
    )


def pair_schedule() -> list[tuple[int, int]]:
    """Return 144 pair slots with degree 24 for each of 12 auditors."""
    complete_graph = [
        (left, right)
        for left in range(1, AUDITOR_COUNT + 1)
        for right in range(left + 1, AUDITOR_COUNT + 1)
    ]
    cycle = [
        (number, number + 1) for number in range(1, AUDITOR_COUNT)
    ] + [(1, AUDITOR_COUNT)]
    pairs = complete_graph + complete_graph + cycle
    degrees: Counter[int] = Counter(number for pair in pairs for number in pair)
    if len(pairs) != UNIQUE_ROWS:
        raise ValueError(f"pair schedule has {len(pairs)} rows, expected {UNIQUE_ROWS}")
    if set(degrees.values()) != {ROWS_PER_AUDITOR}:
        raise ValueError(f"pair schedule has unbalanced degrees: {dict(degrees)}")
    return pairs


def eligible_rows(
    root: Path, ignored_payloads: set[Path] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary = fresh.read_json(root / fresh.PRIMARY_PAYLOAD)
    assigned_ids, assigned_hashes, assignment_files = fresh.historical_assignments(
        root, ignored_payloads
    )
    completed_ids, decision_files = fresh.completed_assignments(root)
    staged = fresh.staged_ids(root)
    agreement = fresh.agreement_ids(root)
    gold_ids = fresh.active_gold_ids(root)
    excluded_ids = assigned_ids | completed_ids | agreement | gold_ids

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for source in primary.get("rows") or []:
        identifier = fresh.row_identifier(source)
        if (
            not identifier
            or identifier not in staged
            or identifier in excluded_ids
            or identifier in seen_ids
        ):
            continue
        evidence = Path(str(source.get("evidence_path") or ""))
        if not evidence.is_file():
            continue
        evidence_hash = fresh.sha256_file(evidence)
        if evidence_hash in assigned_hashes or evidence_hash in seen_hashes:
            continue
        row = dict(source)
        row["evidence_path"] = evidence.resolve().as_posix()
        row["evidence_sha256"] = evidence_hash
        rows.append(row)
        seen_ids.add(identifier)
        seen_hashes.add(evidence_hash)

    context = {
        "historical_assignment_ids": len(assigned_ids),
        "historical_evidence_sha256": len(assigned_hashes),
        "completed_decision_ids": len(completed_ids),
        "agreement_bridge_ids": len(agreement),
        "active_gold_ids": len(gold_ids),
        "historical_payloads": assignment_files,
        "completed_decision_files": decision_files,
        "excluded_ids": excluded_ids,
        "assigned_hashes": assigned_hashes,
    }
    return rows, context


def target_quotas(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], int]:
    counts = Counter(stratum(row) for row in rows)
    quotas = dict(counts)
    while sum(quotas.values()) > UNIQUE_ROWS:
        candidates = [key for key, value in quotas.items() if value > 1]
        if not candidates:
            raise ValueError("eligible strata cannot be reduced to target size")
        chosen = min(candidates, key=lambda key: (-quotas[key], key))
        quotas[chosen] -= 1
    if sum(quotas.values()) < UNIQUE_ROWS:
        raise ValueError(
            f"only {sum(quotas.values())} eligible unique rows for {UNIQUE_ROWS} required"
        )
    return quotas


def select_unique_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    quotas = target_quotas(rows)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[stratum(row)].append(row)
    selected: list[dict[str, Any]] = []
    used_hashes: set[str] = set()
    source_usage: Counter[str] = Counter()
    for key in sorted(quotas):
        selected.extend(
            fresh.select_diverse(
                grouped[key], quotas[key], used_hashes, source_usage
            )
        )
    if len(selected) != UNIQUE_ROWS:
        raise ValueError(f"selected {len(selected)} rows, expected {UNIQUE_ROWS}")
    return selected


def interleave_strata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for row in sorted(rows, key=lambda item: fresh.stable_key(fresh.row_identifier(item))):
        groups[stratum(row)].append(row)
    ordered: list[dict[str, Any]] = []
    while groups:
        for key in sorted(groups, key=lambda item: (-len(groups[item]), item)):
            ordered.append(groups[key].popleft())
            if not groups[key]:
                del groups[key]
    return ordered


def assign_pairs(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    if len(rows) != UNIQUE_ROWS:
        raise ValueError(f"expected {UNIQUE_ROWS} unique rows, got {len(rows)}")
    remaining = pair_schedule()
    assignments: dict[int, list[dict[str, Any]]] = defaultdict(list)
    stratum_counts: Counter[tuple[int, tuple[str, str]]] = Counter()
    task_counts: Counter[tuple[int, str]] = Counter()

    for source in interleave_strata(rows):
        key = stratum(source)
        task = key[0]
        identifier = fresh.row_identifier(source)

        def pair_score(pair: tuple[int, int]) -> tuple[Any, ...]:
            left, right = pair
            return (
                max(stratum_counts[(left, key)], stratum_counts[(right, key)]),
                stratum_counts[(left, key)] + stratum_counts[(right, key)],
                max(task_counts[(left, task)], task_counts[(right, task)]),
                task_counts[(left, task)] + task_counts[(right, task)],
                fresh.stable_key(f"{identifier}:{left}:{right}"),
            )

        pair_index = min(range(len(remaining)), key=lambda index: pair_score(remaining[index]))
        left, right = remaining.pop(pair_index)
        pair_id = f"agreement_pair_{min(left, right):02d}_{max(left, right):02d}"
        for replicate, (auditor, peer) in enumerate(((left, right), (right, left)), start=1):
            row = dict(source)
            row.update(
                {
                    "paired_auditor": peer,
                    "agreement_pair_id": pair_id,
                    "agreement_replicate": replicate,
                    "assignment_origin": "paired_primary_reviewed_pending_gates",
                }
            )
            assignments[auditor].append(row)
            stratum_counts[(auditor, key)] += 1
            task_counts[(auditor, task)] += 1

    if remaining:
        raise ValueError(f"unused pair slots: {len(remaining)}")
    if {len(assignments[number]) for number in range(1, AUDITOR_COUNT + 1)} != {
        ROWS_PER_AUDITOR
    }:
        raise ValueError("paired assignments are not balanced at 24 rows per auditor")
    return assignments


def normalize_for_auditor(
    source: dict[str, Any], display_index: int
) -> dict[str, Any]:
    result = fresh.normalized_row(source, display_index)
    result.update(
        {
            "paired_auditor": int(source["paired_auditor"]),
            "agreement_pair_id": str(source["agreement_pair_id"]),
            "agreement_replicate": int(source["agreement_replicate"]),
            "assignment_origin": "paired_primary_reviewed_pending_gates",
        }
    )
    return result


def build(
    root: Path, ignored_payloads: set[Path] | None = None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    eligible, context = eligible_rows(root, ignored_payloads)
    selected = select_unique_rows(eligible)
    assignments = assign_pairs(selected)
    auditors: list[dict[str, Any]] = []
    for number in range(1, AUDITOR_COUNT + 1):
        rows = [
            normalize_for_auditor(row, index)
            for index, row in enumerate(assignments[number], start=1)
        ]
        ids = [fresh.row_identifier(row) for row in rows]
        if len(set(ids)) != ROWS_PER_AUDITOR:
            raise ValueError(f"auditor {number} contains a repeated record")
        auditors.append(
            {
                "number": number,
                "round": ROUND_NAME,
                "sheet_name": "审核24条",
                "machine_sheet_name": "机器数据_勿改",
                "workbook": f"AUDITOR_{number:02d}_PAIRED_REVIEW_24.xlsx",
                "preserved_rows": 0,
                "fresh_rows": ROWS_PER_AUDITOR,
                "preserved_answers": 0,
                "rows": rows,
            }
        )

    all_rows = [row for auditor in auditors for row in auditor["rows"]]
    record_counts = Counter(fresh.row_identifier(row) for row in all_rows)
    evidence_counts = Counter(str(row["evidence_sha256"]) for row in all_rows)
    if set(record_counts.values()) != {REVIEWERS_PER_ROW}:
        raise ValueError("each record must be assigned to exactly two auditors")
    if len(record_counts) != UNIQUE_ROWS or len(evidence_counts) != UNIQUE_ROWS:
        raise ValueError("paired round unique record/evidence counts are invalid")
    if set(evidence_counts.values()) != {REVIEWERS_PER_ROW}:
        raise ValueError("each evidence image must be assigned exactly twice")
    selected_ids = set(record_counts)
    selected_hashes = set(evidence_counts)
    if selected_ids & context["excluded_ids"]:
        raise ValueError("paired round overlaps an excluded cohort")
    if selected_hashes & context["assigned_hashes"]:
        raise ValueError("paired round reuses historical evidence")

    task_counts = Counter(str(row["task"]) for row in all_rows)
    split_counts = Counter(str(row.get("reserved_split") or "") for row in all_rows)
    unique_task_counts = Counter(str(row["task"]) for row in selected)
    unique_split_counts = Counter(str(row.get("reserved_split") or "") for row in selected)
    unique_task_split_counts = Counter(
        f"{row.get('task')}:{row.get('reserved_split')}" for row in selected
    )
    pair_by_record = {
        fresh.row_identifier(row): str(row["agreement_pair_id"])
        for row in all_rows
    }
    pair_counts = Counter(pair_by_record.values())
    counts = {
        "auditors": AUDITOR_COUNT,
        "rows_per_auditor": ROWS_PER_AUDITOR,
        "auditor_assignment_rows": len(all_rows),
        "unique_review_rows": len(record_counts),
        "reviewers_per_unique_row": REVIEWERS_PER_ROW,
        "prefilled_answers": 0,
        "assignment_task_counts": dict(sorted(task_counts.items())),
        "assignment_split_counts": dict(sorted(split_counts.items())),
        "unique_task_counts": dict(sorted(unique_task_counts.items())),
        "unique_split_counts": dict(sorted(unique_split_counts.items())),
        "unique_task_split_counts": dict(sorted(unique_task_split_counts.items())),
        "eligible_pool_before_selection": len(eligible),
        "eligible_source_groups": len({fresh.source_group(row) for row in eligible}),
        "selected_source_groups": len({fresh.source_group(row) for row in selected}),
        "auditor_pair_count": len(pair_counts),
        "rows_per_auditor_pair_min": min(pair_counts.values()),
        "rows_per_auditor_pair_max": max(pair_counts.values()),
    }
    exclusions = {
        key: value
        for key, value in context.items()
        if key not in {"excluded_ids", "assigned_hashes"}
    }
    safety = {
        "all_rows_primary_reviewed_pending_gates": True,
        "historical_assignment_overlap": 0,
        "completed_decision_overlap": 0,
        "formal_agreement_overlap": 0,
        "active_gold_overlap": 0,
        "duplicate_within_auditor": 0,
        "intentional_cross_auditor_replication": REVIEWERS_PER_ROW,
        "prefilled_answers": 0,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "paired independent agreement audit; 24 unanswered rows per auditor",
        "allowed_prefilled_workbooks": {},
        "primary": {"issued": False, "workbook": "PRIMARY_NOT_ISSUED.xlsx", "rows": []},
        "auditors": auditors,
        "counts": counts,
        "exclusions": exclusions,
        "safety": safety,
        "gold_rows_modified": 0,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "payload_valid": True,
        "counts": counts,
        "exclusions": exclusions,
        "safety": safety,
    }
    guide = f"""Eng_Bench Gold v2.0 Global - 12位独立复审员配对复审（每人24题）

【管理员发放】
1. 每位复审员只收到与自己编号相同的一个 XLSX 文件。
2. 例如：复审员01只收到 AUDITOR_01_PAIRED_REVIEW_24.xlsx。
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
- 1：目标是有效工程标签，且机器文字和类别都正确。
- 2：不是有效工程标签，或文字错误，或类别错误；任一项不对都选2。
- 3：太模糊或边界不清，无法仅凭图片可靠判断；明确缺字属于2。
- 只判断题目对应目标；背景中的其他文字或线条不影响结果。
{fresh.category_guide()}

【VisualDiff】
- 1：新旧图有真实工程含义变化，例如器件、连接、尺寸、位号或技术要求改变。
- 2：完全相同，或只有轻微偏移、排版、线宽、字体、扫描/渲染差异，没有工程含义变化。
- 3：OLD/NEW缺失、裁剪不全或太模糊，无法可靠判断。

【独立复审要求】
- 本轮每题会由两位复审员分别作答，用于计算一致性；这是有意的，不是重复错误。
- 两位复审员不能互看答案、讨论或复制，只能按自己文件里的图片独立判断。
- 24题都是本人未作答的新任务，不能复制上一轮答案。
- 复审结果只用于质量控制，不会自动写入 Gold；回收后仍需机器校验、分歧仲裁和发布门禁。
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
    output_target = (root / args.output_json).resolve()
    payload, report, guide = build(root, {output_target})
    for path, content in (
        (args.output_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
        (args.report_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (args.guide, guide),
    ):
        target = (root / path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            content,
            encoding="utf-8-sig" if target.suffix == ".txt" else "utf-8",
        )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
