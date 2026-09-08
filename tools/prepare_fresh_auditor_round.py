#!/usr/bin/env python3
"""Prepare 24 fresh, unanswered rows for each of 12 independent auditors.

Rows come only from the primary-reviewed pending-gate staging pool. The selector
excludes active Gold, every historical auditor assignment, completed auditor
decisions, the formal agreement bridge, duplicate record IDs, and exact
evidence-image duplicates. This tool never edits Gold JSONL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

try:
    from .auditor_rubric import category_guide
except ImportError:
    from auditor_rubric import category_guide


PRIMARY_PAYLOAD = Path(
    "derived/quality/"
    "primary_intern_payload_4000_provenance_complete_engineering_specialist_compact_"
    "2026-08-21-wave292.json"
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
AUDITOR_COUNT = 12
ROWS_PER_AUDITOR = 24
ROWS_PER_STRATUM_PER_AUDITOR = 6
STRATA = (
    ("microtext", "train"),
    ("microtext", "dev"),
    ("microtext", "test"),
    ("visualdiff", "test"),
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
        or row.get("item_id")
        or row.get("id")
        or ""
    ).strip()


def source_group(row: dict[str, Any]) -> str:
    return str(
        row.get("source_group")
        or row.get("project_id")
        or row.get("document_id")
        or row.get("doc_id")
        or row_identifier(row)
    ).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def historical_assignments(
    root: Path, ignored_payloads: set[Path] | None = None
) -> tuple[set[str], set[str], list[str]]:
    assigned_ids: set[str] = set()
    assigned_hashes: set[str] = set()
    used_files: list[str] = []
    ignored = {path.resolve() for path in (ignored_payloads or set())}
    for path in sorted((root / "derived/quality").glob("*audit*payload*.json")):
        if path.resolve() in ignored:
            continue
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        auditors = payload.get("auditors") if isinstance(payload, dict) else None
        if not isinstance(auditors, list):
            continue
        used_files.append(path.relative_to(root).as_posix())
        for auditor in auditors:
            for row in auditor.get("rows") or []:
                identifier = row_identifier(row)
                if identifier:
                    assigned_ids.add(identifier)
                evidence_hash = str(row.get("evidence_sha256") or "").strip()
                if evidence_hash:
                    assigned_hashes.add(evidence_hash)
    return assigned_ids, assigned_hashes, used_files


def completed_assignments(root: Path) -> tuple[set[str], list[str]]:
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
    return {
        identifier
        for relative in STAGED_FILES
        for row in read_jsonl(root / relative)
        if (identifier := row_identifier(row))
    }


def agreement_ids(root: Path) -> set[str]:
    return {
        identifier
        for row in read_jsonl(root / AGREEMENT_BRIDGE)
        if (identifier := row_identifier(row))
    }


def active_gold_ids(root: Path) -> set[str]:
    result: set[str] = set()
    for relative in (
        Path("microtext/annotations/microtext_items.jsonl"),
        Path("visualdiff/annotations/visualdiff_pairs.jsonl"),
    ):
        for row in read_jsonl(root / relative):
            for key in ("candidate_id", "item_id", "pair_id", "id"):
                value = str(row.get(key) or "").strip()
                if value:
                    result.add(value)
    return result


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
    identifier = row_identifier(result)
    task = str(result.get("task") or "").strip()
    result.update(
        {
            "display_index": str(display_index),
            "record_id": identifier,
            "source_group": source_group(result),
            "preserved_answer_code": "",
            "assignment_origin": "fresh_primary_reviewed_pending_gates",
            "auditor_assignment_status": "independent_audit_only",
            "safe_to_merge_gold": False,
        }
    )
    if task == "microtext":
        result["candidate_id"] = identifier
        result.setdefault("corrected_text", "")
        result.setdefault("corrected_category", "")
    elif task == "visualdiff":
        result["pair_id"] = identifier
    else:
        raise ValueError(f"unknown task for {identifier}: {task!r}")
    return result


def build(
    root: Path, ignored_payloads: set[Path] | None = None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    primary = read_json(root / PRIMARY_PAYLOAD)
    assigned_ids, assigned_hashes, assignment_files = historical_assignments(
        root, ignored_payloads
    )
    completed_ids, decision_files = completed_assignments(root)
    staged = staged_ids(root)
    agreement = agreement_ids(root)
    gold_ids = active_gold_ids(root)
    excluded_ids = assigned_ids | completed_ids | agreement | gold_ids

    eligible: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source in primary.get("rows") or []:
        identifier = row_identifier(source)
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
        evidence_hash = sha256_file(evidence)
        if evidence_hash in assigned_hashes:
            continue
        row = dict(source)
        row["evidence_path"] = evidence.resolve().as_posix()
        row["evidence_sha256"] = evidence_hash
        eligible.append(row)
        seen_ids.add(identifier)

    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        strata[(str(row.get("task") or ""), str(row.get("reserved_split") or ""))].append(row)

    required_per_stratum = AUDITOR_COUNT * ROWS_PER_STRATUM_PER_AUDITOR
    used_hashes = set(assigned_hashes)
    source_usage: Counter[str] = Counter()
    selected_by_stratum = {
        key: select_diverse(strata[key], required_per_stratum, used_hashes, source_usage)
        for key in STRATA
    }

    auditors: list[dict[str, Any]] = []
    for number in range(1, AUDITOR_COUNT + 1):
        rows: list[dict[str, Any]] = []
        start = (number - 1) * ROWS_PER_STRATUM_PER_AUDITOR
        stop = start + ROWS_PER_STRATUM_PER_AUDITOR
        for key in STRATA:
            rows.extend(selected_by_stratum[key][start:stop])
        normalized = [
            normalized_row(row, display_index)
            for display_index, row in enumerate(rows, start=1)
        ]
        auditors.append(
            {
                "number": number,
                "round": "fresh_2026_09_01",
                "sheet_name": "审核24条",
                "machine_sheet_name": "机器数据_勿改",
                "workbook": f"AUDITOR_{number:02d}_FRESH_REVIEW_24.xlsx",
                "preserved_rows": 0,
                "fresh_rows": ROWS_PER_AUDITOR,
                "preserved_answers": 0,
                "rows": normalized,
            }
        )

    all_rows = [row for auditor in auditors for row in auditor["rows"]]
    all_ids = [row_identifier(row) for row in all_rows]
    all_hashes = [str(row["evidence_sha256"]) for row in all_rows]
    if len(all_ids) != AUDITOR_COUNT * ROWS_PER_AUDITOR:
        raise ValueError("fresh assignment row count mismatch")
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("fresh assignments contain duplicate record IDs")
    if len(set(all_hashes)) != len(all_hashes):
        raise ValueError("fresh assignments contain exact evidence duplicates")
    if set(all_ids) & excluded_ids:
        raise ValueError("fresh assignments overlap an excluded cohort")
    if set(all_hashes) & assigned_hashes:
        raise ValueError("fresh assignments overlap historical evidence images")

    counts = {
        "auditors": AUDITOR_COUNT,
        "rows_per_auditor": ROWS_PER_AUDITOR,
        "auditor_rows": len(all_rows),
        "fresh_rows": len(all_rows),
        "prefilled_answers": 0,
        "task_counts": dict(sorted(Counter(row["task"] for row in all_rows).items())),
        "split_counts": dict(
            sorted(Counter(str(row.get("reserved_split") or "") for row in all_rows).items())
        ),
        "task_split_counts": {
            f"{task}:{split}": len(rows) for (task, split), rows in selected_by_stratum.items()
        },
        "unique_record_ids": len(set(all_ids)),
        "unique_evidence_sha256": len(set(all_hashes)),
        "eligible_pool_before_selection": len(eligible),
        "eligible_source_groups": len({source_group(row) for row in eligible}),
        "selected_source_groups": len({source_group(row) for row in all_rows}),
    }
    safety = {
        "all_rows_primary_reviewed_pending_gates": True,
        "historical_assignment_overlap": 0,
        "completed_decision_overlap": 0,
        "formal_agreement_overlap": 0,
        "active_gold_overlap": 0,
        "duplicate_record_ids": 0,
        "duplicate_evidence_sha256": 0,
        "prefilled_answers": 0,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "workflow": "fresh independent audit round; 24 unanswered rows per auditor",
        "allowed_prefilled_workbooks": {},
        "primary": {"issued": False, "workbook": "PRIMARY_NOT_ISSUED.xlsx", "rows": []},
        "auditors": auditors,
        "counts": counts,
        "exclusions": {
            "historical_assignment_ids": len(assigned_ids),
            "historical_evidence_sha256": len(assigned_hashes),
            "completed_decision_ids": len(completed_ids),
            "agreement_bridge_ids": len(agreement),
            "active_gold_ids": len(gold_ids),
            "historical_payloads": assignment_files,
            "completed_decision_files": decision_files,
        },
        "safety": safety,
        "gold_rows_modified": 0,
    }
    report = {
        "goal": "Gold v2.0 Global",
        "payload_valid": True,
        "counts": counts,
        "exclusions": payload["exclusions"],
        "safety": safety,
    }
    guide = f"""Eng_Bench Gold v2.0 Global - 12位独立复审员（每人24道全新题）

【管理员发放】
1. 每位复审员只收到与自己编号相同的一个 XLSX 文件。
2. 例如：复审员01只收到 AUDITOR_01_FRESH_REVIEW_24.xlsx。
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
{category_guide()}

【VisualDiff】
- 1：新旧图有真实工程含义变化，例如器件、连接、尺寸、位号或技术要求改变。
- 2：完全相同，或只有轻微偏移、排版、线宽、字体、扫描/渲染差异，没有工程含义变化。
- 3：OLD/NEW缺失、裁剪不全或太模糊，无法可靠判断。

【重要】
- 本轮24题全部是全新、未作答任务；不能复制上一轮答案。
- 每一题都必须按当前图片独立判断。
- 复审结果只用于质量控制，不会自动写入 Gold；回收后仍需机器校验与发布门禁。
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
        target.write_text(content, encoding="utf-8-sig" if target.suffix == ".txt" else "utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
