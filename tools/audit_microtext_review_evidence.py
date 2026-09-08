#!/usr/bin/env python3
"""Audit microtext page evidence and materialize reviewer-readable crops."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(root: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else root / path


def root_relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("item_id") or row.get("id") or "").strip()


def parse_bbox(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    values = row.get("bbox") or row.get("bbox_px") or []
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    try:
        bbox = tuple(int(round(float(value))) for value in values)
    except (TypeError, ValueError):
        return None
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        return None
    return bbox


def image_metrics(image: Image.Image) -> dict[str, Any]:
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    pixels = sum(histogram)
    minimum = next(index for index, count in enumerate(histogram) if count)
    maximum = next(index for index, count in reversed(list(enumerate(histogram))) if count)
    mean = sum(index * count for index, count in enumerate(histogram)) / pixels
    variance = sum((index - mean) ** 2 * count for index, count in enumerate(histogram)) / pixels
    entropy = -sum(
        (count / pixels) * math.log2(count / pixels) for count in histogram if count
    )
    return {
        "width": grayscale.width,
        "height": grayscale.height,
        "minimum": minimum,
        "maximum": maximum,
        "dynamic_range": maximum - minimum,
        "mean": round(mean, 4),
        "stddev": round(math.sqrt(variance), 4),
        "entropy": round(entropy, 4),
        "ink_fraction_below_245": round(sum(histogram[:245]) / pixels, 6),
        "ink_fraction_below_220": round(sum(histogram[:220]) / pixels, 6),
    }


def is_informative(metrics: dict[str, Any]) -> bool:
    """Reject blank or nearly uniform regions without claiming text correctness."""
    return bool(
        metrics["width"] >= 2
        and metrics["height"] >= 2
        and metrics["dynamic_range"] >= 12
        and metrics["ink_fraction_below_245"] >= 0.002
        and (metrics["stddev"] >= 2.0 or metrics["entropy"] >= 0.15)
    )


def padded_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
    pad_x: int,
    pad_y: int,
) -> tuple[int, int, int, int]:
    return (
        max(0, bbox[0] - max(0, pad_x)),
        max(0, bbox[1] - max(0, pad_y)),
        min(image_size[0], bbox[2] + max(0, pad_x)),
        min(image_size[1], bbox[3] + max(0, pad_y)),
    )


def held_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
    held = dict(row)
    held.update(
        {
            "review_status": "machine_held",
            "machine_qa_status": "machine_held",
            "machine_hold_reason": reason,
            "machine_qa_notes": (
                "Held by authoritative page-pixel evidence audit; do not assign or merge."
            ),
            "safe_to_merge_gold": False,
        }
    )
    return held


def audit_rows(
    root: Path,
    rows: list[dict[str, Any]],
    crop_dir: Path,
    *,
    pad_x: int = 16,
    pad_y: int = 16,
    max_image_pixels: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    status_counts: Counter[str] = Counter()
    doc_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    previous_limit = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        for index, row in enumerate(rows, start=1):
            identifier = candidate_id(row)
            detail: dict[str, Any] = {
                "row_number": index,
                "candidate_id": identifier,
                "doc_id": str(row.get("doc_id") or ""),
                "category": str(row.get("category") or ""),
                "proposed_text": str(row.get("proposed_text") or row.get("target_text") or ""),
                "original_crop_path": str(row.get("crop_path") or ""),
            }
            reason = ""
            if not identifier:
                reason = "missing_candidate_id"
            elif identifier in seen_ids:
                raise ValueError(f"duplicate candidate_id in input: {identifier}")
            else:
                seen_ids.add(identifier)

            image_path = resolve_path(root, row.get("image_path"))
            bbox = parse_bbox(row)
            if not reason and (image_path is None or not image_path.is_file()):
                reason = "missing_authoritative_page_image"
            if not reason and bbox is None:
                reason = "invalid_bbox"

            page_crop: Image.Image | None = None
            review_crop: Image.Image | None = None
            if not reason and image_path is not None and bbox is not None:
                with Image.open(image_path) as page:
                    if (
                        bbox[0] < 0
                        or bbox[1] < 0
                        or bbox[2] > page.width
                        or bbox[3] > page.height
                    ):
                        reason = "bbox_out_of_frame"
                    else:
                        page_crop = page.crop(bbox).convert("RGB")
                        crop_box = padded_bbox(bbox, page.size, pad_x, pad_y)
                        review_crop = page.crop(crop_box).convert("RGB")
                        detail["page_image"] = root_relative(root, image_path)
                        detail["page_size"] = [page.width, page.height]
                        detail["bbox"] = list(bbox)
                        detail["materialized_crop_bbox"] = list(crop_box)

            metrics = image_metrics(page_crop) if page_crop is not None else None
            detail["page_bbox_metrics"] = metrics
            if not reason and metrics is not None and not is_informative(metrics):
                reason = "blank_or_low_information_page_bbox"

            if reason:
                status_counts[reason] += 1
                detail["status"] = "held"
                detail["reason"] = reason
                held.append(held_row(row, reason))
            else:
                assert review_crop is not None
                crop_path = crop_dir / f"{identifier}.png"
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                review_crop.save(crop_path, format="PNG")
                audited = dict(row)
                audited["evidence_audit_original_crop_path"] = str(row.get("crop_path") or "")
                audited["crop_path"] = root_relative(root, crop_path)
                audited["evidence_audit_status"] = "pixel_informative_needs_visual_review"
                audited["evidence_audit_source"] = "authoritative_page_image_bbox"
                audited["evidence_audit_crop_bbox"] = detail["materialized_crop_bbox"]
                audited["evidence_audit_page_bbox_metrics"] = metrics
                audited["safe_to_merge_gold"] = False
                passing.append(audited)
                status_counts["pixel_informative_needs_visual_review"] += 1
                detail["status"] = "passing"
                detail["reason"] = "pixel_informative_needs_visual_review"
                detail["materialized_crop_path"] = audited["crop_path"]
                detail["materialized_crop_sha256"] = file_sha256(crop_path)
            doc_counts[detail["doc_id"]] += 1
            category_counts[detail["category"]] += 1
            details.append(detail)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    report = {
        "input_rows": len(rows),
        "passing_rows": len(passing),
        "held_rows": len(held),
        "status_counts": dict(sorted(status_counts.items())),
        "doc_counts": dict(sorted(doc_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "thresholds": {
            "minimum_dynamic_range": 12,
            "minimum_ink_fraction_below_245": 0.002,
            "minimum_stddev_or_entropy": {"stddev": 2.0, "entropy": 0.15},
        },
        "crop_padding": {"x": max(0, pad_x), "y": max(0, pad_y)},
        "interpretation": (
            "Passing means only that the authoritative page bbox contains nonuniform pixels. "
            "Visual review must still confirm target text, category, clipping, and context."
        ),
        "rows": details,
    }
    return passing, held, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--passing-jsonl", required=True)
    parser.add_argument("--held-jsonl", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--crop-dir", required=True)
    parser.add_argument("--pad-x", type=int, default=16)
    parser.add_argument("--pad-y", type=int, default=16)
    parser.add_argument("--max-image-pixels", type=int)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    input_path = resolve_path(root, args.input)
    passing_path = resolve_path(root, args.passing_jsonl)
    held_path = resolve_path(root, args.held_jsonl)
    report_path = resolve_path(root, args.report_json)
    crop_dir = resolve_path(root, args.crop_dir)
    assert input_path is not None
    assert passing_path is not None
    assert held_path is not None
    assert report_path is not None
    assert crop_dir is not None

    rows = read_jsonl(input_path)
    passing, held, report = audit_rows(
        root,
        rows,
        crop_dir,
        pad_x=max(0, args.pad_x),
        pad_y=max(0, args.pad_y),
        max_image_pixels=args.max_image_pixels,
    )
    write_jsonl(passing_path, passing)
    write_jsonl(held_path, held)
    report.update(
        {
            "input_path": root_relative(root, input_path),
            "input_sha256": file_sha256(input_path),
            "passing_path": root_relative(root, passing_path),
            "passing_sha256": file_sha256(passing_path),
            "held_path": root_relative(root, held_path),
            "held_sha256": file_sha256(held_path),
            "crop_dir": root_relative(root, crop_dir),
        }
    )
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "input_rows": len(rows),
                "passing_rows": len(passing),
                "held_rows": len(held),
            },
            indent=2,
        )
    )
    return 0 if not held else 1


if __name__ == "__main__":
    raise SystemExit(main())
