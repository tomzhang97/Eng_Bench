#!/usr/bin/env python3
"""Repair missing page-image paths in microtext review queues.

The raw textlayer queues intentionally keep candidate provenance close to the
mined spans, but some were written before `image_path` was populated. This
helper writes a repaired derivative queue by resolving each row's `doc_id` and
`page_index` against rendered page images, and only keeps a repair when the
candidate bbox fits inside that page image.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


QUESTION_BY_CATEGORY = {
    "dimension_value": "What dimension value is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "instrument_tag": "What instrument tag is shown in this region?",
    "pipe_line_tag": "What pipe or line tag is shown in this region?",
    "pin_label": "What component or pin label is shown at the indicated spot?",
    "process_label": "What process step or stream label is shown in this region?",
    "process_value": "What process value is shown in this region?",
    "room_label": "What room label is shown in this region?",
}
DEFAULT_QUESTION = "What text is shown in this small engineering label region?"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_page_index(row: dict[str, Any]) -> int | None:
    value = row.get("page_index", row.get("page", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bbox_xyxy(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    bbox = row.get("bbox")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return None
    try:
        x1, y1, x2, y2 = [int(round(float(value))) for value in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def relative_posix(path: Path) -> str:
    return path.as_posix()


def candidate_page_paths(root: Path, doc_id: str, page_index: int) -> list[Path]:
    page_name = f"page_{page_index:03d}.png"
    candidates: list[Path] = []
    for dpi_dir in ("pages_300dpi", "pages_200dpi"):
        candidates.append(root / "derived" / dpi_dir / doc_id / page_name)
    return candidates


def bbox_fits(path: Path, bbox: tuple[int, int, int, int]) -> bool:
    with Image.open(path) as image:
        width, height = image.size
    x1, y1, x2, y2 = bbox
    return x1 >= 0 and y1 >= 0 and x2 <= width and y2 <= height


def existing_image_path(root: Path, row: dict[str, Any], bbox: tuple[int, int, int, int]) -> str | None:
    value = str(row.get("image_path") or "").strip().replace("\\", "/")
    if not value:
        return None
    path = root / value
    if not path.is_file():
        return None
    if not bbox_fits(path, bbox):
        return None
    return value


def resolve_image_path(root: Path, row: dict[str, Any], stats: Counter[str]) -> str | None:
    bbox = bbox_xyxy(row)
    if bbox is None:
        stats["invalid_bbox"] += 1
        return None
    existing = existing_image_path(root, row, bbox)
    if existing:
        stats["kept_existing_image_path"] += 1
        return existing
    doc_id = str(row.get("doc_id") or "").strip()
    page_index = safe_page_index(row)
    if not doc_id or page_index is None:
        stats["missing_doc_or_page"] += 1
        return None
    existing_pages = [path for path in candidate_page_paths(root, doc_id, page_index) if path.is_file()]
    if not existing_pages:
        stats["missing_page_image"] += 1
        return None
    for path in existing_pages:
        if bbox_fits(path, bbox):
            stats["repaired_image_path"] += 1
            return relative_posix(path.relative_to(root))
    stats["bbox_out_of_frame"] += 1
    return None


def question_for_category(category: Any) -> str:
    return QUESTION_BY_CATEGORY.get(str(category or "").strip(), DEFAULT_QUESTION)


def enrich_row(row: dict[str, Any], image_path: str, repair_label: str) -> dict[str, Any]:
    repaired = dict(row)
    repaired["image_path"] = image_path
    if not str(repaired.get("proposed_text") or "").strip():
        repaired["proposed_text"] = repaired.get("target_text") or repaired.get("raw_text") or ""
    if not str(repaired.get("text_context") or "").strip():
        repaired["text_context"] = repaired.get("raw_text") or repaired.get("target_text") or ""
    if not str(repaired.get("question_text") or "").strip():
        repaired["question_text"] = question_for_category(repaired.get("category"))
    if str(repaired.get("review_status") or "").strip().lower() == "candidate":
        repaired["review_status"] = "needs_review"
    repaired["image_path_repair_label"] = repair_label
    return repaired


def repair_rows(root: Path, rows: list[dict[str, Any]], repair_label: str) -> tuple[list[dict[str, Any]], Counter[str]]:
    stats: Counter[str] = Counter()
    repaired_rows: list[dict[str, Any]] = []
    for row in rows:
        stats["input_rows"] += 1
        image_path = resolve_image_path(root, row, stats)
        if not image_path:
            stats["unrepaired_rows"] += 1
            continue
        repaired_rows.append(enrich_row(row, image_path, repair_label))
        stats["output_rows"] += 1
    return repaired_rows, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair image_path fields in microtext review queues.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", required=True, help="Input review JSONL")
    parser.add_argument("--output", required=True, help="Output repaired review JSONL")
    parser.add_argument("--report-json", required=True, help="Repair report JSON")
    parser.add_argument("--repair-label", default="manual_repair")
    parser.add_argument("--strict", action="store_true", help="Fail when any input row cannot be repaired")
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    repaired_rows, stats = repair_rows(root, rows, args.repair_label)
    write_jsonl(root / args.output, repaired_rows)
    report = {
        "input": args.input,
        "output": args.output,
        "repair_label": args.repair_label,
        "stats": dict(sorted(stats.items())),
    }
    write_json(root / args.report_json, report)
    print(f"[OK] Wrote {len(repaired_rows)} repaired rows to {args.output}")
    print(f"[OK] Wrote report to {args.report_json}")
    print(json.dumps(report["stats"], indent=2, sort_keys=True))
    return 1 if args.strict and stats.get("unrepaired_rows") else 0


if __name__ == "__main__":
    raise SystemExit(main())
