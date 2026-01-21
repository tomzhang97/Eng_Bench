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


def convert_textlayer(input_dir: Path, output_path: Path, dpi: int = 300):
    """Convert per-page JSON files to JSONL format."""
    # Get all page JSON files
    page_files = sorted(input_dir.glob("page_*.json"))
    
    if not page_files:
        raise FileNotFoundError(f"No page_*.json files found in {input_dir}")
    
    # PDF points to pixels conversion factor
    pts_to_px = dpi / 72.0
    
    records = []
    for page_file in page_files:
        with open(page_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        page_index = data["doc_page"]
        
        for span in data.get("spans", []):
            text = span.get("text", "").strip()
            if not text:
                continue
            
            # Convert bbox from PDF points to pixels
            bbox_pts = span.get("bbox")
            if bbox_pts:
                bbox_px = [coord * pts_to_px for coord in bbox_pts]
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
    args = ap.parse_args()
    
    root = Path(args.root)
    input_dir = root / "derived" / "textlayer" / args.doc_id
    output_path = root / "derived" / "textlayer" / f"{args.doc_id}.jsonl"
    
    convert_textlayer(input_dir, output_path, dpi=args.dpi)


if __name__ == "__main__":
    main()
