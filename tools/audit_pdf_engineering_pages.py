#!/usr/bin/env python3
"""Rank engineering-heavy PDF pages and emit contact sheets for visual QA."""
from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw


ENGINEERING_TERMS = re.compile(
    r"(?i)\b(figure|detail|diagram|schematic|profile|plan view|installation|"
    r"layout|chart|table|section|elevation|valve|pump|tank|pipeline)\b"
)


def analyze_pdf(pdf_path: Path, min_score: int = 5) -> dict[str, Any]:
    document = fitz.open(pdf_path)
    pages: list[dict[str, Any]] = []
    try:
        for page_index, page in enumerate(document):
            text = page.get_text("text")
            drawing_count = len(page.get_drawings())
            image_count = len(page.get_images(full=True))
            reasons: list[str] = []
            score = 0
            if ENGINEERING_TERMS.search(text):
                score += 4
                reasons.append("engineering_term")
            if len(text) < 700 and (drawing_count or image_count):
                score += 3
                reasons.append("sparse_text_with_graphics")
            if image_count >= 2:
                score += 2
                reasons.append("multiple_images")
            if drawing_count >= 20:
                score += 2
                reasons.append("dense_vector_geometry")
            pages.append(
                {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "text_characters": len(text),
                    "drawing_count": drawing_count,
                    "image_count": image_count,
                    "score": score,
                    "selected": score >= min_score,
                    "reasons": reasons,
                }
            )
    finally:
        document.close()

    selected = [page for page in pages if page["selected"]]
    return {
        "pdf_path": pdf_path.as_posix(),
        "page_count": len(pages),
        "text_pages": sum(page["text_characters"] > 0 for page in pages),
        "vector_pages": sum(page["drawing_count"] > 0 for page in pages),
        "image_pages": sum(page["image_count"] > 0 for page in pages),
        "min_score": min_score,
        "selected_page_count": len(selected),
        "selected_page_numbers": [page["page_number"] for page in selected],
        "pages": pages,
        "safe_to_merge_gold": False,
        "valid": bool(pages) and bool(selected),
    }


def render_contact_sheets(
    pdf_path: Path,
    page_numbers: list[int],
    output_dir: Path,
    *,
    columns: int = 5,
    rows: int = 6,
    cell_width: int = 300,
    cell_height: int = 230,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(pdf_path)
    outputs: list[Path] = []
    pages_per_sheet = columns * rows
    try:
        for sheet_index, start in enumerate(
            range(0, len(page_numbers), pages_per_sheet), start=1
        ):
            subset = page_numbers[start : start + pages_per_sheet]
            sheet = Image.new(
                "RGB", (columns * cell_width, rows * cell_height), "white"
            )
            draw = ImageDraw.Draw(sheet)
            for position, page_number in enumerate(subset):
                page = document[page_number - 1]
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(0.75, 0.75),
                    colorspace=fitz.csRGB,
                    alpha=False,
                )
                thumbnail = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
                thumbnail.thumbnail(
                    (cell_width - 10, cell_height - 28), Image.Resampling.LANCZOS
                )
                column = position % columns
                row = position // columns
                x = column * cell_width + (cell_width - thumbnail.width) // 2
                y = row * cell_height + 24
                sheet.paste(thumbnail, (x, y))
                draw.text(
                    (column * cell_width + 5, row * cell_height + 5),
                    f"PDF page {page_number}",
                    fill="black",
                )
            output_path = output_dir / f"contact_sheet_{sheet_index:02d}.jpg"
            sheet.save(output_path, quality=90)
            outputs.append(output_path)
    finally:
        document.close()
    return outputs


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--contact-sheet-dir", type=Path)
    parser.add_argument("--min-score", type=int, default=5)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    pdf_path = args.pdf if args.pdf.is_absolute() else root / args.pdf
    output_json = (
        args.output_json if args.output_json.is_absolute() else root / args.output_json
    )
    report = analyze_pdf(pdf_path, min_score=args.min_score)
    if args.contact_sheet_dir:
        contact_dir = (
            args.contact_sheet_dir
            if args.contact_sheet_dir.is_absolute()
            else root / args.contact_sheet_dir
        )
        outputs = render_contact_sheets(
            pdf_path, report["selected_page_numbers"], contact_dir
        )
        report["contact_sheets"] = [
            output.relative_to(root).as_posix() for output in outputs
        ]
    write_json(output_json, report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
