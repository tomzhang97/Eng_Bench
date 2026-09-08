#!/usr/bin/env python3
"""Strictly shortlist NASA cartridge-table tolerance values for visual QA."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


EXPECTED_DOC_ID = "nasa_19920009198_cartridge_dimension_tolerances"
TOLERANCE_RE = re.compile(r"^\u00b1(?:\d+(?:\.\d+)?|\.\d+)$")
TOLERANCE_COLUMN_WINDOWS = ((0.43, 0.56), (0.72, 0.87))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    if header[12:16] != b"IHDR":
        raise ValueError(f"PNG is missing IHDR: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid PNG dimensions: {path}")
    return width, height


def parse_bbox(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = row.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def overlap_over_smaller(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    if intersection <= 0:
        return 0.0
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / min(left_area, right_area)


def within_tolerance_column(x_center_normalized: float) -> bool:
    return any(
        low <= x_center_normalized <= high
        for low, high in TOLERANCE_COLUMN_WINDOWS
    )


def shortlist(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    min_confidence: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    image_sizes: dict[Path, tuple[int, int]] = {}
    seen_ids: set[str] = set()

    def hold(source_row: dict[str, Any], reason: str) -> None:
        output = dict(source_row)
        output["machine_qa_status"] = "machine_held_cartridge_tolerance_filter"
        output["machine_hold_reason"] = reason
        output["safe_to_merge_gold"] = False
        held.append(output)
        hold_reasons[reason] += 1

    provisional: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        if str(row.get("doc_id") or "") != EXPECTED_DOC_ID:
            hold(row, "unexpected_doc_id")
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen_ids:
            hold(row, "missing_or_duplicate_candidate_id")
            continue
        seen_ids.add(candidate_id)
        if str(row.get("category") or "") != "tolerance_value":
            hold(row, "non_tolerance_category")
            continue
        try:
            confidence = float(row.get("ocr_confidence"))
        except (TypeError, ValueError):
            hold(row, "missing_or_invalid_confidence")
            continue
        if confidence < min_confidence:
            hold(row, "below_confidence_floor")
            continue
        text = str(row.get("proposed_text") or row.get("target_text") or "").strip()
        if not TOLERANCE_RE.fullmatch(text):
            hold(row, "invalid_tolerance_token")
            continue
        region = parse_bbox(row)
        if region is None:
            hold(row, "invalid_bbox")
            continue
        image_value = str(row.get("image_path") or "").strip()
        image_path = (root / image_value).resolve()
        try:
            image_path.relative_to(root)
        except ValueError:
            hold(row, "image_path_outside_root")
            continue
        if not image_path.is_file():
            hold(row, "missing_page_image")
            continue
        try:
            width, height = image_sizes.setdefault(image_path, png_size(image_path))
        except (OSError, ValueError):
            hold(row, "invalid_page_image")
            continue
        if region[2] > width or region[3] > height:
            hold(row, "bbox_outside_page")
            continue
        x_center_normalized = ((region[0] + region[2]) / 2.0) / width
        if not within_tolerance_column(x_center_normalized):
            hold(row, "outside_tolerance_columns")
            continue
        output = dict(row)
        output["raw_text"] = text
        output["target_text"] = text
        output["proposed_text"] = text
        output["text_context"] = text
        output["question_text"] = "What tolerance value is shown in this region?"
        output["review_status"] = "needs_review"
        output["promotion_state"] = "unreviewed_candidate"
        output["machine_qa_status"] = "strict_geometry_confidence_pass_pending_visual_qa"
        output["machine_filter_confidence_floor"] = min_confidence
        output["machine_filter_x_center_normalized"] = round(x_center_normalized, 6)
        output["machine_qa_notes"] = (
            "Strict NASA cartridge-table prefilter passed category, OCR confidence, "
            "tolerance-token, page-bounds, and tolerance-column geometry checks; "
            "full crop visual QA is still required."
        )
        output["safe_to_merge_gold"] = False
        provisional.append(output)

    provisional.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
            str(row.get("candidate_id") or ""),
        )
    )
    selected_regions: list[tuple[int, tuple[float, float, float, float]]] = []
    for row in provisional:
        page_index = int(row.get("page_index", -1))
        region = parse_bbox(row)
        assert region is not None
        if any(
            prior_page == page_index and overlap_over_smaller(prior_region, region) >= 0.8
            for prior_page, prior_region in selected_regions
        ):
            hold(row, "duplicate_overlapping_region")
            continue
        selected_regions.append((page_index, region))
        selected.append(row)

    selected_by_page = Counter(str(row.get("page_index")) for row in selected)
    report = {
        "goal": "Gold v2.0 Global",
        "source_doc_id": EXPECTED_DOC_ID,
        "source_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_by_page": dict(sorted(selected_by_page.items(), key=lambda item: int(item[0]))),
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "min_confidence": min_confidence,
        "tolerance_column_windows_normalized": [list(window) for window in TOLERANCE_COLUMN_WINDOWS],
        "safe_to_merge_gold": False,
    }
    return selected, held, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--min-confidence", type=float, default=0.98)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()
    rows = read_jsonl(input_path)
    selected, held, report = shortlist(
        rows,
        root=root,
        min_confidence=args.min_confidence,
    )
    write_jsonl(selected_path, selected)
    write_jsonl(held_path, held)
    report.update(
        {
            "input": input_path.as_posix(),
            "input_sha256": sha256(input_path),
            "selected_output": selected_path.as_posix(),
            "selected_sha256": sha256(selected_path),
            "held_output": held_path.as_posix(),
            "held_sha256": sha256(held_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
