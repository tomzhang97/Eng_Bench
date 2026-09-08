#!/usr/bin/env python3
"""Propose review-only regions for capsule-shaped labels on legacy P&IDs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


BBox = list[int]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(row)
    return rows


def bbox_iou(left: Iterable[int], right: Iterable[int]) -> float:
    ax1, ay1, ax2, ay2 = (int(value) for value in left)
    bx1, by1, bx2, by2 = (int(value) for value in right)
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if not intersection:
        return 0.0
    left_area = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    right_area = max(0, bx2 - bx1) * max(0, by2 - by1)
    return intersection / (left_area + right_area - intersection)


def bbox_center(bbox: Iterable[int]) -> tuple[float, float]:
    x1, y1, x2, y2 = (int(value) for value in bbox)
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def near_excluded(candidate: BBox, excluded: Iterable[BBox]) -> bool:
    center_x, center_y = bbox_center(candidate)
    for bbox in excluded:
        if bbox_iou(candidate, bbox) > 0.12:
            return True
        x1, y1, x2, y2 = bbox
        if x1 - 6 <= center_x <= x2 + 6 and y1 - 6 <= center_y <= y2 + 6:
            return True
    return False


def suppress_overlaps(
    candidates: Iterable[dict[str, Any]], *, threshold: float = 0.65
) -> list[dict[str, Any]]:
    ordered = sorted(
        candidates,
        key=lambda row: -(
            (int(row["bbox"][2]) - int(row["bbox"][0]))
            * (int(row["bbox"][3]) - int(row["bbox"][1]))
        ),
    )
    kept: list[dict[str, Any]] = []
    for row in ordered:
        if any(bbox_iou(row["bbox"], other["bbox"]) > threshold for other in kept):
            continue
        kept.append(row)
    return kept


def associate_ocr(
    bbox: BBox,
    ocr_rows: Iterable[dict[str, Any]],
    *, margin: int = 10,
) -> list[dict[str, Any]]:
    x1, y1, x2, y2 = bbox
    alternatives: dict[tuple[str, float], dict[str, Any]] = {}
    for row in ocr_rows:
        raw_bbox = row.get("bbox")
        if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            continue
        center_x, center_y = bbox_center(raw_bbox)
        if not (x1 - margin <= center_x <= x2 + margin):
            continue
        if not (y1 - margin <= center_y <= y2 + margin):
            continue
        text = str(row.get("proposed_text") or "").strip()
        if not text:
            continue
        confidence = round(float(row.get("ocr_confidence") or 0.0), 5)
        key = (text, confidence)
        alternatives[key] = {
            "text": text,
            "confidence": confidence,
            "candidate_id": str(row.get("candidate_id") or ""),
        }
    return sorted(
        alternatives.values(),
        key=lambda row: (-float(row["confidence"]), str(row["text"])),
    )


def detect_capsules(image_path: Path) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is required. Run this tool with the isolated OCR environment."
        ) from exc

    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Unable to read image: {image_path}")
    binary = cv2.threshold(
        image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    contours, _ = cv2.findContours(
        binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )
    candidates: list[dict[str, Any]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width <= 0 or height <= 0:
            continue
        extent = float(cv2.contourArea(contour)) / (width * height)
        horizontal = (
            48 <= width <= 150
            and 12 <= height <= 50
            and 1.7 <= width / height <= 8.0
        )
        vertical = (
            12 <= width <= 50
            and 48 <= height <= 150
            and 1.7 <= height / width <= 8.0
        )
        if not (horizontal or vertical) or not (0.35 <= extent <= 0.96):
            continue
        candidates.append(
            {
                "bbox": [x, y, x + width, y + height],
                "orientation": "horizontal" if horizontal else "vertical",
                "contour_extent": round(extent, 6),
            }
        )
    return suppress_overlaps(candidates), (int(image.shape[1]), int(image.shape[0]))


def candidate_id(row: dict[str, Any]) -> str:
    identity = {
        "doc_id": row["doc_id"],
        "page_index": row["page_index"],
        "bbox": row["bbox"],
        "source": row["source"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"pidcapsule__{digest}"


def build_proposals(
    *,
    geometry_rows: Iterable[dict[str, Any]],
    ocr_rows: Iterable[dict[str, Any]],
    excluded_bboxes: Iterable[BBox],
    doc_id: str,
    version_id: str,
    page_index: int,
    image_path: str,
) -> list[dict[str, Any]]:
    excluded = list(excluded_bboxes)
    ocr = list(ocr_rows)
    proposals: list[dict[str, Any]] = []
    for geometry in geometry_rows:
        bbox = [int(value) for value in geometry["bbox"]]
        if near_excluded(bbox, excluded):
            continue
        alternatives = associate_ocr(bbox, ocr)
        best = alternatives[0] if alternatives else {"text": "", "confidence": 0.0}
        row: dict[str, Any] = {
            "doc_id": doc_id,
            "version_id": version_id,
            "page_index": page_index,
            "bbox": bbox,
            "target_text": "",
            "proposed_text": str(best["text"]),
            "category": "unknown_microtext",
            "source": "pid_capsule_geometry_probe",
            "review_status": "needs_review",
            "corrected_text": "",
            "question_text": "What text is shown in this bounded P&ID label region?",
            "image_path": image_path,
            "text_context": str(best["text"]),
            "ocr_confidence": float(best["confidence"]),
            "review_notes": (
                "Geometry-only P&ID capsule proposal; requires full-page visual QA, "
                "category assignment, text correction, and human verification."
            ),
            "machine_probe_status": "capsule_geometry_needs_visual_qa",
            "machine_probe_reason": "legacy_pid_bounded_label_contour",
            "capsule_orientation": str(geometry["orientation"]),
            "capsule_contour_extent": float(geometry["contour_extent"]),
            "ocr_alternatives": alternatives,
            "safe_to_merge_gold": False,
        }
        row["candidate_id"] = candidate_id(row)
        proposals.append(row)
    proposals.sort(key=lambda row: (row["bbox"][1], row["bbox"][0]))
    return proposals


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--page-index", type=int, required=True)
    parser.add_argument("--ocr-input", type=Path, action="append", default=[])
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    image_path = args.image if args.image.is_absolute() else root / args.image
    geometry, image_size = detect_capsules(image_path)
    ocr_rows = [
        row
        for path in args.ocr_input
        for row in read_jsonl(path if path.is_absolute() else root / path)
    ]
    excluded_rows = [
        row
        for path in args.exclude
        for row in read_jsonl(path if path.is_absolute() else root / path)
    ]
    excluded_bboxes = [
        [int(value) for value in row["bbox"]]
        for row in excluded_rows
        if isinstance(row.get("bbox"), list) and len(row["bbox"]) == 4
    ]
    relative_image = image_path.relative_to(root).as_posix()
    proposals = build_proposals(
        geometry_rows=geometry,
        ocr_rows=ocr_rows,
        excluded_bboxes=excluded_bboxes,
        doc_id=args.doc_id,
        version_id=args.version_id,
        page_index=args.page_index,
        image_path=relative_image,
    )
    output_path = args.output if args.output.is_absolute() else root / args.output
    report_path = args.report if args.report.is_absolute() else root / args.report
    write_jsonl(output_path, proposals)
    report = {
        "goal": "Gold v2.0 Global",
        "image_path": relative_image,
        "image_size": list(image_size),
        "geometry_candidates_after_nms": len(geometry),
        "excluded_reference_rows": len(excluded_bboxes),
        "ocr_input_rows": len(ocr_rows),
        "proposal_rows": len(proposals),
        "proposals_with_ocr": sum(bool(row["proposed_text"]) for row in proposals),
        "orientations": {
            orientation: sum(row["capsule_orientation"] == orientation for row in proposals)
            for orientation in ("horizontal", "vertical")
        },
        "active_gold_modified": False,
        "interpretation": (
            "Review-only geometry probe. Rows are not category-ready and require "
            "machine visual curation plus human verification before Gold promotion."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
