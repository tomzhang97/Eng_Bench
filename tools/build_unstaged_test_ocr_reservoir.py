#!/usr/bin/env python3
"""Build a split-aware review-only reservoir from unstaged OCR rows."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_staged_v2_capacity import (
    bboxes_are_near_duplicates,
    capacity_exclusion_reason,
    capacity_identity,
    load_split_reservations,
    microtext_region_geometry,
    read_rows,
    task_for_row,
)


def resolve_globs(root: Path, patterns: Iterable[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path.resolve() for path in root.glob(pattern) if path.is_file())
    return sorted(paths)


def add_reference_row(
    row: dict[str, Any],
    identities: set[str],
    geometries: dict[tuple[str, int], list[tuple[int, int, int, int]]],
) -> None:
    identity = capacity_identity(row)
    if identity:
        identities.add(identity)
    geometry = microtext_region_geometry(row)
    if geometry:
        doc_id, page_index, bbox = geometry
        geometries[(doc_id, page_index)].append(bbox)


def has_near_overlap(
    row: dict[str, Any],
    geometries: dict[tuple[str, int], list[tuple[int, int, int, int]]],
) -> bool:
    geometry = microtext_region_geometry(row)
    if not geometry:
        return False
    doc_id, page_index, bbox = geometry
    return any(
        bboxes_are_near_duplicates(bbox, other)
        for other in geometries.get((doc_id, page_index), [])
    )


def confidence(row: dict[str, Any]) -> float:
    try:
        return float(row.get("ocr_confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def candidate_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    geometry = microtext_region_geometry(row)
    doc_id, page_index, bbox = geometry or ("", -1, (0, 0, 0, 0))
    return (
        -confidence(row),
        doc_id,
        page_index,
        bbox,
        str(row.get("candidate_id") or ""),
    )


def build_reservoir(
    root: Path,
    *,
    capacity_report_path: Path,
    split_plan_path: Path,
    input_paths: list[Path],
    hold_paths: list[Path],
    included_splits: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    included_splits = set(included_splits or {"test"})
    invalid_splits = included_splits - {"train", "dev", "test"}
    if invalid_splits:
        raise ValueError(f"Unsupported splits: {sorted(invalid_splits)}")
    capacity_report = json.loads(capacity_report_path.read_text(encoding="utf-8"))
    split_reservations, split_issues = load_split_reservations(split_plan_path)
    selected_doc_splits = {
        unit_id: split
        for (task, unit_id), split in split_reservations.items()
        if task == "microtext" and split in included_splits
    }

    staged_identities: set[str] = set()
    staged_geometries: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    active_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    for row in read_rows(active_path):
        add_reference_row(row, staged_identities, staged_geometries)
    for cohort in capacity_report.get("cohorts", []):
        path = Path(str(cohort.get("path") or ""))
        absolute = path if path.is_absolute() else root / path
        for row in read_rows(absolute):
            if not capacity_exclusion_reason(row):
                add_reference_row(row, staged_identities, staged_geometries)

    held_identities: set[str] = set()
    held_geometries: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    for path in hold_paths:
        for row in read_rows(path):
            add_reference_row(row, held_identities, held_geometries)

    outcomes: Counter[str] = Counter()
    best_by_identity: dict[str, dict[str, Any]] = {}
    origins_by_identity: dict[str, set[str]] = defaultdict(set)
    input_rows = 0
    for path in input_paths:
        origin = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        for source_row in read_rows(path):
            input_rows += 1
            row = deepcopy(source_row)
            if task_for_row(row) != "microtext":
                outcomes["non_microtext"] += 1
                continue
            doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
            if doc_id not in selected_doc_splits:
                outcomes["non_selected_split_source"] += 1
                continue
            terminal_reason = capacity_exclusion_reason(row)
            if terminal_reason:
                outcomes[f"terminal_input:{terminal_reason}"] += 1
                continue
            identity = capacity_identity(row)
            if not identity:
                outcomes["missing_identity"] += 1
                continue
            if not microtext_region_geometry(row):
                outcomes["missing_or_invalid_region"] += 1
                continue
            image_path = str(row.get("image_path") or "").strip()
            if not image_path or not (root / image_path).is_file():
                outcomes["missing_source_image"] += 1
                continue
            if identity in staged_identities:
                outcomes["active_or_staged_exact"] += 1
                continue
            if has_near_overlap(row, staged_geometries):
                outcomes["active_or_staged_near"] += 1
                continue
            if identity in held_identities:
                outcomes["prior_hold_exact"] += 1
                continue
            if has_near_overlap(row, held_geometries):
                outcomes["prior_hold_near"] += 1
                continue
            origins_by_identity[identity].add(origin)
            previous = best_by_identity.get(identity)
            if previous is None or candidate_rank(row) < candidate_rank(previous):
                best_by_identity[identity] = row

    selected: list[dict[str, Any]] = []
    selected_geometries: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    for identity, row in sorted(best_by_identity.items(), key=lambda item: candidate_rank(item[1])):
        if has_near_overlap(row, selected_geometries):
            outcomes["input_near_duplicate"] += 1
            continue
        geometry = microtext_region_geometry(row)
        assert geometry is not None
        doc_id, page_index, bbox = geometry
        selected_geometries[(doc_id, page_index)].append(bbox)
        reserved_split = selected_doc_splits[doc_id]
        row["reserved_split"] = reserved_split
        row["review_status"] = "needs_review"
        row["promotion_state"] = "unreviewed_candidate"
        row["machine_qa_status"] = "unstaged_ocr_reservoir"
        row["safe_to_merge_gold"] = False
        row["reservoir_origin_files"] = sorted(origins_by_identity[identity])
        row["machine_qa_notes"] = (
            f"Fresh {reserved_split}-locked OCR region; excludes active/staged rows and exact/near "
            "prior holds. "
            "Requires visual and independent human review before promotion."
        )
        selected.append(row)
        outcomes["selected"] += 1

    selected.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row.get("bbox") or ()),
            str(row.get("candidate_id") or ""),
        )
    )
    report = {
        "capacity_input_clean": bool(capacity_report.get("capacity_input_clean")),
        "included_splits": sorted(included_splits),
        "split_plan_valid": not split_issues,
        "split_plan_issues": split_issues,
        "input_files": [
            path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            for path in input_paths
        ],
        "hold_files": len(hold_paths),
        "input_rows": input_rows,
        "active_and_staged_identities": len(staged_identities),
        "prior_hold_identities": len(held_identities),
        "selected_rows": len(selected),
        "selected_documents": len({str(row.get("doc_id") or "") for row in selected}),
        "selected_unique_texts": len(
            {
                str(row.get("proposed_text") or row.get("target_text") or "").strip()
                for row in selected
            }
        ),
        "selected_by_document": dict(
            sorted(Counter(str(row.get("doc_id") or "") for row in selected).items())
        ),
        "selected_by_category": dict(
            sorted(Counter(str(row.get("category") or "") for row in selected).items())
        ),
        "selected_by_split": dict(
            sorted(Counter(str(row.get("reserved_split") or "") for row in selected).items())
        ),
        "outcomes": dict(sorted(outcomes.items())),
        "safe_to_merge_gold": False,
    }
    report["valid"] = bool(
        report["capacity_input_clean"] and report["split_plan_valid"] and selected
    )
    return selected, report


def write_outputs(
    rows: list[dict[str, Any]],
    report: dict[str, Any],
    output_jsonl: Path,
    report_json: Path,
    report_md: Path,
) -> None:
    for path in (output_jsonl, report_json, report_md):
        path.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Unstaged OCR Reservoir",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input rows: {report['input_rows']}",
        f"- Selected rows: {report['selected_rows']}",
        f"- Documents: {report['selected_documents']}",
        f"- Unique proposed texts: {report['selected_unique_texts']}",
        f"- Included splits: `{', '.join(report.get('included_splits', []))}`",
        "- Active gold changed: no",
        "- Safe to merge gold: no",
        "",
        "## Categories",
        "",
    ]
    lines.extend(f"- `{name}`: {count}" for name, count in report["selected_by_category"].items())
    lines.extend(["", "## Reserved Splits", ""])
    lines.extend(f"- `{name}`: {count}" for name, count in report["selected_by_split"].items())
    lines.extend(["", "## Documents", ""])
    lines.extend(f"- `{name}`: {count}" for name, count in report["selected_by_document"].items())
    lines.extend(["", "## Outcomes", ""])
    lines.extend(f"- `{name}`: {count}" for name, count in report["outcomes"].items())
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--capacity-report", type=Path, required=True)
    parser.add_argument("--split-plan", type=Path, required=True)
    parser.add_argument("--input-glob", action="append", required=True)
    parser.add_argument("--hold-glob", action="append", default=[])
    parser.add_argument(
        "--split",
        action="append",
        choices=("train", "dev", "test"),
        default=[],
        help="Include this reserved split; repeat as needed. Defaults to test.",
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    capacity_report = args.capacity_report if args.capacity_report.is_absolute() else root / args.capacity_report
    split_plan = args.split_plan if args.split_plan.is_absolute() else root / args.split_plan
    input_paths = resolve_globs(root, args.input_glob)
    hold_paths = resolve_globs(root, args.hold_glob)
    if not input_paths:
        parser.error("--input-glob did not resolve any files")
    rows, report = build_reservoir(
        root,
        capacity_report_path=capacity_report,
        split_plan_path=split_plan,
        input_paths=input_paths,
        hold_paths=hold_paths,
        included_splits=set(args.split or ["test"]),
    )
    output_jsonl = args.output_jsonl if args.output_jsonl.is_absolute() else root / args.output_jsonl
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    write_outputs(rows, report, output_jsonl, report_json, report_md)
    print(json.dumps({key: report[key] for key in (
        "valid", "input_rows", "selected_rows", "selected_documents", "selected_unique_texts"
    )}, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
