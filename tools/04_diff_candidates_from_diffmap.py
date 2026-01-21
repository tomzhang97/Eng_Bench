#!/usr/bin/env python3
"""
Generate coarse candidate change boxes from diffmaps (pre-annotation helper).
Input:
  derived/align/<pair_id>/diffmap_page_XXX.png
Output:
  derived/align/<pair_id>/candidates_page_XXX.json
Method:
  - Otsu threshold
  - morphological close
  - connected components -> bbox
This is NOT ground truth; it's only to speed up CVAT marking.
"""
import argparse, json
from pathlib import Path
import cv2
import numpy as np

def candidates_from_diff(diff_img, min_area=300):
    # Otsu threshold
    _, bw = cv2.threshold(diff_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Morphology to merge nearby pixels
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5,5))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k, iterations=2)
    # Connected components
    num, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    boxes=[]
    for i in range(1, num):
        x,y,w,h,area = stats[i]
        if area < min_area:
            continue
        boxes.append({"bbox":[int(x),int(y),int(x+w),int(y+h)], "area": int(area)})
    boxes.sort(key=lambda b: b["area"], reverse=True)
    return boxes

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".")
    ap.add_argument("--pair_id", type=str, required=True)
    ap.add_argument("--pages", type=str, default="0-2")
    ap.add_argument("--min_area", type=int, default=300)
    args = ap.parse_args()

    root = Path(args.root)
    d = root / "derived" / "align" / args.pair_id

    # parse pages
    pages=[]
    if "-" in args.pages:
        a,b=args.pages.split("-")
        pages=list(range(int(a), int(b)+1))
    else:
        pages=[int(x) for x in args.pages.split(",") if x.strip()]

    for i in pages:
        p = d / f"diffmap_page_{i:03d}.png"
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(p)
        boxes = candidates_from_diff(img, min_area=args.min_area)
        out = {"page_index": i, "pair_id": args.pair_id, "candidates": boxes}
        (d / f"candidates_page_{i:03d}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[OK] candidates -> {d}")

if __name__ == "__main__":
    main()
