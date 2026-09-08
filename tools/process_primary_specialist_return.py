#!/usr/bin/env python3
"""Validate and stage the engineering-specialist sheets in a primary workbook.

The specialist sheets are deliberately processed separately from the main
primary review. Outputs remain pending all release gates and are never written
to active Gold by this command.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.process_primary_intern_catchup_return import (
    MICRO_CATEGORIES,
    VISUAL_TYPES,
    engineering_note_valid,
    normalized,
    original_visual_type,
    read_workbook,
    sha256_file,
    text,
    write_json,
    write_jsonl,
)


SPECIALIST_SHEETS = (
    "专项_英文描述15",
    "专项_MicroText3",
    "机器数据_专项勿改",
)
VISUAL_STATUSES = {"accepted", "edited", "rejected", "needs_full_page"}
MICRO_STATUSES = VISUAL_STATUSES

VISUAL_HEADERS = [
    "#",
    "OLD / NEW 证据",
    "OLD -> NEW 文字",
    "中文已审描述",
    "机器类型",
    "Type required",
    "Proposed English",
    "审核状态",
    "Corrected English",
    "Corrected type",
    "Notes",
    "完成状态",
]
MICRO_HEADERS = [
    "#",
    "证据图片",
    "Proposed text",
    "Proposed category",
    "审核状态",
    "Corrected text",
    "Corrected category",
    "工程依据",
    "Notes",
    "完成状态",
]
MACHINE_HEADERS = [
    "specialist_index",
    "task",
    "record_id",
    "reserved_split",
    "source_group",
    "evidence_path",
    "evidence_sha256",
    "type_required",
    "safe_to_merge_gold",
]


def as_bool(value: Any) -> bool | None:
    value = normalized(value)
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    return None


def cells_equal(actual: Any, expected: Any) -> bool:
    return text(actual).replace("\\", "/") == text(expected).replace("\\", "/")


def validate_header(
    sheet: str,
    rows: dict[int, dict[int, str]],
    expected: list[str],
    row_number: int,
) -> list[dict[str, Any]]:
    actual = rows.get(row_number, {})
    return [
        {
            "sheet": sheet,
            "row": row_number,
            "field": value,
            "reason": "header_changed",
            "actual": text(actual.get(index)),
        }
        for index, value in enumerate(expected)
        if text(actual.get(index)) != value
    ]


def validate_specialist_identity(
    sheet: str,
    rows: dict[int, dict[int, str]],
    payload_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    headers = VISUAL_HEADERS if sheet == "专项_英文描述15" else MICRO_HEADERS
    issues = validate_header(sheet, rows, headers, 6)
    for offset, expected in enumerate(payload_rows, 7):
        actual = rows.get(offset, {})
        if sheet == "专项_英文描述15":
            comparisons = {
                "specialist_index": (actual.get(0), expected["specialist_index"]),
                "old_new_text": (
                    actual.get(2),
                    f"{expected.get('old_text') or '(空)'} -> {expected.get('new_text') or '(空)'}",
                ),
                "chinese_description": (actual.get(3), expected.get("chinese_description")),
                "current_change_type": (actual.get(4), expected.get("current_change_type")),
                "type_required": (actual.get(5), "YES" if expected.get("type_required") else "NO"),
                "proposed_english": (actual.get(6), expected.get("proposed_english_description")),
            }
        else:
            comparisons = {
                "specialist_index": (actual.get(0), expected["specialist_index"]),
                "proposed_text": (actual.get(2), expected.get("proposed_text")),
                "category": (actual.get(3), expected.get("category")),
            }
        for field, (value, expected_value) in comparisons.items():
            if not cells_equal(value, expected_value):
                issues.append(
                    {
                        "sheet": sheet,
                        "row": offset,
                        "record_id": expected["record_id"],
                        "field": field,
                        "reason": "immutable_value_changed",
                        "expected": text(expected_value),
                        "actual": text(value),
                    }
                )
    last_expected = len(payload_rows) + 6
    for row_number, values in rows.items():
        if row_number > last_expected and any(text(value) for value in values.values()):
            issues.append(
                {
                    "sheet": sheet,
                    "row": row_number,
                    "field": "row",
                    "reason": "unexpected_extra_row",
                }
            )
    return issues


def validate_machine_sheet(
    rows: dict[int, dict[int, str]],
    payload_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sheet = "机器数据_专项勿改"
    issues = validate_header(sheet, rows, MACHINE_HEADERS, 1)
    for offset, expected in enumerate(payload_rows, 2):
        actual = rows.get(offset, {})
        comparisons = {
            "specialist_index": (actual.get(0), expected["specialist_index"]),
            "task": (actual.get(1), expected["task"]),
            "record_id": (actual.get(2), expected["record_id"]),
            "reserved_split": (actual.get(3), expected["reserved_split"]),
            "source_group": (actual.get(4), expected["source_group"]),
            "evidence_path": (actual.get(5), expected["evidence_path"]),
            "evidence_sha256": (actual.get(6), expected["evidence_sha256"]),
        }
        for field, (value, expected_value) in comparisons.items():
            if not cells_equal(value, expected_value):
                issues.append(
                    {
                        "sheet": sheet,
                        "row": offset,
                        "record_id": expected["record_id"],
                        "field": field,
                        "reason": "immutable_value_changed",
                        "expected": text(expected_value),
                        "actual": text(value),
                    }
                )
        if as_bool(actual.get(7)) is not bool(expected.get("type_required")):
            issues.append(
                {
                    "sheet": sheet,
                    "row": offset,
                    "record_id": expected["record_id"],
                    "field": "type_required",
                    "reason": "immutable_value_changed",
                }
            )
        if as_bool(actual.get(8)) is not False:
            issues.append(
                {
                    "sheet": sheet,
                    "row": offset,
                    "record_id": expected["record_id"],
                    "field": "safe_to_merge_gold",
                    "reason": "immutable_value_changed",
                }
            )
    last_expected = len(payload_rows) + 1
    for row_number, values in rows.items():
        if row_number > last_expected and any(text(value) for value in values.values()):
            issues.append(
                {
                    "sheet": sheet,
                    "row": row_number,
                    "field": "row",
                    "reason": "unexpected_extra_row",
                }
            )
    return issues


def interpret_visual(row: dict[str, Any], cells: dict[int, str]) -> dict[str, Any]:
    status = normalized(cells.get(7))
    corrected_english = text(cells.get(8))
    corrected_type = normalized(cells.get(9))
    notes = text(cells.get(10))
    reasons: list[str] = []
    if status not in VISUAL_STATUSES:
        reasons.append("missing_or_invalid_status")
    if corrected_type and corrected_type not in VISUAL_TYPES:
        reasons.append("invalid_corrected_type")
    if status == "accepted" and corrected_english:
        reasons.append("accepted_has_corrected_english")
    if status == "edited" and not corrected_english:
        reasons.append("edited_requires_corrected_english")
    if status in {"accepted", "edited"} and row.get("type_required") and not corrected_type:
        reasons.append("type_required_missing_corrected_type")
    if status in {"rejected", "needs_full_page"} and not notes:
        reasons.append("hold_requires_notes")
    effective_english = (
        corrected_english if status == "edited" else text(row.get("proposed_english_description"))
    )
    effective_type = corrected_type or original_visual_type(row.get("current_change_type"))
    if status in {"accepted", "edited"} and not effective_english:
        reasons.append("usable_row_missing_english")
    return {
        "specialist_index": row["specialist_index"],
        "record_id": row["record_id"],
        "task": row["task"],
        "status": status,
        "corrected_english": corrected_english,
        "corrected_type": corrected_type,
        "effective_english": effective_english,
        "effective_type": effective_type,
        "notes": notes,
        "blocking_reasons": sorted(set(reasons)),
        "ready": not reasons,
    }


def interpret_micro(row: dict[str, Any], cells: dict[int, str]) -> dict[str, Any]:
    status = normalized(cells.get(4))
    corrected_text = text(cells.get(5))
    corrected_category = normalized(cells.get(6))
    engineering_basis = text(cells.get(7))
    notes = text(cells.get(8))
    reasons: list[str] = []
    if status not in MICRO_STATUSES:
        reasons.append("missing_or_invalid_status")
    if corrected_category and corrected_category not in MICRO_CATEGORIES:
        reasons.append("invalid_corrected_category")
    if status == "accepted" and (corrected_text or corrected_category):
        reasons.append("accepted_has_corrections")
    if status == "edited" and not (corrected_text or corrected_category):
        reasons.append("edited_requires_text_or_category")
    if status in {"accepted", "edited"} and not engineering_note_valid(engineering_basis):
        reasons.append("engineering_basis_missing_or_too_generic")
    if status in {"rejected", "needs_full_page"} and not notes:
        reasons.append("hold_requires_notes")
    effective_text = corrected_text if status == "edited" and corrected_text else text(row.get("proposed_text"))
    effective_category = (
        corrected_category if status == "edited" and corrected_category else normalized(row.get("category"))
    )
    if status in {"accepted", "edited"} and not effective_text:
        reasons.append("usable_row_missing_text")
    if status in {"accepted", "edited"} and effective_category not in MICRO_CATEGORIES:
        reasons.append("usable_row_invalid_category")
    if status in {"accepted", "edited"} and effective_category == "unknown_microtext":
        reasons.append("usable_row_unknown_category")
    return {
        "specialist_index": row["specialist_index"],
        "record_id": row["record_id"],
        "task": row["task"],
        "status": status,
        "corrected_text": corrected_text,
        "corrected_category": corrected_category,
        "effective_text": effective_text,
        "effective_category": effective_category,
        "engineering_basis": engineering_basis,
        "notes": notes,
        "blocking_reasons": sorted(set(reasons)),
        "ready": not reasons,
    }


def stage_specialist_row(
    source: dict[str, Any],
    decision: dict[str, Any],
    workbook_sha256: str,
    date_label: str,
) -> dict[str, Any]:
    staged = dict(source)
    staged.update(
        {
            "human_completion_date_label": date_label,
            "human_completion_source": "primary_intern_engineering_specialist_workbook",
            "human_completion_workbook_sha256": workbook_sha256,
            "human_review_status": decision["status"],
            "review_depth": "single_review_engineering_specialist",
            "promotion_state": "human_reviewed_pending_release_gates",
            "safe_to_merge_gold": False,
        }
    )
    if source["task"] == "visualdiff_english":
        staged.update(
            {
                "pair_id": source["record_id"],
                "human_english_description": decision["effective_english"],
                "human_change_type": decision["effective_type"],
                "specialist_notes": decision["notes"],
            }
        )
    else:
        staged.update(
            {
                "candidate_id": source["record_id"],
                "proposed_text": decision["effective_text"],
                "category": decision["effective_category"],
                "engineering_review_basis": decision["engineering_basis"],
                "specialist_notes": decision["notes"],
            }
        )
    return staged


def rework_action(reasons: list[str]) -> str:
    values = set(reasons)
    if "missing_or_invalid_status" in values:
        return "在审核状态列选择 accepted、edited、rejected 或 needs_full_page。"
    if "edited_requires_corrected_english" in values:
        return "选择 edited 后必须填写完整 Corrected English。"
    if "type_required_missing_corrected_type" in values:
        return "Type required=YES；请填写规范 Corrected type。"
    if "edited_requires_text_or_category" in values:
        return "选择 edited 后至少填写 Corrected text 或 Corrected category。"
    if "engineering_basis_missing_or_too_generic" in values:
        return "accepted/edited 必须写至少 6 个有效字符的工程类别依据，不能只写“正确/对”。"
    if "hold_requires_notes" in values:
        return "rejected/needs_full_page 必须在 Notes 说明原因。"
    if any("changed" in value for value in values):
        return "请用原始工作簿重填，不要修改序号、机器内容或机器数据页。"
    return "按工作表顶部说明修正本行；不确定时选 needs_full_page 并写清原因。"


def write_rework(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["task", "specialist_index", "record_id", "blocking_reasons", "action"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    return "\n".join(
        [
            "# Primary Intern Engineering Specialist Return",
            "",
            "- Goal: **Gold v2.0 Global**",
            f"- Processing complete: **{str(report['complete']).lower()}**",
            "- Safe to merge Gold: **false**",
            f"- Specialist actions ready: **{totals['ready']}/{totals['expected']}**",
            f"- VisualDiff English ready: **{totals['visual_ready']}/{totals['visual_expected']}**",
            f"- MicroText engineering ready: **{totals['micro_ready']}/{totals['micro_expected']}**",
            f"- Usable rows staged pending gates: **{totals['staged']}**",
            f"- Human holds: **{totals['holds']}**",
            f"- Rework rows: **{totals['rework']}**",
            f"- Structure/identity issues: **{totals['structural_issues']}**",
            "- Gold rows modified: **0**",
            "",
            "All staged rows still require provenance, evidence, split, leakage, duplicate, and strict promotion gates.",
            "",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    workbook = Path(args.workbook)
    if not workbook.is_absolute():
        workbook = (root / workbook).resolve()
    payload_path = Path(args.payload)
    if not payload_path.is_absolute():
        payload_path = (root / payload_path).resolve()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (root / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    visual = payload.get("specialist", {}).get("visualdiff_english", [])
    micro = payload.get("specialist", {}).get("microtext_balance", [])
    expected = [*visual, *micro]
    structural: list[dict[str, Any]] = []
    counts = payload.get("counts", {})
    if len(expected) != int(counts.get("specialist_total") or 0):
        structural.append(
            {
                "reason": "payload_specialist_count_mismatch",
                "expected": counts.get("specialist_total"),
                "actual": len(expected),
            }
        )
    workbook_rows, workbook_issues = read_workbook(workbook, SPECIALIST_SHEETS)
    structural.extend({"reason": issue} for issue in workbook_issues)
    if not workbook_issues:
        structural.extend(validate_specialist_identity("专项_英文描述15", workbook_rows["专项_英文描述15"], visual))
        structural.extend(validate_specialist_identity("专项_MicroText3", workbook_rows["专项_MicroText3"], micro))
        structural.extend(validate_machine_sheet(workbook_rows["机器数据_专项勿改"], expected))

    workbook_sha = sha256_file(workbook)
    decisions: list[dict[str, Any]] = []
    if not workbook_issues:
        decisions.extend(
            interpret_visual(row, workbook_rows["专项_英文描述15"].get(7 + offset, {}))
            for offset, row in enumerate(visual)
        )
        decisions.extend(
            interpret_micro(row, workbook_rows["专项_MicroText3"].get(7 + offset, {}))
            for offset, row in enumerate(micro)
        )
    source_by_key = {(row["task"], row["record_id"]): row for row in expected}
    structural_ids = {text(issue.get("record_id")) for issue in structural if issue.get("record_id")}
    staged: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    rework: list[dict[str, Any]] = []
    for decision in decisions:
        identity = decision["record_id"]
        source = source_by_key[(decision["task"], identity)]
        reasons = list(decision["blocking_reasons"])
        if structural:
            reasons.append("workbook_structure_or_identity_issue")
        if identity in structural_ids:
            reasons.append("identity_or_structure_changed")
        reasons = sorted(set(reasons))
        if reasons:
            rework.append(
                {
                    "task": decision["task"],
                    "specialist_index": decision["specialist_index"],
                    "record_id": identity,
                    "blocking_reasons": ";".join(reasons),
                    "action": rework_action(reasons),
                }
            )
        elif decision["status"] in {"accepted", "edited"}:
            staged.append(stage_specialist_row(source, decision, workbook_sha, args.date_label))
        else:
            holds.append(
                {
                    **source,
                    "human_completion_date_label": args.date_label,
                    "human_completion_workbook_sha256": workbook_sha,
                    "human_review_status": decision["status"],
                    "specialist_notes": decision["notes"],
                    "promotion_state": "human_hold",
                    "safe_to_merge_gold": False,
                }
            )

    ready_decisions = [decision for decision in decisions if decision["ready"]]
    ready_visual = [decision for decision in ready_decisions if decision["task"] == "visualdiff_english"]
    ready_micro = [decision for decision in ready_decisions if decision["task"] == "microtext_balance"]
    complete = (
        not structural
        and len(decisions) == len(expected)
        and len(ready_decisions) == len(expected)
    )
    totals = {
        "expected": len(expected),
        "ready": len(ready_decisions),
        "visual_expected": len(visual),
        "visual_ready": len(ready_visual),
        "micro_expected": len(micro),
        "micro_ready": len(ready_micro),
        "staged": len(staged),
        "holds": len(holds),
        "rework": len(rework),
        "structural_issues": len(structural),
        "status_counts": dict(sorted(Counter(decision["status"] or "blank" for decision in decisions).items())),
    }
    report = {
        "goal": payload.get("goal", "Gold v2.0 Global"),
        "complete": complete,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "workbook": str(workbook),
        "workbook_sha256": workbook_sha,
        "payload": str(payload_path),
        "payload_sha256": sha256_file(payload_path),
        "date_label": args.date_label,
        "totals": totals,
        "structural_issues": structural,
        "outputs": {},
    }
    paths = {
        "visual_staged": output_dir / f"specialist_visualdiff_reviewed_pending_gates_{args.date_label}.jsonl",
        "micro_staged": output_dir / f"specialist_microtext_reviewed_pending_gates_{args.date_label}.jsonl",
        "holds": output_dir / f"specialist_holds_{args.date_label}.jsonl",
        "rework": output_dir / f"specialist_resume_rework_{args.date_label}.csv",
        "report_json": output_dir / f"specialist_return_processing_{args.date_label}.json",
        "report_md": output_dir / f"specialist_return_processing_{args.date_label}.md",
    }
    write_jsonl(paths["visual_staged"], [row for row in staged if row["task"] == "visualdiff_english"])
    write_jsonl(paths["micro_staged"], [row for row in staged if row["task"] == "microtext_balance"])
    write_jsonl(paths["holds"], holds)
    write_rework(paths["rework"], rework)
    report["outputs"] = {name: str(path) for name, path in paths.items()}
    write_json(paths["report_json"], report)
    paths["report_md"].write_text(render_markdown(report), encoding="utf-8")
    if not args.no_snapshot:
        snapshot_dir = output_dir / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workbook, snapshot_dir / workbook.name)
        shutil.copy2(payload_path, snapshot_dir / payload_path.name)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and not complete else (1 if structural else 0)


if __name__ == "__main__":
    raise SystemExit(main())
