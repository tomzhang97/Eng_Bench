#!/usr/bin/env python3
"""
filter_invalid_bboxes.py

Filters out items with invalid bboxes (out of bounds) from an Eng_Bench JSONL.
"""
import argparse
import json
import os
from pathlib import Path
from typing import Optional, Tuple, List

def load_jsonl(path: str) -> List[dict]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items

def get_page_dims(root: str, doc_id: str, page: int) -> Optional[Tuple[int, int]]:
    from PIL import Image
    base = Path(root) / "derived" / "pages_300dpi" / doc_id
    candidates = [f"p{page:04d}.png", f"page_{page:03d}.png", f"page_{page:04d}.png"]
    for pat in candidates:
        p = base / pat
        if p.exists():
            return Image.open(p).size
    return None

def is_bbox_valid(bbox: List[float], dims: Tuple[int, int]) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    x1, y1, x2, y2 = bbox
    w, h = dims
    if x2 <= x1 or y2 <= y1:
        return False
    # allow small float forgiveness or strictly enforce? Strictly enforce for now
    if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        return False
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--manifest", default="manifest.jsonl")
    args = ap.parse_args()

    manifest_path = os.path.join(args.root, args.manifest)
    manifest = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("type") == "pair":
                    manifest[r.get("pair_id")] = r

    items = load_jsonl(args.input)
    clean_items = []
    removed = 0

    from PIL import Image

    for it in items:
        valid = True
        pair_id = it.get("pair_id")
        pair = manifest.get(pair_id)
        
        # Check answer bbox
        ans = it.get("answer", {})
        if isinstance(ans, dict) and ans.get("bbox"):
            page = ans.get("page")
            if pair and page is not None:
                doc_id = pair.get("to_doc_id")
                dims = get_page_dims(args.root, doc_id, int(page))
                if dims and not is_bbox_valid(ans["bbox"], dims):
                    valid = False
        
        # Check evidence bboxes
        if valid:
            for ev in it.get("evidence", []):
                if ev.get("bbox"):
                    page = ev.get("page")
                    doc_side = ev.get("doc_side") # A or B
                    if pair and page is not None and doc_side:
                        doc_id = pair.get("from_doc_id" if doc_side == "A" else "to_doc_id")
                        dims = get_page_dims(args.root, doc_id, int(page))
                        if dims and not is_bbox_valid(ev["bbox"], dims):
                            valid = False
                            break
        
        if valid:
            clean_items.append(it)
        else:
            removed += 1

    with open(args.output, "w", encoding="utf-8") as f:
        for it in clean_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    print(f"[OK] Filtered {len(items)} -> {len(clean_items)} items.")
    print(f"     Removed {removed} invalid items.")

if __name__ == "__main__":
    main()
