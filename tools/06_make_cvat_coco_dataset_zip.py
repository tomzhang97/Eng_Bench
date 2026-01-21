#!/usr/bin/env python3
"""
06_make_cvat_coco_dataset_zip.py

Create a CVAT-importable COCO dataset zip from Eng-Bench seed JSONL.
This is an *annotation acceleration* step only.

Zip layout (COCO 1.0 style):
  images/ p0000.png ...
  annotations/ instances_default.json

You can import this zip into CVAT via:
  Create task -> "Import dataset" -> Format: COCO 1.0

After human edits, export from CVAT as COCO and convert back to Eng-Bench JSONL
with tools/07_cvat_coco_to_engbench_jsonl.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict
from typing import Dict, List, Any, Tuple

from PIL import Image

CAT_ORDER = ["TEXT_EDIT", "ADD_TEXT", "REMOVE_TEXT", "SYMBOL_CHANGE", "UNKNOWN"]

def parse_page_from_filename(fn: str) -> int:
    m = re.search(r"p(\d{4})\.png$", fn)
    if not m:
        raise ValueError(f"cannot parse page from filename: {fn}")
    return int(m.group(1))

def load_seed_jsonl(path: str, pair_id: str) -> Dict[int, List[dict]]:
    per_page = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("pair_id") != pair_id:
                continue
            per_page[int(r["page"])].append(r)
    return per_page

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Eng_Bench root")
    ap.add_argument("--pair_id", required=True)
    ap.add_argument("--base_doc_id", required=True, help="doc id whose rendered pages are used as the annotation canvas")
    ap.add_argument("--seed_jsonl", default="visualdiff/annotations/diff_pairs.seed.jsonl")
    ap.add_argument("--pages", default="all", help="e.g., all or 0-10 or 0,1,2")
    ap.add_argument("--topk_per_page", type=int, default=40)
    ap.add_argument("--out_zip", required=True)
    args = ap.parse_args()

    seed_path = os.path.join(args.root, args.seed_jsonl)
    per_page = load_seed_jsonl(seed_path, args.pair_id)

    # page selection
    if args.pages == "all":
        pages = sorted(per_page.keys())
    elif "-" in args.pages:
        a, b = args.pages.split("-", 1)
        pages = list(range(int(a), int(b) + 1))
    else:
        pages = [int(x) for x in args.pages.split(",") if x.strip()]

    tmp = tempfile.mkdtemp(prefix="cvat_coco_")
    img_dir = os.path.join(tmp, "images")
    ann_dir = os.path.join(tmp, "annotations")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(ann_dir, exist_ok=True)

    images = []
    annotations = []
    categories = [{"id": i+1, "name": name, "supercategory": "change"} for i, name in enumerate(CAT_ORDER)]
    cat2id = {c["name"]: c["id"] for c in categories}

    ann_id = 1
    img_id = 1

    for p in pages:
        # Try both naming conventions
        base_dir = os.path.join(args.root, "derived", "pages_300dpi", args.base_doc_id)
        src_img = None
        for pattern in [f"p{p:04d}.png", f"page_{p:03d}.png", f"page_{p:04d}.png"]:
            candidate = os.path.join(base_dir, pattern)
            if os.path.exists(candidate):
                src_img = candidate
                break
        if not src_img:
            continue
        dst_img = os.path.join(img_dir, f"p{p:04d}.png")
        shutil.copy2(src_img, dst_img)

        w, h = Image.open(dst_img).size
        images.append({"id": img_id, "file_name": f"p{p:04d}.png", "width": w, "height": h})

        # select boxes
        recs = per_page.get(p, [])
        # sort by area desc
        recs.sort(key=lambda r: (r["bbox_xyxy"][2]-r["bbox_xyxy"][0])*(r["bbox_xyxy"][3]-r["bbox_xyxy"][1]), reverse=True)
        recs = recs[: args.topk_per_page]

        for r in recs:
            x1, y1, x2, y2 = r["bbox_xyxy"]
            wbb = max(1, x2 - x1)
            hbb = max(1, y2 - y1)
            cat = r.get("auto_type") or "UNKNOWN"
            cat = cat if cat in cat2id else "UNKNOWN"
            annotations.append({
                "id": ann_id,
                "image_id": img_id,
                "category_id": cat2id[cat],
                "bbox": [x1, y1, wbb, hbb],
                "area": float(wbb * hbb),
                "iscrowd": 0
            })
            ann_id += 1

        img_id += 1

    coco = {"images": images, "annotations": annotations, "categories": categories}
    ann_path = os.path.join(ann_dir, "instances_default.json")
    with open(ann_path, "w", encoding="utf-8") as f:
        json.dump(coco, f, ensure_ascii=False)

    # zip it
    out_zip = args.out_zip
    os.makedirs(os.path.dirname(out_zip) or ".", exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for folder, _, files in os.walk(tmp):
            for fn in files:
                full = os.path.join(folder, fn)
                rel = os.path.relpath(full, tmp)
                z.write(full, rel)

    shutil.rmtree(tmp)
    print(f"Wrote CVAT COCO dataset zip: {out_zip}")

if __name__ == "__main__":
    main()
