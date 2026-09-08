#!/usr/bin/env python3
"""Build compact visual contact sheets for a machine-certification calibration pack."""
from __future__ import annotations

import argparse
import csv
import html
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def fit_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    image = image.convert("RGB")
    scale = min(width / max(1, image.width), height / max(1, image.height))
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


def render_tile(row: dict[str, str], *, width: int, height: int) -> Image.Image:
    tile = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(tile)
    heading_font = load_font(22, bold=True)
    body_font = load_font(18)
    crop_path = Path(row["_pack_dir"]) / row["crop_path"]
    with Image.open(crop_path) as source:
        crop = fit_crop(source, width - 20, height - 88)
    tile.paste(crop, (10, 42))
    index = row.get("sample_index", "")
    category = row.get("category", "")
    proposed = row.get("proposed_text", "")
    ocr_text = row.get("ocr_text", "")
    draw.text((10, 8), f"#{index}  {category}", fill="black", font=heading_font)
    footer = f"Expected: {proposed}"
    if ocr_text and ocr_text != proposed:
        footer += f" | OCR: {ocr_text}"
    draw.rectangle((0, height - 40, width, height), fill=(242, 246, 248))
    draw.text((10, height - 34), footer[:54], fill="black", font=body_font)
    return ImageOps.expand(tile, border=1, fill=(170, 170, 170))


def build_contact_sheets(
    pack_dir: Path,
    *,
    checklist: Path,
    output_dir: Path,
    columns: int,
    rows_per_sheet: int,
) -> list[Path]:
    with checklist.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if columns <= 0 or rows_per_sheet <= 0 or rows_per_sheet % columns:
        raise ValueError("rows_per_sheet must be positive and divisible by columns")
    output_dir.mkdir(parents=True, exist_ok=True)
    tile_width, tile_height = 420, 240
    sheet_rows = rows_per_sheet // columns
    paths: list[Path] = []
    for sheet_index in range(math.ceil(len(rows) / rows_per_sheet)):
        page_rows = rows[sheet_index * rows_per_sheet : (sheet_index + 1) * rows_per_sheet]
        sheet = Image.new("RGB", (columns * (tile_width + 2), sheet_rows * (tile_height + 2)), "white")
        for offset, row in enumerate(page_rows):
            row["_pack_dir"] = str(pack_dir)
            tile = render_tile(row, width=tile_width, height=tile_height)
            x = (offset % columns) * tile.width
            y = (offset // columns) * tile.height
            sheet.paste(tile, (x, y))
        path = output_dir / f"contact_sheet_{sheet_index + 1:02d}.png"
        sheet.save(path, optimize=True)
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--checklist", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--rows-per-sheet", type=int, default=20)
    args = parser.parse_args()

    pack_dir = args.pack_dir.resolve()
    checklist = (args.checklist or (pack_dir / "machine_certification_calibration_checklist.csv")).resolve()
    output_dir = (args.output_dir or (pack_dir / "contact_sheets")).resolve()
    paths = build_contact_sheets(
        pack_dir,
        checklist=checklist,
        output_dir=output_dir,
        columns=args.columns,
        rows_per_sheet=args.rows_per_sheet,
    )
    links = "\n".join(
        f"<li><a href='{html.escape(path.name)}'>{html.escape(path.name)}</a></li>" for path in paths
    )
    (output_dir / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Calibration Contact Sheets</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}li{margin:8px 0}</style>"
        f"<h1>Calibration Contact Sheets</h1><p>{len(paths)} sheets</p><ol>{links}</ol>",
        encoding="utf-8",
    )
    print(f"rows={sum(1 for _ in csv.DictReader(checklist.open('r', encoding='utf-8-sig', newline='')))}")
    print(f"sheets={len(paths)}")
    print(f"output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
