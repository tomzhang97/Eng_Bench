#!/usr/bin/env python3
"""Render aligned VisualDiff queue rows for machine semantic prefill."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


SHEET_WIDTH = 1800
ROW_HEIGHT = 390
HEADER_HEIGHT = 54
PANEL_SIZE = (420, 290)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(row)
    return rows


def resolve_path(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else root / path


def parse_bbox(row: dict[str, Any], key: str) -> tuple[int, int, int, int]:
    value = row.get(key)
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{row.get('pair_id', '<unknown>')}: invalid {key}")
    x1, y1, x2, y2 = (int(round(float(item))) for item in value)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"{row.get('pair_id', '<unknown>')}: empty {key}")
    return x1, y1, x2, y2


def crop_bounds(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    *,
    context: bool,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    width = x2 - x1
    height = y2 - y1
    if context:
        crop_width = max(650, min(2600, int(width * 1.8)))
        crop_height = max(430, min(1500, int(height * 1.8)))
    else:
        crop_width = max(180, min(1200, width + 140))
        crop_height = max(140, min(900, height + 140))
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    left = max(0, min(image.width - crop_width, int(round(cx - crop_width / 2))))
    top = max(0, min(image.height - crop_height, int(round(cy - crop_height / 2))))
    right = min(image.width, left + crop_width)
    bottom = min(image.height, top + crop_height)
    left = max(0, right - crop_width)
    top = max(0, bottom - crop_height)
    return left, top, right, bottom


def render_panel(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    *,
    context: bool,
    label: str,
) -> Image.Image:
    bounds = crop_bounds(image, bbox, context=context)
    crop = image.crop(bounds).convert("RGB")
    draw = ImageDraw.Draw(crop)
    x1, y1, x2, y2 = bbox
    left, top, _, _ = bounds
    draw.rectangle(
        (x1 - left, y1 - top, x2 - left, y2 - top),
        outline=(220, 30, 30),
        width=max(3, min(crop.width, crop.height) // 100),
    )
    fitted = ImageOps.contain(crop, PANEL_SIZE, Image.Resampling.LANCZOS)
    panel = Image.new("RGB", PANEL_SIZE, "white")
    panel.paste(
        fitted,
        ((PANEL_SIZE[0] - fitted.width) // 2, (PANEL_SIZE[1] - fitted.height) // 2),
    )
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rectangle((0, 0, PANEL_SIZE[0] - 1, PANEL_SIZE[1] - 1), outline=(150, 150, 150))
    panel_draw.rectangle((0, 0, PANEL_SIZE[0], 24), fill=(245, 245, 245))
    panel_draw.text((8, 5), label, fill="black", font=ImageFont.load_default())
    return panel


def build_sheets(
    root: Path,
    rows: list[dict[str, Any]],
    output_dir: Path,
    rows_per_sheet: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    issues: list[str] = []
    font = ImageFont.load_default()

    for sheet_start in range(0, len(rows), rows_per_sheet):
        sheet_rows = rows[sheet_start : sheet_start + rows_per_sheet]
        sheet_number = sheet_start // rows_per_sheet + 1
        sheet_name = f"sheet_{sheet_number:03d}.png"
        sheet = Image.new(
            "RGB",
            (SHEET_WIDTH, HEADER_HEIGHT + ROW_HEIGHT * len(sheet_rows)),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        draw.text(
            (18, 16),
            f"VisualDiff semantic machine audit | sheet {sheet_number} | rows {sheet_start + 1}-{sheet_start + len(sheet_rows)}",
            fill="black",
            font=font,
        )

        for offset, row in enumerate(sheet_rows):
            index = sheet_start + offset + 1
            y = HEADER_HEIGHT + offset * ROW_HEIGHT
            pair_id = str(row.get("pair_id") or "")
            old_path = resolve_path(root, row.get("image_old"))
            new_path = resolve_path(root, row.get("image_new"))
            record = {
                "index": index,
                "pair_id": pair_id,
                "sheet": sheet_name,
                "description": str(row.get("description") or ""),
                "description_source": str(row.get("description_source") or ""),
                "image_old": old_path.relative_to(root).as_posix() if old_path.is_relative_to(root) else str(old_path),
                "image_new": new_path.relative_to(root).as_posix() if new_path.is_relative_to(root) else str(new_path),
                "bbox_old": row.get("bbox_old"),
                "bbox_new": row.get("bbox_new"),
            }
            manifest.append(record)
            draw.line((0, y, SHEET_WIDTH, y), fill=(205, 205, 205), width=1)
            title = f"#{index} {pair_id}"
            draw.text((18, y + 10), title[:220], fill="black", font=font)
            draw.text(
                (18, y + 30),
                f"current draft: {record['description']}"[:220],
                fill=(70, 70, 70),
                font=font,
            )
            try:
                if not old_path.is_file() or not new_path.is_file():
                    raise FileNotFoundError(f"missing page: {old_path} or {new_path}")
                old_bbox = parse_bbox(row, "bbox_old")
                new_bbox = parse_bbox(row, "bbox_new")
                with Image.open(old_path) as old_image, Image.open(new_path) as new_image:
                    panels = [
                        render_panel(old_image, old_bbox, context=True, label="OLD context"),
                        render_panel(new_image, new_bbox, context=True, label="NEW context"),
                        render_panel(old_image, old_bbox, context=False, label="OLD detail"),
                        render_panel(new_image, new_bbox, context=False, label="NEW detail"),
                    ]
                for panel_index, panel in enumerate(panels):
                    sheet.paste(panel, (18 + panel_index * 440, y + 78))
            except Exception as exc:  # Fail visibly while reporting every row.
                message = f"{pair_id}: {exc}"
                issues.append(message)
                draw.text((18, y + 100), message[:220], fill=(180, 0, 0), font=font)

        sheet.save(output_dir / sheet_name)

    return manifest, issues


def write_outputs(
    output_dir: Path,
    manifest: list[dict[str, Any]],
    issues: list[str],
) -> None:
    manifest_path = output_dir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    sheet_names = sorted({row["sheet"] for row in manifest})
    html_lines = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>VisualDiff semantic machine audit</title>",
        "<style>body{font-family:Arial,sans-serif;margin:24px}img{max-width:100%;border:1px solid #aaa;margin-bottom:24px}</style>",
        "</head><body><h1>VisualDiff semantic machine audit</h1>",
        f"<p>Rows: {len(manifest)}. Issues: {len(issues)}. Evidence only; no review decision is implied.</p>",
    ]
    for name in sheet_names:
        html_lines.append(f"<h2>{html.escape(name)}</h2><img src='{html.escape(name)}'>")
    html_lines.append("</body></html>")
    (output_dir / "index.html").write_text("\n".join(html_lines) + "\n", encoding="utf-8")

    report = {
        "rows": len(manifest),
        "sheets": len(sheet_names),
        "issues": issues,
        "valid": not issues,
        "safe_to_merge_gold": False,
        "interpretation": "Evidence-only machine audit. No row is human-reviewed or safe to merge Gold.",
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--description-source", default="machine_visual_candidate_missing_textlayer")
    parser.add_argument("--rows-per-sheet", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rows_per_sheet < 1:
        raise SystemExit("--rows-per-sheet must be positive")
    root = Path(args.root).resolve()
    input_path = resolve_path(root, args.input)
    output_dir = resolve_path(root, args.output_dir)
    rows = load_jsonl(input_path)
    selected = [
        row for row in rows if str(row.get("description_source") or "") == args.description_source
    ]
    manifest, issues = build_sheets(root, selected, output_dir, args.rows_per_sheet)
    write_outputs(output_dir, manifest, issues)
    print(json.dumps({"rows": len(manifest), "issues": len(issues), "output_dir": str(output_dir)}, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
