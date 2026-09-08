#!/usr/bin/env python3
"""
Convert per-page textlayer JSON files to JSONL format for downstream processing.

Input:  derived/textlayer/<doc_id>/page_000.json, page_001.json, ...
Output: derived/textlayer/<doc_id>.jsonl

Each output line contains: page, text, bbox_px (at specified DPI)
"""
import argparse
import json
from pathlib import Path

import fitz


def rotate_bbox_xyxy(
    bbox: list[float],
    rotation: int,
    page_width: float,
    page_height: float,
) -> list[float]:
    """Map an unrotated PDF bbox into the rendered page orientation."""
    x0, y0, x1, y1 = bbox
    rotation = rotation % 360
    if rotation == 90:
        return [page_height - y1, x0, page_height - y0, x1]
    if rotation == 180:
        return [page_width - x1, page_height - y1, page_width - x0, page_height - y0]
    if rotation == 270:
        return [y0, page_width - x1, y1, page_width - x0]
    return [x0, y0, x1, y1]


def load_page_geometry(pdf_path: Path | None) -> dict[int, dict[str, float | int]]:
    if pdf_path is None:
        return {}
    doc = fitz.open(pdf_path)
    geometry: dict[int, dict[str, float | int]] = {}
    for index, page in enumerate(doc):
        crop = page.cropbox
        geometry[index] = {
            "rotation": page.rotation,
            "width": float(crop.width),
            "height": float(crop.height),
        }
    doc.close()
    return geometry


def convert_textlayer(input_dir: Path, output_path: Path, dpi: int = 300, pdf_path: Path | None = None):
    """Convert per-page JSON files to JSONL format."""
    # Get all page JSON files
    page_files = sorted(input_dir.glob("page_*.json"))
    
    if not page_files:
        raise FileNotFoundError(f"No page_*.json files found in {input_dir}")
    
    pts_to_px = dpi / 72.0
    geometry = load_page_geometry(pdf_path)
    
    records = []
    for page_file in page_files:
        with open(page_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        page_index = data["doc_page"]
        
        for span in data.get("spans", []):
            text = span.get("text", "").strip()
            if not text:
                continue
            
            bbox_pts = span.get("bbox")
            if bbox_pts:
                page_geometry = geometry.get(page_index)
                if page_geometry:
                    oriented_bbox = rotate_bbox_xyxy(
                        bbox_pts,
                        int(page_geometry["rotation"]),
                        float(page_geometry["width"]),
                        float(page_geometry["height"]),
                    )
                else:
                    oriented_bbox = bbox_pts
                bbox_px = [coord * pts_to_px for coord in oriented_bbox]
            else:
                bbox_px = None
            
            record = {
                "page": page_index,
                "text": text,
                "bbox_px": bbox_px,
                "font": span.get("font"),
                "size": span.get("size"),
                "flags": span.get("flags"),
                "color": span.get("color"),
            }
            records.append(record)
    
    # Write JSONL
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    print(f"[OK] Converted {len(page_files)} pages, {len(records)} spans -> {output_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".", help="Eng_Bench root")
    ap.add_argument("--doc_id", type=str, required=True)
    ap.add_argument("--dpi", type=int, default=300, help="DPI for bbox conversion")
    ap.add_argument(
        "--pdf_relpath",
        type=str,
        default=None,
        help="Optional source PDF path used to map rotated text bboxes to rendered pages.",
    )
    args = ap.parse_args()
    
    root = Path(args.root)
    input_dir = root / "derived" / "textlayer" / args.doc_id
    output_path = root / "derived" / "textlayer" / f"{args.doc_id}.jsonl"
    
    pdf_path = root / args.pdf_relpath if args.pdf_relpath else None
    convert_textlayer(input_dir, output_path, dpi=args.dpi, pdf_path=pdf_path)


if __name__ == "__main__":
    main()
