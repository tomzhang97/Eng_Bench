#!/usr/bin/env python3
"""Audit microtext gold rows for benchmark-usability failures."""
from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PIN_LABEL_RE = re.compile(r"^[A-Z]{1,4}[0-9][A-Z0-9._-]*$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def bbox_tuple(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    bbox = row.get("bbox") or []
    if len(bbox) < 4:
        return None
    return tuple(int(round(float(value))) for value in bbox[:4])


def quantized_bbox(row: dict[str, Any], step: int = 4) -> tuple[int, int, int, int] | None:
    bbox = bbox_tuple(row)
    if bbox is None:
        return None
    return tuple(int(round(value / step) * step) for value in bbox)


def image_candidates(root: Path, row: dict[str, Any]) -> list[Path]:
    doc_id = str(row.get("doc_id", ""))
    version_id = str(row.get("version_id", ""))
    page = int(row.get("page_index", 0))
    return [
        root / "images" / f"{doc_id}__{version_id}" / f"page_{page:04d}.png",
        root / "images" / f"{doc_id}__{version_id}" / f"page_{page:03d}.png",
        root / "derived" / "pages_300dpi" / doc_id / f"page_{page:04d}.png",
        root / "derived" / "pages_300dpi" / doc_id / f"page_{page:03d}.png",
    ]


def resolve_image(root: Path, row: dict[str, Any]) -> Path | None:
    return next((path for path in image_candidates(root, row) if path.exists()), None)


def png_dimensions(path: Path) -> tuple[int, int]:
    """Read PNG dimensions from IHDR without decoding a potentially huge raster."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError("invalid PNG signature or IHDR header")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError("invalid PNG dimensions")
    return width, height


def pin_label_flags(row: dict[str, Any]) -> list[str]:
    if row.get("category") != "pin_label":
        return []
    text = str(row.get("text_gt", "")).strip()
    flags: list[str] = []
    if text == "L4V":
        flags.append("postal_fragment")
    if text in {"Rev", "REV"}:
        flags.append("revision_literal")
    if len(text) > 16:
        flags.append("long_pin_label")
    if re.search(r"\s", text):
        flags.append("space_in_pin_label")
    if not PIN_LABEL_RE.match(text):
        flags.append("weak_pin_label_pattern")
    return flags


def audit(root: Path) -> dict[str, Any]:
    items = load_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    questions = load_jsonl(root / "microtext" / "annotations" / "microtext_questions.jsonl")
    question_item_ids = {
        item_id
        for question in questions
        for item_id in (question.get("item_ids") or [])
    }

    ids: Counter[str] = Counter()
    regions: defaultdict[tuple[Any, ...], list[str]] = defaultdict(list)
    full_regions: defaultdict[tuple[Any, ...], list[str]] = defaultdict(list)
    categories: Counter[str] = Counter()
    docs: Counter[str] = Counter()
    missing_images: list[str] = []
    unreadable_images: list[dict[str, str]] = []
    invalid_bboxes: list[str] = []
    out_of_frame: list[dict[str, Any]] = []
    missing_questions: list[str] = []
    missing_source_candidates: list[str] = []
    content_flags: list[dict[str, str]] = []

    for row in items:
        item_id = str(row.get("item_id", ""))
        ids[item_id] += 1
        categories[str(row.get("category", "unknown"))] += 1
        docs[str(row.get("doc_id", "unknown"))] += 1
        if item_id not in question_item_ids:
            missing_questions.append(item_id)
        if not row.get("source_candidate_id"):
            missing_source_candidates.append(item_id)

        bbox = bbox_tuple(row)
        if bbox is None or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            invalid_bboxes.append(item_id)
            continue
        qbbox = quantized_bbox(row)
        regions[(row.get("doc_id"), int(row.get("page_index", 0)), qbbox)].append(item_id)
        full_regions[
            (
                row.get("doc_id"),
                int(row.get("page_index", 0)),
                qbbox,
                str(row.get("text_gt", "")).strip(),
            )
        ].append(item_id)

        image_path = resolve_image(root, row)
        if image_path is None:
            missing_images.append(item_id)
            continue
        try:
            width, height = png_dimensions(image_path)
        except (OSError, ValueError, struct.error) as error:
            unreadable_images.append(
                {
                    "item_id": item_id,
                    "image_path": image_path.as_posix(),
                    "error": str(error),
                }
            )
            continue
        if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > width or bbox[3] > height:
            out_of_frame.append(
                {
                    "item_id": item_id,
                    "image_path": image_path.as_posix(),
                    "bbox": list(bbox),
                    "image_size": [width, height],
                }
            )

        flags = pin_label_flags(row)
        if flags:
            content_flags.append(
                {
                    "item_id": item_id,
                    "doc_id": str(row.get("doc_id", "")),
                    "text_gt": str(row.get("text_gt", "")),
                    "flags": ",".join(flags),
                }
            )

    duplicate_ids = sorted(item_id for item_id, count in ids.items() if count > 1)
    duplicate_regions = {
        "|".join(map(str, key)): value
        for key, value in regions.items()
        if len(value) > 1
    }
    duplicate_full_regions = {
        "|".join(map(str, key)): value
        for key, value in full_regions.items()
        if len(value) > 1
    }

    critical_failures = (
        len(duplicate_ids)
        + len(duplicate_regions)
        + len(duplicate_full_regions)
        + len(missing_questions)
        + len(missing_images)
        + len(unreadable_images)
        + len(invalid_bboxes)
        + len(out_of_frame)
        + len(missing_source_candidates)
    )
    return {
        "items": len(items),
        "questions": len(questions),
        "categories": dict(sorted(categories.items())),
        "docs": dict(sorted(docs.items())),
        "duplicate_ids": duplicate_ids,
        "duplicate_regions": duplicate_regions,
        "duplicate_full_regions": duplicate_full_regions,
        "missing_questions": missing_questions,
        "missing_images": missing_images,
        "unreadable_images": unreadable_images,
        "invalid_bboxes": invalid_bboxes,
        "out_of_frame": out_of_frame,
        "missing_source_candidates": missing_source_candidates,
        "content_flags": content_flags,
        "critical_failures": critical_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit microtext gold quality")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    report = audit(Path(args.root))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["critical_failures"]:
        print(f"[FAIL] {report['critical_failures']} critical microtext quality failures")
        return 1
    print("[OK] Microtext quality audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
