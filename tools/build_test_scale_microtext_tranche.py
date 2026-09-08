#!/usr/bin/env python3
"""Build a diverse, review-only MicroText tranche for the test-scale gate."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable


NONPIN_CATEGORIES = {
    "component_value",
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "room_label",
    "tolerance_value",
}
SPLITS = ("train", "dev", "test")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_split_locks(root: Path) -> dict[str, str]:
    locks: dict[str, str] = {}
    for split in SPLITS:
        path = root / "splits" / f"microtext_{split}.txt"
        if not path.is_file():
            raise FileNotFoundError(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            doc_id = line.strip()
            if not doc_id or doc_id.startswith("#"):
                continue
            prior = locks.get(doc_id)
            if prior and prior != split:
                raise ValueError(
                    f"MicroText document appears in multiple splits: {doc_id} ({prior}, {split})"
                )
            locks[doc_id] = split
    return locks


def manifest_doc_rows(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    path = root / "manifest.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    for row in read_jsonl(path):
        if str(row.get("type") or "doc") != "doc":
            continue
        source_doc = str(row.get("doc_id") or "").strip()
        if source_doc:
            rows[source_doc] = row
    return rows


def source_signals(source_doc: str, row: dict[str, Any]) -> set[str]:
    signals = {f"doc_id:{source_doc}"}
    for field in ("sha256", "source_candidate_id", "same_model_id", "source_url"):
        value = str(row.get(field) or "").strip().casefold()
        if value:
            signals.add(f"{field}:{value}")
    return signals


def source_family_split_locks(root: Path) -> dict[str, str]:
    direct_locks = read_split_locks(root)
    docs = manifest_doc_rows(root)
    parent: dict[str, str] = {source_doc: source_doc for source_doc in docs}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    owner: dict[str, str] = {}
    for source_doc, row in sorted(docs.items()):
        for signal in source_signals(source_doc, row):
            prior = owner.get(signal)
            if prior:
                union(source_doc, prior)
            else:
                owner[signal] = source_doc

    component_splits: dict[str, set[str]] = defaultdict(set)
    for source_doc, split in direct_locks.items():
        component_splits[find(source_doc)].add(split)
    conflicts = {
        component: values
        for component, values in component_splits.items()
        if len(values) > 1
    }
    if conflicts:
        detail = ", ".join(
            f"{component}={sorted(values)}" for component, values in sorted(conflicts.items())
        )
        raise ValueError(f"source-family component appears in multiple splits: {detail}")

    effective = dict(direct_locks)
    for source_doc in docs:
        splits = component_splits.get(find(source_doc), set())
        if splits:
            effective[source_doc] = next(iter(splits))
    return effective


def candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("item_id") or row.get("id") or "").strip()


def doc_id(row: dict[str, Any]) -> str:
    return str(row.get("doc_id") or row.get("source_doc_id") or "").strip()


def category(row: dict[str, Any]) -> str:
    return str(row.get("category") or row.get("region_type") or "").strip().lower()


def text_value(row: dict[str, Any]) -> str:
    return str(row.get("proposed_text") or row.get("target_text") or "").strip()


def normalized_text(row: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", text_value(row)).strip().casefold()


def page_index(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("page_index"))
    except (TypeError, ValueError):
        return None


def bbox(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    value = row.get("bbox") or row.get("bbox_px")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(int(round(float(part))) for part in value)
    except (TypeError, ValueError):
        return None
    x0, y0, x1, y1 = box
    return box if min(x0, y0) >= 0 and x1 > x0 and y1 > y0 else None


def row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        doc_id(row),
        page_index(row) if page_index(row) is not None else 10**9,
        bbox(row) or (10**9, 10**9, 10**9, 10**9),
        candidate_id(row),
    )


def diverse_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_doc: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in sorted(rows, key=row_key):
        by_doc[doc_id(row)].append(row)
    ordered: list[dict[str, Any]] = []
    doc_order = sorted(by_doc)
    while any(by_doc.values()):
        for source_doc in doc_order:
            if by_doc[source_doc]:
                ordered.append(by_doc[source_doc].popleft())
    return ordered


def category_shortfalls(gate_report: Path | None) -> dict[str, int]:
    if gate_report is None:
        return {name: 0 for name in NONPIN_CATEGORIES}
    payload = json.loads(gate_report.read_text(encoding="utf-8"))
    values = (
        payload.get("release_constraints", {})
        .get("microtext_category_balance", {})
        .get("category_shortfalls", {})
    )
    return {name: max(0, int(values.get(name) or 0)) for name in NONPIN_CATEGORIES}


def build_tranche(
    root: Path,
    input_path: Path,
    *,
    row_target: int,
    max_rows_per_doc: int,
    max_rows_per_page: int,
    max_same_text_per_doc: int,
    max_same_text_global: int,
    gate_report: Path | None = None,
    date_label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if min(
        row_target,
        max_rows_per_doc,
        max_rows_per_page,
        max_same_text_per_doc,
        max_same_text_global,
    ) <= 0:
        raise ValueError("all row and diversity limits must be positive")

    root = root.resolve()
    input_path = resolve(root, input_path).resolve()
    gate_report = resolve(root, gate_report).resolve() if gate_report else None
    locks = source_family_split_locks(root)
    shortfalls = category_shortfalls(gate_report)
    inputs = read_jsonl(input_path)
    eligible: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for row in inputs:
        reasons: list[str] = []
        row_id = candidate_id(row)
        source_doc = doc_id(row)
        row_category = category(row)
        source_lock = locks.get(source_doc, "unseen")
        image_value = str(row.get("image_path") or row.get("page_image_path") or "").strip()
        if not row_id:
            reasons.append("candidate_id_missing")
        elif row_id in seen_ids:
            reasons.append("duplicate_candidate_id")
        if row.get("pair_id") or str(row.get("task") or "microtext").strip().lower() == "visualdiff":
            reasons.append("not_microtext")
        if not source_doc:
            reasons.append("doc_id_missing")
        if row_category not in NONPIN_CATEGORIES:
            reasons.append("not_supported_nonpin_category")
        if str(row.get("unstaged_capacity_tier") or "").strip() != "machine_prequalified_needs_visual_qa":
            reasons.append("not_machine_prequalified")
        if str(row.get("machine_qa_status") or "").strip() != "selected_for_human_review":
            reasons.append("not_selected_for_human_review")
        if source_lock not in {"test", "unseen"}:
            reasons.append(f"source_locked_{source_lock}")
        if not text_value(row):
            reasons.append("proposed_text_missing")
        if page_index(row) is None:
            reasons.append("page_index_missing")
        if bbox(row) is None:
            reasons.append("bbox_invalid")
        if not image_value:
            reasons.append("image_path_missing")
        elif not resolve(root, image_value).is_file():
            reasons.append("image_missing")
        if row.get("safe_to_merge_gold") is True:
            reasons.append("unexpected_mergeable_input")
        lineage_docs = row.get("unstaged_capacity_source_docs")
        if not isinstance(lineage_docs, list) or source_doc not in {str(value) for value in lineage_docs}:
            reasons.append("unstaged_source_lineage_missing")

        if reasons:
            held = dict(row)
            held["test_scale_disposition"] = "held"
            held["test_scale_hold_reasons"] = sorted(set(reasons))
            held["safe_to_merge_gold"] = False
            holds.append(held)
            exclusion_counts.update(set(reasons))
            continue
        seen_ids.add(row_id)
        prepared = dict(row)
        prepared["test_scale_source_lock_before"] = source_lock
        eligible.append(prepared)

    ordered_by_category = {
        name: diverse_order([row for row in eligible if category(row) == name])
        for name in NONPIN_CATEGORIES
    }
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    doc_counts: Counter[str] = Counter()
    page_counts: Counter[tuple[str, int]] = Counter()
    text_doc_counts: Counter[tuple[str, str]] = Counter()
    text_counts: Counter[str] = Counter()
    cap_reasons: Counter[str] = Counter()

    def take(row: dict[str, Any]) -> bool:
        source_doc = doc_id(row)
        page = page_index(row)
        text = normalized_text(row)
        reasons: list[str] = []
        if doc_counts[source_doc] >= max_rows_per_doc:
            reasons.append("per_document_cap")
        if page is not None and page_counts[(source_doc, page)] >= max_rows_per_page:
            reasons.append("per_page_cap")
        if text_doc_counts[(source_doc, text)] >= max_same_text_per_doc:
            reasons.append("same_text_per_document_cap")
        if text_counts[text] >= max_same_text_global:
            reasons.append("same_text_global_cap")
        if reasons:
            cap_reasons.update(reasons)
            return False
        selected.append(row)
        selected_ids.add(candidate_id(row))
        doc_counts[source_doc] += 1
        if page is not None:
            page_counts[(source_doc, page)] += 1
        text_doc_counts[(source_doc, text)] += 1
        text_counts[text] += 1
        return True

    # First close active category deficits where the candidate reservoir allows it.
    remaining: dict[str, deque[dict[str, Any]]] = {}
    for name in sorted(NONPIN_CATEGORIES, key=lambda value: (-shortfalls[value], value)):
        queue = deque(ordered_by_category[name])
        accepted = 0
        while queue and accepted < shortfalls[name] and len(selected) < row_target:
            accepted += int(take(queue.popleft()))
        remaining[name] = queue

    # Fill the scale target in category round-robin order to avoid one-note cohorts.
    category_order = sorted(
        NONPIN_CATEGORIES,
        key=lambda value: (-shortfalls[value], value),
    )
    made_progress = True
    while len(selected) < row_target and made_progress:
        made_progress = False
        for name in category_order:
            queue = remaining[name]
            while queue:
                row = queue.popleft()
                if take(row):
                    made_progress = True
                    break
            if len(selected) >= row_target:
                break

    for row in eligible:
        if candidate_id(row) in selected_ids:
            continue
        held = dict(row)
        held["test_scale_disposition"] = "held"
        held["test_scale_hold_reasons"] = ["not_selected_by_target_or_diversity_policy"]
        held["safe_to_merge_gold"] = False
        holds.append(held)

    output_rows: list[dict[str, Any]] = []
    for row in sorted(selected, key=lambda value: (category(value), *row_key(value))):
        prepared = dict(row)
        prepared.update(
            {
                "reserved_split": "test",
                "split": "test",
                "test_scale_tranche_only": True,
                "test_scale_tranche_date_label": date_label,
                "promotion_state": "unreviewed_candidate",
                "review_status": "needs_review",
                "safe_to_merge_gold": False,
            }
        )
        output_rows.append(prepared)

    report = {
        "schema": "eng_bench_test_scale_microtext_tranche_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "status": "PASS",
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "input_path": input_path.relative_to(root).as_posix(),
        "gate_report": gate_report.relative_to(root).as_posix() if gate_report else "",
        "policy": {
            "task": "microtext",
            "target_split": "test",
            "allowed_source_locks": ["test", "unseen"],
            "source_lock_scope": "manifest identity-connected source family",
            "allowed_categories": sorted(NONPIN_CATEGORIES),
            "required_unstaged_tier": "machine_prequalified_needs_visual_qa",
            "required_machine_qa_status": "selected_for_human_review",
            "row_target": row_target,
            "max_rows_per_document": max_rows_per_doc,
            "max_rows_per_page": max_rows_per_page,
            "max_same_text_per_document": max_same_text_per_doc,
            "max_same_text_global": max_same_text_global,
        },
        "counts": {
            "input_rows": len(inputs),
            "eligible_rows": len(eligible),
            "selected_rows": len(output_rows),
            "held_rows": len(holds),
            "selected_documents": len(doc_counts),
            "selected_unseen_source_rows": sum(
                row.get("test_scale_source_lock_before") == "unseen" for row in output_rows
            ),
            "selected_existing_test_source_rows": sum(
                row.get("test_scale_source_lock_before") == "test" for row in output_rows
            ),
            "selected_categories": dict(sorted(Counter(category(row) for row in output_rows).items())),
            "exclusion_reasons": dict(sorted(exclusion_counts.items())),
            "selection_cap_rejections": dict(sorted(cap_reasons.items())),
        },
        "active_category_shortfalls_used_for_priority": dict(sorted(shortfalls.items())),
        "interpretation": (
            "Rows are review-only test-scale capacity. Test/unseen source eligibility, "
            "machine prequalification, provenance lineage, evidence existence, and diversity "
            "caps are deterministic screens; visual acceptance and all promotion gates remain required."
        ),
    }
    return output_rows, holds, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts = report["counts"]
    lines = [
        "# Test-Scale MicroText Tranche",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Status: `{report['status']}`",
        f"- Input rows: `{counts['input_rows']}`",
        f"- Eligible rows: `{counts['eligible_rows']}`",
        f"- Selected review-only test rows: `{counts['selected_rows']}`",
        f"- Selected source documents: `{counts['selected_documents']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Selected Categories",
        "",
    ]
    for name, count in counts["selected_categories"].items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    path.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--row-target", type=int, default=900)
    parser.add_argument("--max-rows-per-doc", type=int, default=100)
    parser.add_argument("--max-rows-per-page", type=int, default=15)
    parser.add_argument("--max-same-text-per-doc", type=int, default=3)
    parser.add_argument("--max-same-text-global", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--holds-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    selected, holds, report = build_tranche(
        root,
        args.input,
        row_target=args.row_target,
        max_rows_per_doc=args.max_rows_per_doc,
        max_rows_per_page=args.max_rows_per_page,
        max_same_text_per_doc=args.max_same_text_per_doc,
        max_same_text_global=args.max_same_text_global,
        gate_report=args.gate_report,
        date_label=args.date_label,
    )
    write_jsonl(resolve(root, args.output), selected)
    write_jsonl(resolve(root, args.holds_output), holds)
    write_report(resolve(root, args.report_json), report)
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
