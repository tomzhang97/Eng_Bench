#!/usr/bin/env python3
"""Build review batches from mined microtext candidates."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


CATEGORY_PRIORITY = [
    "instrument_tag",
    "equipment_tag",
    "process_label",
    "tolerance_value",
    "room_label",
    "dimension_value",
    "wire_number",
    "gdandt_symbol",
    "pin_label",
    "door_label",
    "room_number",
]

QUESTION_BY_CATEGORY = {
    "instrument_tag": "What instrument tag is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "process_label": "What process step or stream label is shown in this region?",
    "tolerance_value": "What tolerance is specified in this small text region?",
    "room_label": "What room label is shown in this region?",
    "dimension_value": "What dimension value is shown in this region?",
    "wire_number": "What wire number is shown in this region?",
    "gdandt_symbol": "What GD&T symbol or callout is shown in this region?",
    "pin_label": "What pin or component label is shown in this region?",
    "door_label": "What door label is shown in this region?",
    "room_number": "What room number is shown in this region?",
}
REFERENCE_DESIGNATOR_RE = re.compile(r"(?:R|C|L|D|U|J|P|TP|CN)\d{1,4}[A-Z]?", re.I)


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


def bbox_tuple(row: dict[str, Any]) -> tuple[int, int, int, int]:
    bbox = row.get("bbox") or row.get("bbox_px") or [0, 0, 0, 0]
    return tuple(int(round(float(value))) for value in bbox[:4])


def candidate_text(row: dict[str, Any]) -> str:
    """Return the candidate transcription across text-layer and OCR schemas."""
    return str(row.get("target_text") or row.get("proposed_text") or "").strip()


def item_key(row: dict[str, Any]) -> tuple[str, int, tuple[int, int, int, int], str]:
    return (
        str(row.get("doc_id", "")),
        int(row.get("page_index", row.get("page", 0))),
        bbox_tuple(row),
        str(row.get("text_gt") or candidate_text(row)).strip(),
    )


def priority_index(category: str) -> int:
    try:
        return CATEGORY_PRIORITY.index(category)
    except ValueError:
        return len(CATEGORY_PRIORITY)


def candidate_sort_key(row: dict[str, Any]) -> tuple[int, str, int, str]:
    return (
        priority_index(str(row.get("category", ""))),
        str(row.get("doc_id", "")),
        int(row.get("page_index", 0)),
        str(row.get("candidate_id", "")),
    )


def candidate_image_path(root: Path, row: dict[str, Any]) -> str | None:
    doc_id = str(row.get("doc_id", ""))
    version_id = str(row.get("version_id", ""))
    page = int(row.get("page_index", 0))
    candidates = [
        Path("images") / f"{doc_id}__{version_id}" / f"page_{page:04d}.png",
        Path("images") / f"{doc_id}__{version_id}" / f"page_{page:03d}.png",
        Path("derived") / "pages_300dpi" / doc_id / f"page_{page:03d}.png",
        Path("derived") / "pages_300dpi" / doc_id / f"page_{page:04d}.png",
    ]
    for rel_path in candidates:
        if (root / rel_path).exists():
            return rel_path.as_posix()
    return None


def bbox_intersects_image(root: Path, image_path: str, row: dict[str, Any]) -> bool:
    x1, y1, x2, y2 = bbox_tuple(row)
    with Image.open(root / image_path) as image:
        width, height = image.size
    return not (x2 < 0 or y2 < 0 or x1 > width or y1 > height)


def load_textlayer_rows(root: Path, doc_id: str) -> list[dict[str, Any]]:
    return load_jsonl(root / "derived" / "textlayer" / f"{doc_id}.jsonl")


def text_context(root: Path, row: dict[str, Any], window: int = 2) -> str:
    doc_id = str(row.get("doc_id", ""))
    page = int(row.get("page_index", 0))
    target = str(row.get("raw_text") or candidate_text(row)).strip()
    target_bbox = bbox_tuple(row)
    page_rows = [
        text_row
        for text_row in load_textlayer_rows(root, doc_id)
        if int(text_row.get("page", 0)) == page and str(text_row.get("text", "")).strip()
    ]
    page_rows.sort(key=lambda r: (bbox_tuple(r)[1], bbox_tuple(r)[0], str(r.get("text", ""))))
    if not page_rows:
        return target

    match_index = 0
    for idx, text_row in enumerate(page_rows):
        text = str(text_row.get("text", "")).strip()
        if bbox_tuple(text_row) == target_bbox or text == target or target in text:
            match_index = idx
            break

    start = max(0, match_index - window)
    end = min(len(page_rows), match_index + window + 1)
    return " | ".join(str(text_row.get("text", "")).strip() for text_row in page_rows[start:end])


def build_review_batch(
    root: str | Path,
    candidates: list[dict[str, Any]],
    existing_items: list[dict[str, Any]],
    limit: int,
    max_per_category: int,
    reviewed_rows: list[dict[str, Any]] | None = None,
    categories: set[str] | None = None,
    doc_ids: set[str] | None = None,
    source_candidate_id: str = "",
    max_per_text: int | None = None,
    max_per_doc: int | None = None,
    max_per_page: int | None = None,
    exact_text_only: bool = False,
    exclude_reference_designators: bool = False,
) -> list[dict[str, Any]]:
    root = Path(root)
    existing_keys = {item_key(row) for row in existing_items}
    reviewed_rows = reviewed_rows or []
    reviewed_candidate_ids = {str(row.get("candidate_id", "")) for row in reviewed_rows}
    reviewed_keys = {item_key(row) for row in reviewed_rows}
    counts: Counter[str] = Counter()
    text_counts: Counter[tuple[str, str]] = Counter()
    doc_counts: Counter[str] = Counter()
    page_counts: Counter[tuple[str, int]] = Counter()
    batch: list[dict[str, Any]] = []

    for candidate in sorted(candidates, key=candidate_sort_key):
        category = str(candidate.get("category", ""))
        doc_id = str(candidate.get("doc_id", ""))
        if categories is not None and category not in categories:
            continue
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        if candidate.get("review_status") not in {None, "", "candidate", "needs_review"}:
            continue
        if item_key(candidate) in existing_keys:
            continue
        if str(candidate.get("candidate_id", "")) in reviewed_candidate_ids:
            continue
        if item_key(candidate) in reviewed_keys:
            continue
        if counts[category] >= max_per_category:
            continue
        if max_per_doc and doc_counts[doc_id] >= max_per_doc:
            continue
        page_key = (doc_id, int(candidate.get("page_index", 0)))
        if max_per_page and page_counts[page_key] >= max_per_page:
            continue
        proposed_text = candidate_text(candidate)
        if (
            exclude_reference_designators
            and category == "pin_label"
            and REFERENCE_DESIGNATOR_RE.fullmatch(proposed_text)
        ):
            continue
        if exact_text_only and str(candidate.get("raw_text") or "").strip() != proposed_text:
            continue
        text_key = (category, proposed_text.casefold())
        if proposed_text and max_per_text and text_counts[text_key] >= max_per_text:
            continue
        image_path = candidate_image_path(root, candidate)
        if image_path is None:
            continue
        if not bbox_intersects_image(root, image_path, candidate):
            continue

        review_row = dict(candidate)
        review_row.update(
            {
                "review_status": "needs_review",
                "proposed_text": proposed_text,
                "corrected_text": "",
                "question_text": QUESTION_BY_CATEGORY.get(
                    category, "What text is shown in this small region?"
                ),
                "image_path": image_path,
                "text_context": text_context(root, candidate),
                "review_notes": "",
            }
        )
        if source_candidate_id:
            review_row["source_candidate_id"] = source_candidate_id
        batch.append(review_row)
        counts[category] += 1
        doc_counts[doc_id] += 1
        page_counts[page_key] += 1
        if proposed_text:
            text_counts[text_key] += 1
        if len(batch) >= limit:
            break

    return batch


def parse_csv_filter(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def load_reviewed_rows(root: Path, pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob(pattern)):
        rows.extend(load_jsonl(path))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a microtext review batch")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--candidates", default="microtext/annotations/microtext_candidates.jsonl")
    parser.add_argument("--items", default="microtext/annotations/microtext_items.jsonl")
    parser.add_argument("--output", default="microtext/annotations/microtext_review_batch_001.jsonl")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-per-category", type=int, default=80)
    parser.add_argument(
        "--max-per-text",
        type=int,
        default=0,
        help="Optional cap for repeated proposed text within each category; 0 disables the cap",
    )
    parser.add_argument(
        "--max-per-doc",
        type=int,
        default=0,
        help="Optional cap for rows from one source document; 0 disables the cap",
    )
    parser.add_argument(
        "--max-per-page",
        type=int,
        default=0,
        help="Optional cap for rows from one document page; 0 disables the cap",
    )
    parser.add_argument(
        "--exact-text-only",
        action="store_true",
        help="Keep only candidates whose proposed text equals the complete source text span",
    )
    parser.add_argument(
        "--exclude-reference-designators",
        action="store_true",
        help="Exclude generic component references such as R12, C4, U3, J5, and TP7",
    )
    parser.add_argument("--categories", help="Comma-separated category filter")
    parser.add_argument("--doc-ids", help="Comma-separated doc_id filter")
    parser.add_argument(
        "--source-candidate-id",
        default="",
        help="Optional source-candidate lineage ID to attach to every staged review row.",
    )
    parser.add_argument(
        "--reviewed-glob",
        default="microtext/annotations/microtext_review*_reviewed.jsonl",
        help="Glob of previously reviewed batches to suppress from future staging",
    )
    args = parser.parse_args()

    root = Path(args.root)
    candidates = load_jsonl(root / args.candidates)
    existing_items = load_jsonl(root / args.items)
    reviewed_rows = load_reviewed_rows(root, args.reviewed_glob)
    batch = build_review_batch(
        root=root,
        candidates=candidates,
        existing_items=existing_items,
        reviewed_rows=reviewed_rows,
        limit=args.limit,
        max_per_category=args.max_per_category,
        categories=parse_csv_filter(args.categories),
        doc_ids=parse_csv_filter(args.doc_ids),
        source_candidate_id=args.source_candidate_id,
        max_per_text=args.max_per_text or None,
        max_per_doc=args.max_per_doc or None,
        max_per_page=args.max_per_page or None,
        exact_text_only=args.exact_text_only,
        exclude_reference_designators=args.exclude_reference_designators,
    )
    write_jsonl(root / args.output, batch)

    counts = Counter(row.get("category", "unknown") for row in batch)
    print(f"[OK] Wrote {len(batch)} review rows to {args.output}")
    print(f"by_category: {dict(sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
