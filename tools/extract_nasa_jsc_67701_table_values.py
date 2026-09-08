#!/usr/bin/env python3
"""Extract review-only numeric table values from NASA JSC-67701 text spans."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


DOC_ID = "nasa_jsc_67701_fabrication_tolerances"
SOURCE_CANDIDATE_ID = "mech_067"
SAME_MODEL_ID = "nasa_jsc_67701_fabrication_tolerances"
SOURCE_URL = "https://standards.nasa.gov/standard/JSC/JSC-67701-DCN-001"
EXPECTED_SOURCE_SHA256 = (
    "be73e0e29d44484ec8b1cdf91f752aee553dd64a8a1cd36fbf07f73f6e0fdc2d"
)
MIN_PAGE_INDEX = 9
MAX_PAGE_INDEX = 42
MIN_CONTENT_Y = 450.0
MAX_CONTENT_Y = 3000.0

DECIMAL_RE = re.compile(r"^(?:\d+\.\d+|\.\d+)$")
PLUS_MINUS_RE = re.compile(r"^±\.?\d+$")
RANGE_RE = re.compile(
    r"^[+\-]?(?:\d+\.\d+|\.\d+)-[+\-]?(?:\d+\.\d+|\.\d+)$"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_token(value: str) -> str:
    token = " ".join(value.strip().split())
    token = token.replace("�", "±").replace("−", "-").replace("–", "-")
    token = re.sub(r"^±\s+", "±", token)
    token = re.sub(r"\s*-\s*", "-", token)
    return token


def classify_token(value: str) -> tuple[str, str] | None:
    token = normalize_token(value)
    if PLUS_MINUS_RE.fullmatch(token):
        return token, "tolerance_value"
    if RANGE_RE.fullmatch(token):
        return token, "tolerance_value"
    if DECIMAL_RE.fullmatch(token):
        return token, "dimension_value"
    return None


def candidate_id(
    version_id: str,
    page_index: int,
    bbox: list[int],
    proposed_text: str,
    category: str,
) -> str:
    payload = json.dumps(
        {
            "doc_id": DOC_ID,
            "version_id": version_id,
            "page_index": page_index,
            "bbox": bbox,
            "proposed_text": proposed_text,
            "category": category,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mtcand__{hashlib.sha256(payload).hexdigest()[:20]}"


def padded_bbox(
    raw_bbox: list[float],
    width: int,
    height: int,
    padding_x: int,
    padding_y: int,
) -> list[int]:
    if len(raw_bbox) != 4:
        raise ValueError(f"bbox_px must contain four values: {raw_bbox!r}")
    x0, y0, x1, y1 = (float(value) for value in raw_bbox)
    bbox = [
        max(0, math.floor(x0) - padding_x),
        max(0, math.floor(y0) - padding_y),
        min(width, math.ceil(x1) + padding_x),
        min(height, math.ceil(y1) + padding_y),
    ]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"degenerate bbox after padding: {bbox!r}")
    return bbox


def build_candidates(
    textlayer_rows: list[dict[str, Any]],
    page_sizes: dict[int, tuple[int, int]],
    *,
    version_id: str,
    padding_x: int = 10,
    padding_y: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, tuple[int, ...], str]] = set()
    held = Counter()

    for source_row in textlayer_rows:
        try:
            page_index = int(source_row.get("page"))
        except (TypeError, ValueError):
            held["invalid_page"] += 1
            continue
        if not MIN_PAGE_INDEX <= page_index <= MAX_PAGE_INDEX:
            held["outside_selected_pages"] += 1
            continue

        raw_bbox = source_row.get("bbox_px")
        if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            held["invalid_bbox"] += 1
            continue
        try:
            y0 = float(raw_bbox[1])
        except (TypeError, ValueError):
            held["invalid_bbox"] += 1
            continue
        if not MIN_CONTENT_Y < y0 < MAX_CONTENT_Y:
            held["outside_engineering_content_band"] += 1
            continue

        classified = classify_token(str(source_row.get("text") or ""))
        if classified is None:
            held["non_atomic_decimal_or_tolerance"] += 1
            continue
        proposed_text, category = classified
        if page_index not in page_sizes:
            raise ValueError(f"missing rendered page size for page {page_index}")
        width, height = page_sizes[page_index]
        bbox = padded_bbox(raw_bbox, width, height, padding_x, padding_y)
        identity = (page_index, tuple(bbox), proposed_text)
        if identity in seen:
            held["duplicate_textlayer_span"] += 1
            continue
        seen.add(identity)

        image_path = (
            f"derived/pages_300dpi/{DOC_ID}/page_{page_index:03d}.png"
        )
        basis = (
            "explicit_plus_minus_or_numeric_range"
            if category == "tolerance_value"
            else "atomic_decimal_in_verified_fabrication_table_or_figure"
        )
        question = (
            "What tolerance value or range is shown in this fabrication region?"
            if category == "tolerance_value"
            else "What dimension value is shown in this fabrication region?"
        )
        candidates.append(
            {
                "candidate_id": candidate_id(
                    version_id, page_index, bbox, proposed_text, category
                ),
                "doc_id": DOC_ID,
                "version_id": version_id,
                "page_index": page_index,
                "bbox": bbox,
                "target_text": "",
                "raw_text": str(source_row.get("text") or "").strip(),
                "proposed_text": proposed_text,
                "corrected_text": "",
                "category": category,
                "question_text": question,
                "image_path": image_path,
                "source": "pdf_textlayer_atomic_fabrication_value",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
                "same_model_id": SAME_MODEL_ID,
                "source_url": SOURCE_URL,
                "source_sha256": EXPECTED_SOURCE_SHA256,
                "source_textlayer_bbox_px": [float(value) for value in raw_bbox],
                "review_status": "needs_review",
                "promotion_state": "unreviewed_candidate",
                "machine_qa_status": "textlayer_pattern_candidate",
                "machine_category_basis": basis,
                "review_bucket": "v2_0_category_balance",
                "split": "provisional_review",
                "safe_to_merge_gold": False,
                "review_notes": (
                    "Exact embedded PDF text-layer span from a visually verified "
                    "fabrication table or figure; human must confirm text and category."
                ),
            }
        )

    candidates.sort(
        key=lambda row: (
            int(row["page_index"]),
            int(row["bbox"][1]),
            int(row["bbox"][0]),
            str(row["candidate_id"]),
        )
    )
    return candidates, dict(sorted(held.items()))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} is not an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def markdown_report(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# NASA JSC-67701 Atomic Table-Value Extraction",
            "",
            "- Goal: Gold v2.0 Global",
            f"- Text-layer rows: `{report['textlayer_rows']}`",
            f"- Review candidates: `{report['review_candidates']}`",
            f"- Categories: `{json.dumps(report['categories'], sort_keys=True)}`",
            f"- Selected pages: `{report['selected_pages']}`",
            f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
            f"- Valid: `{str(report['valid']).lower()}`",
            "",
            "Outputs remain review-only and require evidence-based human validation.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--pages-dir", type=Path, required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--padding-x", type=int, default=10)
    parser.add_argument("--padding-y", type=int, default=6)
    args = parser.parse_args()

    root = args.root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    input_path = resolve(args.input)
    source_pdf = resolve(args.source_pdf)
    pages_dir = resolve(args.pages_dir)
    source_hash = file_sha256(source_pdf)
    if source_hash.lower() != EXPECTED_SOURCE_SHA256.lower():
        raise ValueError(
            f"source SHA-256 mismatch: expected {EXPECTED_SOURCE_SHA256}, got {source_hash}"
        )

    page_sizes: dict[int, tuple[int, int]] = {}
    for page_index in range(MIN_PAGE_INDEX, MAX_PAGE_INDEX + 1):
        page_path = pages_dir / f"page_{page_index:03d}.png"
        if not page_path.is_file():
            raise FileNotFoundError(f"missing rendered page: {page_path}")
        with Image.open(page_path) as image:
            page_sizes[page_index] = image.size

    textlayer_rows = load_jsonl(input_path)
    candidates, held_reasons = build_candidates(
        textlayer_rows,
        page_sizes,
        version_id=args.version_id,
        padding_x=args.padding_x,
        padding_y=args.padding_y,
    )
    if len({row["candidate_id"] for row in candidates}) != len(candidates):
        raise ValueError("candidate ID collision")
    if len(
        {
            (row["doc_id"], row["page_index"], tuple(row["bbox"]))
            for row in candidates
        }
    ) != len(candidates):
        raise ValueError("duplicate physical region in extracted candidates")

    output_path = resolve(args.output_jsonl)
    write_jsonl(output_path, candidates)
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "source_specific_exact_textlayer_atomic_values",
        "doc_id": DOC_ID,
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "source_sha256": source_hash,
        "input": input_path.relative_to(root).as_posix(),
        "input_sha256": file_sha256(input_path),
        "output": output_path.relative_to(root).as_posix(),
        "output_sha256": file_sha256(output_path),
        "textlayer_rows": len(textlayer_rows),
        "review_candidates": len(candidates),
        "selected_pages": sorted({int(row["page_index"]) for row in candidates}),
        "categories": dict(sorted(Counter(row["category"] for row in candidates).items())),
        "held_reasons": held_reasons,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "issues": [],
        "valid": bool(candidates),
    }
    report_json = resolve(args.report_json)
    report_md = resolve(args.report_md)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
