#!/usr/bin/env python3
"""Extract approximate text spans from SVG diagrams into Eng_Bench textlayer JSONL.

This is intentionally conservative: it only estimates text bboxes from SVG text
nodes and a rendered reference image. The rows are meant for review staging,
not as a substitute for crop review.
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import fitz
from PIL import Image


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
SCALE_RE = re.compile(rf"scale\(({NUMBER})(?:[, ]+({NUMBER}))?\)")
FONT_SIZE_RE = re.compile(r"font-size:([0-9.]+)px")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_float(value: str | None, default: float = 0.0) -> float:
    if not value:
        return default
    match = re.search(NUMBER, value)
    return float(match.group(0)) if match else default


def parse_size(value: str | None, default: float = 0.0) -> float:
    return parse_float(value, default)


def parse_scale(transform: str | None) -> tuple[float, float]:
    if not transform:
        return 1.0, 1.0
    match = SCALE_RE.search(transform)
    if not match:
        return 1.0, 1.0
    sx = float(match.group(1))
    sy = float(match.group(2) or match.group(1))
    return sx, sy


def font_size(style: str | None) -> float:
    if not style:
        return 12.0
    match = FONT_SIZE_RE.search(style)
    return float(match.group(1)) if match else 12.0


def text_anchor(style: str | None) -> str:
    if style and "text-anchor:middle" in style:
        return "middle"
    if style and "text-anchor:end" in style:
        return "end"
    return "start"


def viewbox_size(root: ET.Element) -> tuple[float, float]:
    viewbox = root.get("viewBox")
    if viewbox:
        parts = [float(part) for part in re.findall(NUMBER, viewbox)]
        if len(parts) == 4:
            return parts[2], parts[3]
    return parse_size(root.get("width"), 1.0), parse_size(root.get("height"), 1.0)


def element_text(node: ET.Element) -> str:
    text = "".join(node.itertext())
    return " ".join(text.split())


def element_xy(node: ET.Element) -> tuple[float, float]:
    x = node.get("x")
    y = node.get("y")
    if x is None or y is None:
        for child in node.iter():
            if local_name(child.tag) == "tspan" and child.get("x") and child.get("y"):
                x = x or child.get("x")
                y = y or child.get("y")
                break
    return parse_float(x), parse_float(y)


def clip_bbox(bbox: list[float], width: int, height: int) -> list[int]:
    x0, y0, x1, y1 = bbox
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(1, min(width, x1))
    y1 = max(1, min(height, y1))
    if x1 <= x0:
        x1 = min(width, x0 + 1)
    if y1 <= y0:
        y1 = min(height, y0 + 1)
    return [int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))]


def extract_rows(
    svg_path: Path,
    image_path: Path,
    page_index: int = 0,
) -> list[dict[str, Any]]:
    with Image.open(image_path) as image:
        image_w, image_h = image.size

    document = fitz.open(stream=svg_path.read_bytes(), filetype="svg")
    try:
        page = document[0]
        scale_x = image_w / page.rect.width
        scale_y = image_h / page.rect.height
        rows: list[dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = str(span.get("text") or "").strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    rows.append(
                        {
                            "page": page_index,
                            "text": text,
                            "bbox_px": clip_bbox(
                                [
                                    x0 * scale_x,
                                    y0 * scale_y,
                                    x1 * scale_x,
                                    y1 * scale_y,
                                ],
                                image_w,
                                image_h,
                            ),
                            "image_width_px": image_w,
                            "image_height_px": image_h,
                            "bbox_coordinate_space": "rendered_image_px",
                            "font": span.get("font", "svg"),
                            "size": span.get("size", 0),
                            "flags": span.get("flags", 0),
                            "color": span.get("color", 0),
                        }
                    )
        return rows
    finally:
        document.close()


def extract_directory_rows(svg_dir: Path, image_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for svg_path in sorted(svg_dir.glob("page_*.svg")):
        page_token = svg_path.stem.removeprefix("page_")
        if not page_token.isdigit():
            continue
        image_path = image_dir / f"{svg_path.stem}.png"
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        rows.extend(extract_rows(svg_path, image_path, page_index=int(page_token)))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract approximate SVG textlayer rows")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--doc-id", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--svg", help="Relative path to one source SVG")
    source.add_argument("--svg-dir", help="Relative path to a directory of page_*.svg files")
    parser.add_argument("--image", help="Relative path to rendered page PNG")
    parser.add_argument("--image-dir", help="Relative path to matching page_*.png files")
    parser.add_argument("--page-index", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.root)
    if args.svg:
        if not args.image:
            parser.error("--image is required with --svg")
        rows = extract_rows(root / args.svg, root / args.image, page_index=args.page_index)
    else:
        if not args.image_dir:
            parser.error("--image-dir is required with --svg-dir")
        rows = extract_directory_rows(root / args.svg_dir, root / args.image_dir)
    out = root / "derived" / "textlayer" / f"{args.doc_id}.jsonl"
    write_jsonl(out, rows)
    print(f"[OK] Wrote {len(rows)} SVG text spans -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
