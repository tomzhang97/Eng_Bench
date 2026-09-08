#!/usr/bin/env python3
"""Render a numbered contact sheet for microtext review-pack crops."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def fit_image(image: Image.Image, width: int, height: int) -> Image.Image:
    fitted = image.copy().convert("RGB")
    if fitted.width and fitted.height:
        scale = min(width / fitted.width, height / fitted.height)
        if scale > 1:
            fitted = fitted.resize(
                (max(1, round(fitted.width * scale)), max(1, round(fitted.height * scale))),
                Image.Resampling.LANCZOS,
            )
        else:
            fitted.thumbnail((width, height), Image.Resampling.LANCZOS)
    return fitted


def review_crop(
    root: Path,
    row: dict[str, Any],
    max_image_pixels: int | None = None,
    *,
    authoritative_bbox: bool = False,
) -> Image.Image | None:
    previous_limit = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        return _review_crop(root, {**row, "crop_path": ""} if authoritative_bbox else row)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def _review_crop(root: Path, row: dict[str, Any]) -> Image.Image | None:
    crop_value = str(row.get("crop_path") or "").strip()
    if crop_value:
        crop_path = Path(crop_value)
        if not crop_path.is_absolute():
            crop_path = root / crop_path
        if crop_path.is_file():
            with Image.open(crop_path) as image:
                return image.copy().convert("RGB")

    image_value = str(row.get("image_path") or "").strip()
    bbox = row.get("bbox") or row.get("bbox_px") or []
    if not image_value or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    image_path = Path(image_value)
    if not image_path.is_absolute():
        image_path = root / image_path
    if not image_path.is_file():
        return None
    try:
        coordinates = tuple(int(round(float(value))) for value in bbox)
    except (TypeError, ValueError):
        return None
    if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
        return None
    with Image.open(image_path) as image:
        if (
            coordinates[0] < 0
            or coordinates[1] < 0
            or coordinates[2] > image.width
            or coordinates[3] > image.height
        ):
            return None
        return image.crop(coordinates).convert("RGB")


def review_context(
    root: Path,
    row: dict[str, Any],
    padding: int,
    max_image_pixels: int | None = None,
) -> Image.Image | None:
    previous_limit = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        return _review_context(root, row, max(1, padding))
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def _review_context(root: Path, row: dict[str, Any], padding: int) -> Image.Image | None:
    image_value = str(row.get("image_path") or "").strip()
    bbox = row.get("bbox") or row.get("bbox_px") or []
    if not image_value or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    image_path = Path(image_value)
    if not image_path.is_absolute():
        image_path = root / image_path
    if not image_path.is_file():
        return None
    try:
        x1, y1, x2, y2 = (int(round(float(value))) for value in bbox)
    except (TypeError, ValueError):
        return None
    if x1 >= x2 or y1 >= y2:
        return None

    with Image.open(image_path) as image:
        if x1 < 0 or y1 < 0 or x2 > image.width or y2 > image.height:
            return None
        left = max(0, x1 - padding)
        top = max(0, y1 - padding)
        right = min(image.width, x2 + padding)
        bottom = min(image.height, y2 + padding)
        context = image.crop((left, top, right, bottom)).convert("RGB")
    draw = ImageDraw.Draw(context)
    line_width = max(2, min(context.size) // 100)
    draw.rectangle(
        (x1 - left, y1 - top, x2 - left - 1, y2 - top - 1),
        outline="#d00000",
        width=line_width,
    )
    return context


def render_contact_sheet(
    root: Path,
    rows: list[dict[str, Any]],
    output: Path,
    *,
    start: int,
    limit: int,
    columns: int,
    cell_width: int,
    cell_height: int,
    max_image_pixels: int | None = None,
    context_padding: int = 0,
    authoritative_bbox: bool = False,
) -> dict[str, int]:
    selected = rows[start : start + limit]
    columns = max(1, columns)
    rows_count = max(1, (len(selected) + columns - 1) // columns)
    canvas = Image.new("RGB", (columns * cell_width, rows_count * cell_height), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    missing = 0
    missing_contexts = 0
    image_area_height = cell_height - 50

    for offset, row in enumerate(selected):
        column = offset % columns
        sheet_row = offset // columns
        left = column * cell_width
        top = sheet_row * cell_height
        draw.rectangle((left, top, left + cell_width - 1, top + cell_height - 1), outline="#b8b8b8")
        crop = review_crop(root, row, max_image_pixels=max_image_pixels, authoritative_bbox=authoritative_bbox)
        context = None
        if context_padding > 0:
            context = review_context(
                root,
                row,
                context_padding,
                max_image_pixels=max_image_pixels,
            )
            if context is None:
                missing_contexts += 1
        if crop is not None and context is None:
            fitted = fit_image(crop, cell_width - 12, image_area_height - 8)
            image_left = left + (cell_width - fitted.width) // 2
            image_top = top + 4 + (image_area_height - fitted.height) // 2
            canvas.paste(fitted, (image_left, image_top))
        elif crop is not None and context is not None:
            crop_panel_width = max(80, (cell_width - 18) * 2 // 5)
            context_panel_width = cell_width - crop_panel_width - 18
            fitted_crop = fit_image(crop, crop_panel_width, image_area_height - 12)
            fitted_context = fit_image(context, context_panel_width, image_area_height - 12)
            crop_left = left + 6 + (crop_panel_width - fitted_crop.width) // 2
            context_left = left + 12 + crop_panel_width + (context_panel_width - fitted_context.width) // 2
            crop_top = top + 6 + (image_area_height - 12 - fitted_crop.height) // 2
            context_top = top + 6 + (image_area_height - 12 - fitted_context.height) // 2
            canvas.paste(fitted_crop, (crop_left, crop_top))
            canvas.paste(fitted_context, (context_left, context_top))
        else:
            missing += 1
            draw.text((left + 8, top + 12), "MISSING CROP", fill="#b00020", font=font)
        absolute_index = start + offset + 1
        doc_id = str(row.get("doc_id") or "")[:38]
        category = str(row.get("category") or "")[:20]
        text = str(row.get("proposed_text") or row.get("target_text") or "").replace("\n", " ")[:38]
        draw.text((left + 6, top + image_area_height + 2), f"#{absolute_index} {doc_id}", fill="black", font=font)
        draw.text((left + 6, top + image_area_height + 18), f"{category}: {text}", fill="black", font=font)

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    return {
        "rows": len(selected),
        "missing_crops": missing,
        "missing_contexts": missing_contexts,
        "start": start,
        "end": start + len(selected),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Zero-based input row offset (0 starts with displayed row #1).",
    )
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--cell-width", type=int, default=360)
    parser.add_argument("--cell-height", type=int, default=180)
    parser.add_argument(
        "--authoritative-bbox", action="store_true",
        help="Ignore cached or padded crop_path images; inspect the exact benchmark page bbox.",
    )
    parser.add_argument(
        "--context-padding",
        type=int,
        default=0,
        help="Source-image pixels around each bbox; renders crop plus a red-box context view.",
    )
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        help="Opt-in Pillow safety ceiling for audited large raster sources.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = root / input_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path
    stats = render_contact_sheet(
        root,
        read_jsonl(input_path),
        output_path,
        start=max(0, args.start),
        limit=max(1, args.limit),
        columns=max(1, args.columns),
        cell_width=max(160, args.cell_width),
        cell_height=max(100, args.cell_height),
        max_image_pixels=args.max_image_pixels,
        context_padding=max(0, args.context_padding),
        authoritative_bbox=args.authoritative_bbox,
    )
    print(json.dumps(stats, indent=2))
    return 1 if stats["missing_crops"] or stats["missing_contexts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
