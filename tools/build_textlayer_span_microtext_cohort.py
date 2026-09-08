#!/usr/bin/env python3
"""Build review-only MicroText rows from explicitly selected PDF text spans."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def question_for(category: str) -> str:
    return {
        "dimension_value": "What dimension value is shown in this region?",
        "equipment_tag": "What equipment tag is shown in this region?",
        "instrument_tag": "What instrument tag is shown in this region?",
        "pin_label": "What pin or terminal label is shown in this region?",
        "process_label": "What process label is shown in this region?",
        "process_value": "What process value is shown in this region?",
        "room_label": "What room or area label is shown in this region?",
    }.get(category, "What text is shown in this small engineering label region?")


def candidate_id(
    doc_id: str,
    version_id: str,
    page_index: int,
    bbox: list[int],
    target_text: str,
    category: str,
) -> str:
    payload = json.dumps(
        {
            "doc_id": doc_id,
            "version_id": version_id,
            "page_index": page_index,
            "bbox": bbox,
            "target_text": target_text,
            "category": category,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mtcand__{hashlib.sha256(payload).hexdigest()[:20]}"


def load_page_payload(path: Path, expected_page: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("spans"), list):
        raise ValueError(f"invalid text-layer page payload: {path}")
    if int(payload.get("doc_page", -1)) != expected_page:
        raise ValueError(
            f"text-layer page mismatch: expected {expected_page}, got {payload.get('doc_page')}"
        )
    return [row for row in payload["spans"] if isinstance(row, dict)]


def select_span(
    spans: list[dict[str, Any]],
    selection: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    source_text = str(selection.get("source_text") or "").strip()
    if not source_text:
        raise ValueError("selection source_text is required")
    matches = [row for row in spans if str(row.get("text") or "").strip() == source_text]
    expected_bbox = selection.get("bbox_pdf")
    if expected_bbox is not None:
        if not isinstance(expected_bbox, list) or len(expected_bbox) != 4:
            raise ValueError(f"bbox_pdf must contain four values for {source_text!r}")
        expected = [float(value) for value in expected_bbox]
        matches = [
            row
            for row in matches
            if isinstance(row.get("bbox"), list)
            and len(row["bbox"]) == 4
            and all(
                abs(float(actual) - wanted) <= tolerance
                for actual, wanted in zip(row["bbox"], expected)
            )
        ]
    matches.sort(key=lambda row: tuple(float(value) for value in row.get("bbox", [])))
    occurrence = int(selection.get("occurrence") or 1)
    if occurrence < 1 or occurrence > len(matches):
        raise ValueError(
            f"span selection failed for {source_text!r}: occurrence={occurrence}, matches={len(matches)}"
        )
    return matches[occurrence - 1]


def select_spans(
    spans: list[dict[str, Any]],
    selection: dict[str, Any],
    tolerance: float,
) -> list[dict[str, Any]]:
    span_selections = selection.get("spans")
    if span_selections is None:
        return [select_span(spans, selection, tolerance)]
    if not isinstance(span_selections, list) or not span_selections:
        raise ValueError("selection spans must be a non-empty list")
    selected: list[dict[str, Any]] = []
    identities: set[tuple[str, tuple[float, ...]]] = set()
    for span_selection in span_selections:
        if not isinstance(span_selection, dict):
            raise ValueError("each span selection must be an object")
        span = select_span(spans, span_selection, tolerance)
        bbox = tuple(float(value) for value in span.get("bbox") or [])
        identity = (str(span.get("text") or "").strip(), bbox)
        if identity in identities:
            raise ValueError(f"duplicate span in selection: {identity[0]!r} {list(bbox)}")
        identities.add(identity)
        selected.append(span)
    return selected


def union_bbox(selected_spans: list[dict[str, Any]]) -> list[float]:
    boxes = [span.get("bbox") for span in selected_spans]
    if any(not isinstance(bbox, list) or len(bbox) != 4 for bbox in boxes):
        raise ValueError(f"selected span has no usable bbox: {selected_spans}")
    return [
        min(float(bbox[0]) for bbox in boxes),
        min(float(bbox[1]) for bbox in boxes),
        max(float(bbox[2]) for bbox in boxes),
        max(float(bbox[3]) for bbox in boxes),
    ]


def bbox_to_pixels(
    bbox_pdf: list[float],
    page_rect: fitz.Rect,
    image_width: int,
    image_height: int,
    padding_x: int,
    padding_y: int,
) -> list[int]:
    if page_rect.width <= 0 or page_rect.height <= 0:
        raise ValueError("PDF page has invalid dimensions")
    scale_x = image_width / page_rect.width
    scale_y = image_height / page_rect.height
    x0 = max(0, math.floor(bbox_pdf[0] * scale_x) - padding_x)
    y0 = max(0, math.floor(bbox_pdf[1] * scale_y) - padding_y)
    x1 = min(image_width, math.ceil(bbox_pdf[2] * scale_x) + padding_x)
    y1 = min(image_height, math.ceil(bbox_pdf[3] * scale_y) + padding_y)
    bbox = [x0, y0, x1, y1]
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"degenerate converted bbox: {bbox}")
    return bbox


def build_cohort(
    root: Path,
    pdf_path: Path,
    textlayer_dir: Path,
    pages_dir: Path,
    selection_payload: dict[str, Any],
    padding_x: int,
    padding_y: int,
    tolerance: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = ["doc_id", "version_id", "source_candidate_id", "same_model_id", "source_url"]
    missing = [field for field in required if not str(selection_payload.get(field) or "").strip()]
    if missing:
        raise ValueError(f"selection payload missing fields: {', '.join(missing)}")
    selections = selection_payload.get("selections")
    if not isinstance(selections, list) or not selections:
        raise ValueError("selection payload requires a non-empty selections list")

    doc_id = str(selection_payload["doc_id"]).strip()
    version_id = str(selection_payload["version_id"]).strip()
    source_candidate_id = str(selection_payload["source_candidate_id"]).strip()
    same_model_id = str(selection_payload["same_model_id"]).strip()
    source_url = str(selection_payload["source_url"]).strip()
    source_hash = sha256(pdf_path)
    rows: list[dict[str, Any]] = []

    with fitz.open(pdf_path) as document:
        page_cache: dict[int, tuple[list[dict[str, Any]], fitz.Rect, int, int, Path]] = {}
        for selection in selections:
            if not isinstance(selection, dict):
                raise ValueError("each selection must be an object")
            page_index = int(selection.get("page_index", -1))
            if page_index < 0 or page_index >= document.page_count:
                raise ValueError(f"invalid page_index: {page_index}")
            if page_index not in page_cache:
                textlayer_path = textlayer_dir / f"page_{page_index:03d}.json"
                image_path = pages_dir / f"page_{page_index:03d}.png"
                if not textlayer_path.is_file() or not image_path.is_file():
                    raise FileNotFoundError(
                        f"missing page evidence for page {page_index}: {textlayer_path}, {image_path}"
                    )
                spans = load_page_payload(textlayer_path, page_index)
                with Image.open(image_path) as image:
                    width, height = image.size
                page_cache[page_index] = (
                    spans,
                    fitz.Rect(document[page_index].rect),
                    width,
                    height,
                    image_path,
                )
            spans, page_rect, width, height, image_path = page_cache[page_index]
            selected_spans = select_spans(spans, selection, tolerance)
            bbox_pdf = union_bbox(selected_spans)
            bbox = bbox_to_pixels(
                bbox_pdf,
                page_rect,
                width,
                height,
                padding_x,
                padding_y,
            )
            source_text = " ".join(
                str(span.get("text") or "").strip() for span in selected_spans
            ).strip()
            target_text = str(selection.get("target_text") or source_text).strip()
            category = str(selection.get("category") or "unknown_microtext").strip()
            if not target_text:
                raise ValueError(f"blank target_text for {source_text!r}")
            image_relpath = image_path.relative_to(root).as_posix()
            row = {
                "candidate_id": candidate_id(
                    doc_id, version_id, page_index, bbox, target_text, category
                ),
                "doc_id": doc_id,
                "version_id": version_id,
                "page_index": page_index,
                "bbox": bbox,
                "target_text": target_text,
                "raw_text": source_text,
                "proposed_text": target_text,
                "answer": target_text,
                "category": category,
                "question_text": question_for(category),
                "image_path": image_relpath,
                "source": "pdf_textlayer_exact_span_curated",
                "source_candidate_id": source_candidate_id,
                "same_model_id": same_model_id,
                "source_url": source_url,
                "source_sha256": source_hash,
                "source_textlayer_bbox_pdf": bbox_pdf,
                "review_status": "needs_review",
                "promotion_state": "unreviewed_candidate",
                "machine_qa_status": "selected_for_human_review",
                "machine_visual_qa_status": "pending_contact_sheet_review",
                "review_bucket": "v2_0_source_expansion",
                "split": "provisional_review",
                "safe_to_merge_gold": False,
                "review_notes": str(selection.get("notes") or "").strip(),
            }
            rows.append(row)

    ids = [str(row["candidate_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate ID collision in selected cohort")
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "review_only_exact_textlayer_span_selection",
        "doc_id": doc_id,
        "source_candidate_id": source_candidate_id,
        "source_sha256": source_hash,
        "selected_rows": len(rows),
        "selected_pages": sorted({int(row["page_index"]) for row in rows}),
        "by_category": dict(sorted(Counter(row["category"] for row in rows).items())),
        "padding_px": {"x": padding_x, "y": padding_y},
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold": False,
        "valid": True,
    }
    return rows, report


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--textlayer-dir", type=Path, required=True)
    parser.add_argument("--pages-dir", type=Path, required=True)
    parser.add_argument("--selection-json", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--padding-x", type=int, default=14)
    parser.add_argument("--padding-y", type=int, default=8)
    parser.add_argument("--bbox-tolerance", type=float, default=0.05)
    args = parser.parse_args()

    root = args.root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    payload = json.loads(resolve(args.selection_json).read_text(encoding="utf-8"))
    rows, report = build_cohort(
        root=root,
        pdf_path=resolve(args.pdf),
        textlayer_dir=resolve(args.textlayer_dir),
        pages_dir=resolve(args.pages_dir),
        selection_payload=payload,
        padding_x=args.padding_x,
        padding_y=args.padding_y,
        tolerance=args.bbox_tolerance,
    )
    write_jsonl(resolve(args.output_jsonl), rows)
    report_path = resolve(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
