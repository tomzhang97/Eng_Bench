#!/usr/bin/env python3
"""Select a deterministic source- and category-balanced microtext review cohort."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalized_answer(row: dict[str, Any]) -> str:
    value = str(row.get("proposed_text") or row.get("target_text") or "")
    return " ".join(value.casefold().split())


def parse_category_caps(values: list[str]) -> dict[str, int]:
    caps: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"category cap must use CATEGORY=COUNT: {value}")
        category, count_text = value.split("=", 1)
        category = category.strip()
        count = int(count_text)
        if not category or count < 0:
            raise ValueError(f"invalid category cap: {value}")
        caps[category] = count
    return caps


def resolve_allowed_doc_ids(
    explicit_doc_ids: list[str],
    planned_doc_ids: set[str] | None,
) -> set[str] | None:
    explicit = {value.strip() for value in explicit_doc_ids if value.strip()}
    if not explicit:
        return planned_doc_ids
    if planned_doc_ids is None:
        return explicit
    return explicit & planned_doc_ids


def reactivate_for_review(row: dict[str, Any]) -> None:
    """Clear stale hold/supersession metadata on a newly selected row."""
    row["review_status"] = "needs_review"
    row.pop("superseded_by", None)
    row.pop("machine_hold_reason", None)
    row.pop("cross_packet_collision_path", None)
    if str(row.get("machine_qa_status") or "").strip().lower() == "superseded":
        row.pop("machine_qa_status", None)
        row.pop("machine_qa_notes", None)


def rows_not_selected(
    rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {
        str(row.get("candidate_id") or "").strip()
        for row in selected_rows
        if str(row.get("candidate_id") or "").strip()
    }
    return [
        dict(row)
        for row in rows
        if str(row.get("candidate_id") or "").strip() not in selected_ids
    ]


def select_rows(
    rows: list[dict[str, Any]],
    category_order: list[str],
    target_rows: int,
    max_per_doc: int,
    max_per_doc_category: int,
    max_per_doc_answer: int,
    category_caps: dict[str, int] | None = None,
    allowed_doc_ids: set[str] | None = None,
    answer_baseline_rows: list[dict[str, Any]] | None = None,
    min_ocr_confidence: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if target_rows < 0 or max_per_doc < 1 or max_per_doc_category < 1 or max_per_doc_answer < 1:
        raise ValueError("selection limits must be positive, except target_rows may be zero")
    if not 0.0 <= min_ocr_confidence <= 1.0:
        raise ValueError("minimum OCR confidence must be between 0 and 1")
    if len(category_order) != len(set(category_order)):
        raise ValueError("category order contains duplicates")

    allowed = set(category_order)
    pools: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    input_categories: Counter[str] = Counter()
    excluded_below_ocr_confidence = 0
    for row in rows:
        category = str(row.get("category") or "").strip()
        if category not in allowed:
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
            continue
        candidate = str(row.get("candidate_id") or "").strip()
        answer = normalized_answer(row)
        if not doc_id or not candidate or not answer:
            continue
        if min_ocr_confidence > 0:
            try:
                confidence = float(row.get("ocr_confidence"))
            except (TypeError, ValueError):
                excluded_below_ocr_confidence += 1
                continue
            if confidence < min_ocr_confidence:
                excluded_below_ocr_confidence += 1
                continue
        pools[doc_id][category].append(row)
        input_categories[category] += 1

    for doc_pools in pools.values():
        for category, category_rows in doc_pools.items():
            category_rows.sort(
                key=lambda row: (
                    int(row.get("page_index") or row.get("page") or 0),
                    tuple(row.get("bbox") or row.get("bbox_px") or ()),
                    str(row.get("candidate_id") or ""),
                )
            )

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    doc_counts: Counter[str] = Counter()
    doc_category_counts: Counter[tuple[str, str]] = Counter()
    doc_answer_counts: Counter[tuple[str, str]] = Counter()
    baseline_answer_rows = 0
    for row in answer_baseline_rows or []:
        doc_id = str(row.get("doc_id") or "").strip()
        answer = normalized_answer(row)
        if not doc_id or not answer:
            continue
        doc_answer_counts[(doc_id, answer)] += 1
        baseline_answer_rows += 1
    category_counts: Counter[str] = Counter()
    indexes: Counter[tuple[str, str]] = Counter()
    docs = sorted(pools)
    caps = category_caps or {}

    while len(selected) < target_rows:
        made_progress = False
        for category in category_order:
            cap = caps.get(category)
            if cap is not None and category_counts[category] >= cap:
                continue
            for doc_id in docs:
                if len(selected) >= target_rows:
                    break
                if doc_counts[doc_id] >= max_per_doc:
                    continue
                key = (doc_id, category)
                if doc_category_counts[key] >= max_per_doc_category:
                    continue
                category_rows = pools[doc_id].get(category, [])
                index = indexes[key]
                while index < len(category_rows):
                    row = category_rows[index]
                    index += 1
                    identity = str(row.get("candidate_id") or "")
                    answer_key = (doc_id, normalized_answer(row))
                    if identity in selected_ids:
                        continue
                    if doc_answer_counts[answer_key] >= max_per_doc_answer:
                        continue
                    selected_row = dict(row)
                    reactivate_for_review(selected_row)
                    selected_row["balanced_selection_status"] = "selected"
                    selected_row["balanced_selection_category_priority"] = category_order.index(category) + 1
                    selected.append(selected_row)
                    selected_ids.add(identity)
                    doc_counts[doc_id] += 1
                    doc_category_counts[key] += 1
                    doc_answer_counts[answer_key] += 1
                    category_counts[category] += 1
                    made_progress = True
                    break
                indexes[key] = index
                if cap is not None and category_counts[category] >= cap:
                    break
        if not made_progress:
            break

    report = {
        "totals": {
            "input_rows": len(rows),
            "eligible_rows": sum(input_categories.values()),
            "selected_rows": len(selected),
            "selected_source_docs": len(doc_counts),
            "unique_selected_candidate_ids": len(selected_ids),
        },
        "limits": {
            "target_rows": target_rows,
            "max_per_doc": max_per_doc,
            "max_per_doc_category": max_per_doc_category,
            "max_per_doc_answer": max_per_doc_answer,
            "category_order": category_order,
            "category_caps": caps,
            "allowed_doc_ids": len(allowed_doc_ids) if allowed_doc_ids is not None else None,
            "answer_baseline_rows": baseline_answer_rows,
            "minimum_ocr_confidence": min_ocr_confidence,
            "excluded_below_ocr_confidence": excluded_below_ocr_confidence,
        },
        "eligible_categories": dict(sorted(input_categories.items())),
        "selected_categories": dict(sorted(category_counts.items())),
        "selected_documents": dict(sorted(doc_counts.items())),
        "interpretation": (
            "This is deterministic machine selection only. Selected rows still require strict "
            "evidence/provenance assembly and visual QA before human assignment."
        ),
    }
    return selected, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    limits = report["limits"]
    lines = [
        "# Balanced Microtext Review Pool Selection",
        "",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Eligible rows: `{totals['eligible_rows']}`",
        f"- Selected rows: `{totals['selected_rows']}`",
        f"- Selected source documents: `{totals['selected_source_docs']}`",
        f"- Unique selected candidate IDs: `{totals['unique_selected_candidate_ids']}`",
        f"- Remaining input rows: `{totals.get('remaining_input_rows', 0)}`",
        f"- Per-document cap: `{limits['max_per_doc']}`",
        f"- Per-document/category cap: `{limits['max_per_doc_category']}`",
        f"- Per-document/answer cap: `{limits['max_per_doc_answer']}`",
        f"- Prior-cohort answer baseline rows: `{limits['answer_baseline_rows']}`",
        "",
        "## Selected Categories",
        "",
    ]
    for category, count in report["selected_categories"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Source Distribution", ""])
    for doc_id, count in report["selected_documents"].items():
        lines.append(f"- `{doc_id}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--category", action="append", required=True)
    parser.add_argument("--category-cap", action="append", default=[])
    parser.add_argument("--target-rows", type=int, default=1000)
    parser.add_argument("--max-per-doc", type=int, default=12)
    parser.add_argument("--max-per-doc-category", type=int, default=8)
    parser.add_argument("--max-per-doc-answer", type=int, default=2)
    parser.add_argument(
        "--min-ocr-confidence",
        type=float,
        default=0.0,
        help=(
            "Require an OCR confidence at or above this value. Rows with missing or "
            "invalid confidence are excluded when the threshold is nonzero."
        ),
    )
    parser.add_argument(
        "--allow-doc-id",
        action="append",
        default=[],
        help="Restrict selection to an explicit document ID; may be repeated.",
    )
    parser.add_argument(
        "--answer-baseline-input",
        action="append",
        type=Path,
        default=[],
        help=(
            "Prior review JSONL whose per-document answers count toward "
            "--max-per-doc-answer; may be repeated."
        ),
    )
    parser.add_argument("--split-plan", type=Path)
    parser.add_argument("--split", choices=("train", "dev", "test"))
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument(
        "--remaining-output-jsonl",
        type=Path,
        help="Optional JSONL containing every input row not selected.",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.split_plan) != bool(args.split):
        parser.error("--split-plan and --split must be supplied together")
    planned_doc_ids = None
    if args.split_plan:
        payload = json.loads(args.split_plan.read_text(encoding="utf-8"))
        if not payload.get("valid", False):
            parser.error("split plan is not valid")
        planned_doc_ids = {
            str(row.get("unit_id") or "").strip()
            for row in payload.get("reservations", [])
            if str(row.get("task") or "") == "microtext"
            and str(row.get("split") or "") == args.split
            and str(row.get("unit_id") or "").strip()
        }
    allowed_doc_ids = resolve_allowed_doc_ids(args.allow_doc_id, planned_doc_ids)
    rows = read_jsonl(args.input)
    answer_baseline_rows = [
        row
        for path in args.answer_baseline_input
        for row in read_jsonl(path)
    ]
    selected, report = select_rows(
        rows,
        category_order=args.category,
        target_rows=args.target_rows,
        max_per_doc=args.max_per_doc,
        max_per_doc_category=args.max_per_doc_category,
        max_per_doc_answer=args.max_per_doc_answer,
        category_caps=parse_category_caps(args.category_cap),
        allowed_doc_ids=allowed_doc_ids,
        answer_baseline_rows=answer_baseline_rows,
        min_ocr_confidence=args.min_ocr_confidence,
    )
    remaining = rows_not_selected(rows, selected)
    report["totals"]["remaining_input_rows"] = len(remaining)
    write_jsonl(args.output_jsonl, selected)
    if args.remaining_output_jsonl:
        write_jsonl(args.remaining_output_jsonl, remaining)
    write_report(args.output_json, report)
    write_markdown(args.output_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
