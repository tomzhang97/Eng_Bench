#!/usr/bin/env python3
"""Render a side-by-side old/new crop contact sheet for VisualDiff review rows."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def render_contact_sheet(
    input_path: Path,
    output_path: Path,
    *,
    row_count: int = 12,
    start: int = 0,
    stride: int = 1,
    pad: int = 90,
    cell: int = 420,
    columns: int = 1,
) -> int:
    rows = read_rows(input_path)
    start = max(0, start)
    stride = max(1, stride)
    columns = max(1, columns)
    rows = rows[start::stride][: max(0, row_count)]
    row_height = cell + 40
    tile_width = cell * 2 + 30
    grid_rows = math.ceil(len(rows) / columns) if rows else 0
    sheet = Image.new(
        "RGB",
        (tile_width * columns, max(40, row_height * grid_rows)),
        "white",
    )
    draw = ImageDraw.Draw(sheet)

    for index, row in enumerate(rows):
        grid_row, grid_column = divmod(index, columns)
        x0 = tile_width * grid_column
        y0 = row_height * grid_row
        for side, (image_key, bbox_key) in enumerate(
            (("image_old", "bbox_old"), ("image_new", "bbox_new"))
        ):
            with Image.open(row[image_key]) as source:
                image = source.convert("RGB")
            x1, y1, x2, y2 = row[bbox_key]
            crop_x1, crop_y1 = max(0, x1 - pad), max(0, y1 - pad)
            crop_x2, crop_y2 = min(image.width, x2 + pad), min(image.height, y2 + pad)
            crop = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            crop_draw = ImageDraw.Draw(crop)
            crop_draw.rectangle(
                [x1 - crop_x1, y1 - crop_y1, x2 - crop_x1, y2 - crop_y1],
                outline=(255, 0, 0),
                width=3,
            )
            crop.thumbnail((cell, cell))
            sheet.paste(crop, (x0 + side * (cell + 30), y0 + 35))
        label = (
            f"#{start + index * stride + 1} {row['pair_id']}  "
            f"[{row.get('change_type', '')}]  "
            f"old='{str(row.get('old_text', ''))[:40]}' "
            f"new='{str(row.get('new_text', ''))[:40]}'"
        )
        draw.text((x0 + 5, y0 + 8), label, fill=(0, 0, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=12)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--pad", type=int, default=90)
    parser.add_argument("--cell", type=int, default=420)
    parser.add_argument("--columns", type=int, default=1)
    args = parser.parse_args(argv)
    rendered = render_contact_sheet(
        args.input,
        args.output,
        row_count=args.rows,
        start=args.start,
        stride=args.stride,
        pad=args.pad,
        cell=args.cell,
        columns=args.columns,
    )
    print(f"saved {args.output} with {rendered} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
