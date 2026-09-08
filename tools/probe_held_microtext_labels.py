#!/usr/bin/env python3
"""Surface high-confidence held MicroText regions for conservative reinspection.

The probe is deliberately non-promotional. It removes exact/physical duplicates
of already selected rows and obvious document-administration text, then emits a
small review-only queue. Every output remains ``safe_to_merge_gold=false``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.filter_nasa_epsdu_procurement_candidates import (  # noqa: E402
    parse_bbox,
    read_jsonl,
    sha256,
    write_jsonl,
)


ADMIN_TEXT = re.compile(
    r"(?:ORIGINAL\s+PAGE|POOR\s+QUALITY|NATIONAL\s+AERONAUTICS|"
    r"SPACE\s+ADMINISTRATION|FIGURE\s*\d*|DRAWING\s+NO|SHEET\s+NO|"
    r"REVISION|APPROVED|CHECKED|DRAWN\s+BY|DATE\b|SCALE\b)",
    re.IGNORECASE,
)


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def alnum_count(value: str) -> int:
    return sum(character.isalnum() for character in value)


def intersection(a: Sequence[int], b: Sequence[int]) -> int:
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def area(box: Sequence[int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def physically_overlaps(a: Sequence[int], b: Sequence[int]) -> bool:
    overlap = intersection(a, b)
    if not overlap:
        return False
    union = area(a) + area(b) - overlap
    iou = overlap / union if union else 0.0
    smaller = min(area(a), area(b))
    containment = overlap / smaller if smaller else 0.0
    return iou >= 0.30 or containment >= 0.70


def _index_selected(rows: Iterable[dict[str, Any]]) -> tuple[set[str], dict[tuple[str, int], list[list[int]]]]:
    texts: set[str] = set()
    regions: dict[tuple[str, int], list[list[int]]] = defaultdict(list)
    for row in rows:
        text = normalized_text(row.get("target_text") or row.get("proposed_text"))
        if text:
            texts.add(text)
        bbox = parse_bbox(row)
        if bbox is not None:
            key = (str(row.get("doc_id") or ""), int(row.get("page_index", -1)))
            regions[key].append(bbox)
    return texts, regions


def probe(
    held_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    *,
    confidence_floor: float = 0.98,
    max_text_length: int = 60,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_texts, selected_regions = _index_selected(selected_rows)
    reasons: Counter[str] = Counter()
    output: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for source_row in held_rows:
        row = dict(source_row)
        candidate_id = str(row.get("candidate_id") or "").strip()
        text = str(row.get("proposed_text") or "").strip()
        norm = normalized_text(text)
        bbox = parse_bbox(row)
        reason = ""
        if not candidate_id or candidate_id in seen_ids:
            reason = "missing_or_duplicate_candidate_id"
        else:
            seen_ids.add(candidate_id)
        try:
            confidence = float(row.get("ocr_confidence"))
        except (TypeError, ValueError):
            confidence = -1.0
        if not reason and confidence < confidence_floor:
            reason = "below_confidence_floor"
        elif not reason and (not norm or alnum_count(norm) < 2):
            reason = "too_little_text"
        elif not reason and len(text) > max_text_length:
            reason = "text_too_long"
        elif not reason and ADMIN_TEXT.search(text):
            reason = "obvious_document_administration_text"
        elif not reason and norm in selected_texts:
            reason = "text_already_selected"
        elif not reason and bbox is None:
            reason = "invalid_bbox"
        elif not reason:
            key = (str(row.get("doc_id") or ""), int(row.get("page_index", -1)))
            if any(physically_overlaps(bbox, selected) for selected in selected_regions.get(key, [])):
                reason = "physical_region_already_selected"
        if reason:
            reasons[reason] += 1
            continue

        row.update(
            {
                "review_status": "machine_probe_pending_visual_reinspection",
                "machine_qa_status": "held_microtext_supplemental_probe_only",
                "machine_qa_notes": (
                    "High-confidence, non-duplicate held region surfaced for visual reinspection only. "
                    "This probe does not validate text, category, engineering meaning, or promotion eligibility."
                ),
                "safe_to_merge_gold": False,
            }
        )
        output.append(row)

    output.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            int(parse_bbox(row)[1]) if parse_bbox(row) else -1,
            int(parse_bbox(row)[0]) if parse_bbox(row) else -1,
            str(row.get("candidate_id") or ""),
        )
    )
    report = {
        "goal": "Gold v2.0 Global",
        "held_rows": len(held_rows),
        "selected_reference_rows": len(selected_rows),
        "probe_rows": len(output),
        "probe_unique_ids": len({str(row.get("candidate_id") or "") for row in output}),
        "probe_pages": len({int(row.get("page_index", -1)) for row in output}),
        "excluded_by_reason": dict(sorted(reasons.items())),
        "confidence_floor": confidence_floor,
        "max_text_length": max_text_length,
        "safe_to_merge_gold": False,
    }
    return output, report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--held", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True, action="append")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--confidence-floor", type=float, default=0.98)
    parser.add_argument("--max-text-length", type=int, default=60)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    held_path = (root / args.held).resolve()
    selected_paths = [(root / path).resolve() for path in args.selected]
    output_path = (root / args.output).resolve()
    report_path = (root / args.report_json).resolve()
    rows, report = probe(
        read_jsonl(held_path),
        [row for path in selected_paths for row in read_jsonl(path)],
        confidence_floor=args.confidence_floor,
        max_text_length=args.max_text_length,
    )
    write_jsonl(output_path, rows)
    report.update(
        {
            "held": held_path.as_posix(),
            "held_sha256": sha256(held_path),
            "selected": [path.as_posix() for path in selected_paths],
            "selected_sha256": [sha256(path) for path in selected_paths],
            "output": output_path.as_posix(),
            "output_sha256": sha256(output_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
