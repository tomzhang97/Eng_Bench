#!/usr/bin/env python3
"""Build one review-only VisualDiff queue from pinned Git manifest additions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from build_visualdiff_source_review import build_review_rows, write_jsonl


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(row)
    return rows


def build_queue(
    root: Path,
    additions_path: Path,
    *,
    min_area: int,
    max_area_ratio: float,
    border_margin: int,
    titleblock_corner_start_ratio: float,
    min_inlier_ratio: float,
    merge_gap_px: int,
    max_per_page: int,
    max_per_pair: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    rows = read_jsonl(additions_path)
    docs = {
        str(row.get("doc_id")): row
        for row in rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pairs = [row for row in rows if row.get("type") == "pair"]
    review_rows: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []

    for pair in pairs:
        pair_id = str(pair.get("pair_id") or "")
        old_doc_id = str(pair.get("from_doc_id") or "")
        new_doc_id = str(pair.get("to_doc_id") or "")
        old_doc = docs.get(old_doc_id)
        new_doc = docs.get(new_doc_id)
        if old_doc is None or new_doc is None:
            issues.append({"pair_id": pair_id, "type": "missing_manifest_doc"})
            continue
        candidate_id = str(pair.get("source_candidate_id") or "")
        if not candidate_id or not (
            candidate_id
            == old_doc.get("source_candidate_id")
            == new_doc.get("source_candidate_id")
        ):
            issues.append({"pair_id": pair_id, "type": "source_candidate_id_mismatch"})
            continue
        built, report = build_review_rows(
            root=root,
            pair_family=pair_id,
            source_candidate_id=candidate_id,
            old_doc_id=old_doc_id,
            new_doc_id=new_doc_id,
            min_area=min_area,
            max_area_ratio=max_area_ratio,
            border_margin=border_margin,
            titleblock_corner_start_ratio=titleblock_corner_start_ratio,
            min_inlier_ratio=min_inlier_ratio,
            merge_gap_px=merge_gap_px,
            max_per_page=max_per_page,
            max_total=max_per_pair,
        )
        pair_reports.append(report)
        if not built:
            issues.append({"pair_id": pair_id, "type": "zero_review_rows"})
        review_rows.extend(built)

    pair_ids = [str(row.get("pair_id") or "") for row in review_rows]
    if not all(pair_ids) or len(pair_ids) != len(set(pair_ids)):
        issues.append({"pair_id": "", "type": "missing_or_duplicate_review_row_id"})
    represented_families = sorted({str(row.get("project_id") or "") for row in review_rows})
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "versioned_git_visualdiff_review_staging",
        "manifest_additions": additions_path.relative_to(root).as_posix(),
        "totals": {
            "manifest_pairs": len(pairs),
            "represented_families": len(represented_families),
            "review_rows": len(review_rows),
            "issues": len(issues),
        },
        "settings": {
            "min_area": min_area,
            "max_area_ratio": max_area_ratio,
            "border_margin": border_margin,
            "titleblock_corner_start_ratio": titleblock_corner_start_ratio,
            "min_inlier_ratio": min_inlier_ratio,
            "merge_gap_px": merge_gap_px,
            "max_per_page": max_per_page,
            "max_per_pair": max_per_pair,
        },
        "represented_families": represented_families,
        "issues": issues,
        "pairs": pair_reports,
        "active_gold_rows_modified": 0,
        "valid": not issues and len(represented_families) == len(pairs),
    }
    return review_rows, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest-additions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--min-area", type=int, default=300)
    parser.add_argument("--max-area-ratio", type=float, default=0.12)
    parser.add_argument("--border-margin", type=int, default=12)
    parser.add_argument("--titleblock-corner-start-ratio", type=float, default=0.82)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.25)
    parser.add_argument("--merge-gap-px", type=int, default=18)
    parser.add_argument("--max-per-page", type=int, default=40)
    parser.add_argument("--max-per-pair", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    additions = args.manifest_additions if args.manifest_additions.is_absolute() else root / args.manifest_additions
    rows, report = build_queue(
        root,
        additions,
        min_area=args.min_area,
        max_area_ratio=args.max_area_ratio,
        border_margin=args.border_margin,
        titleblock_corner_start_ratio=args.titleblock_corner_start_ratio,
        min_inlier_ratio=args.min_inlier_ratio,
        merge_gap_px=args.merge_gap_px,
        max_per_page=args.max_per_page,
        max_per_pair=args.max_per_pair,
    )
    output = args.output if args.output.is_absolute() else root / args.output
    report_path = args.report_json if args.report_json.is_absolute() else root / args.report_json
    write_jsonl(output, rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"], sort_keys=True))
    if not report["valid"]:
        print(json.dumps(report["issues"], sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
