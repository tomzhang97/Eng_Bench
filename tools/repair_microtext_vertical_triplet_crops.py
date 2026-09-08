#!/usr/bin/env python3
"""Create auditable center-crop replacements for vertically merged OCR regions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def parse_row_numbers(values: Iterable[str]) -> list[int]:
    rows: set[int] = set()
    for value in values:
        for token in value.split(","):
            token = token.strip()
            if not token:
                continue
            number = int(token)
            if number < 1:
                raise ValueError(f"row numbers are one-based, got {number}")
            rows.add(number)
    return sorted(rows)


def centered_vertical_bbox(
    bbox: list[int],
    crop_height: int,
    image_width: int,
    image_height: int,
) -> list[int]:
    if len(bbox) != 4 or any(not isinstance(value, int) for value in bbox):
        raise ValueError(f"invalid integer bbox: {bbox!r}")
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= image_width and 0 <= y0 < y1 <= image_height):
        raise ValueError(
            f"bbox {bbox!r} is outside image bounds {image_width}x{image_height}"
        )
    if crop_height <= 0:
        raise ValueError("crop height must be positive")
    if crop_height >= y1 - y0:
        raise ValueError(
            f"crop height {crop_height} must be smaller than source height {y1 - y0}"
        )
    center = (y0 + y1) / 2.0
    new_y0 = int(round(center - crop_height / 2.0))
    new_y0 = max(0, min(new_y0, image_height - crop_height))
    new_y1 = new_y0 + crop_height
    return [x0, new_y0, x1, new_y1]


def repaired_candidate_id(row: dict[str, Any], bbox: list[int]) -> str:
    identity = {
        "doc_id": row.get("doc_id"),
        "page_index": row.get("page_index"),
        "bbox": bbox,
        "proposed_text": row.get("proposed_text"),
        "category": row.get("category"),
    }
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"repaircand__{hashlib.sha1(payload).hexdigest()[:20]}"


def repair_row(
    row: dict[str, Any],
    *,
    root: Path,
    crop_height: int,
    source_row_number: int,
) -> dict[str, Any]:
    image_value = str(row.get("image_path") or "")
    image_path = Path(image_value)
    if not image_path.is_absolute():
        image_path = root / image_path
    if not image_path.is_file():
        raise FileNotFoundError(f"row {source_row_number}: missing image {image_path}")
    with Image.open(image_path) as image:
        width, height = image.size
    original_bbox = row.get("bbox")
    if not isinstance(original_bbox, list):
        raise ValueError(f"row {source_row_number}: missing bbox")
    bbox = centered_vertical_bbox(original_bbox, crop_height, width, height)
    source_candidate_id = str(row.get("candidate_id") or "")
    if not source_candidate_id:
        raise ValueError(f"row {source_row_number}: missing candidate_id")

    repaired = dict(row)
    repaired.update(
        {
            "candidate_id": repaired_candidate_id(row, bbox),
            "bbox": bbox,
            "source_candidate_id": source_candidate_id,
            "machine_candidate_id_repaired_from": source_candidate_id,
            "machine_bbox_repaired_from": original_bbox,
            "machine_crop_repair": "centered_vertical_triplet_to_single_row",
            "machine_crop_repair_height_px": crop_height,
            "machine_crop_repair_source_row": source_row_number,
            "machine_qa_status": "crop_repaired_needs_visual_review",
            "machine_qa_notes": (
                "Source OCR bbox spanned adjacent dimension-table rows. "
                "Replacement is centered on the OCR target and requires visual and "
                "independent human review before promotion."
            ),
            "promotion_state": "unreviewed_candidate",
            "review_status": "needs_review",
            "safe_to_merge_gold": False,
        }
    )
    return repaired


def build_repairs(
    input_path: Path,
    row_numbers: list[int],
    *,
    root: Path,
    crop_height: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = read_jsonl(input_path)
    if not row_numbers:
        raise ValueError("at least one --rows value is required")
    if row_numbers[-1] > len(rows):
        raise IndexError(
            f"requested row {row_numbers[-1]} but input contains {len(rows)} rows"
        )
    repaired = [
        repair_row(
            rows[number - 1],
            root=root,
            crop_height=crop_height,
            source_row_number=number,
        )
        for number in row_numbers
    ]
    ids = [str(row["candidate_id"]) for row in repaired]
    regions = [
        (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row["bbox"]),
        )
        for row in repaired
    ]
    if len(ids) != len(set(ids)):
        raise ValueError("repaired candidate IDs are not unique")
    if len(regions) != len(set(regions)):
        raise ValueError("repaired physical regions are not unique")
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "future_only_crop_repair",
        "input": input_path.resolve().as_posix(),
        "input_rows": len(rows),
        "selected_source_rows": row_numbers,
        "crop_height_px": crop_height,
        "repaired_rows": len(repaired),
        "unique_candidate_ids": len(set(ids)),
        "unique_physical_regions": len(set(regions)),
        "reserved_splits": sorted(
            {str(row.get("reserved_split") or "") for row in repaired}
        ),
        "review_statuses": sorted(
            {str(row.get("review_status") or "") for row in repaired}
        ),
        "safe_to_merge_gold_values": sorted(
            {bool(row.get("safe_to_merge_gold")) for row in repaired}
        ),
        "gold_rows_modified": 0,
        "valid": True,
    }
    return repaired, report


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rows", action="append", required=True)
    parser.add_argument("--crop-height", type=int, default=44)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    repaired, report = build_repairs(
        input_path,
        parse_row_numbers(args.rows),
        root=root,
        crop_height=args.crop_height,
    )
    output_path = (
        args.output_jsonl
        if args.output_jsonl.is_absolute()
        else root / args.output_jsonl
    )
    report_path = (
        args.report_json if args.report_json.is_absolute() else root / args.report_json
    )
    write_jsonl(output_path, repaired)
    report["output_jsonl"] = output_path.resolve().as_posix()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
