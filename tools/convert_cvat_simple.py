#!/usr/bin/env python3
"""
Simple converter: CVAT COCO export -> Eng_Bench diff_pairs JSONL.

For v1.0→v1.1 annotations where images are v1.1 pages (p0000.png format).
"""
import json
import re
from pathlib import Path


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def coco_bbox_to_xyxy(bbox):
    """COCO [x, y, w, h] -> [x1, y1, x2, y2]"""
    x, y, w, h = bbox
    return [x, y, x + w, y + h]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--coco", required=True, help="COCO instances_default.json")
    ap.add_argument("--pair_id", required=True, help="Pair ID for output")
    ap.add_argument("--out", required=True, help="Output JSONL path")
    args = ap.parse_args()

    coco = load_json(args.coco)
    
    # Build category id -> name mapping
    cat_map = {c["id"]: c["name"] for c in coco["categories"]}
    
    # Build image id -> page mapping
    img_map = {}
    for img in coco["images"]:
        # Parse page number from filename like "images/p0000.png"
        fname = img["file_name"]
        match = re.search(r'p(\d+)\.png', fname)
        if match:
            page = int(match.group(1))
            img_map[img["id"]] = {
                "page": page,
                "width": img["width"],
                "height": img["height"],
            }
    
    # Process annotations
    records = []
    for i, ann in enumerate(coco["annotations"]):
        img_id = ann["image_id"]
        if img_id not in img_map:
            continue
        
        img_info = img_map[img_id]
        page = img_info["page"]
        bbox = coco_bbox_to_xyxy(ann["bbox"])
        label = cat_map.get(ann["category_id"], "UNKNOWN")
        
        # Create change record
        change_id = f"{args.pair_id}_ann{i:04d}"
        
        record = {
            "pair_id": args.pair_id,
            "change_id": change_id,
            "page": page,
            "bbox_xyxy": bbox,
            "label": label,
            "auto_type": label,
            "source": "cvat_manual",
        }
        records.append(record)
    
    # Write JSONL
    with open(args.out, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    
    print(f"[OK] Wrote {len(records)} annotations to {args.out}")


if __name__ == "__main__":
    main()
