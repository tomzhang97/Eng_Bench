#!/usr/bin/env python3
"""
mock_cvat_export_from_seeds.py

Converts diff_pairs.seed.jsonl into a "Mock" COCO export.
This simulates a user who imported seeds into CVAT and exported them without changes.
Allows testing Script 07 and creating a "Silver" dataset.
"""
import json
import argparse
from pathlib import Path

def parse_pair_id(pair_id):
    # pair_id: vdiff__bbb__docRevC_pcbB6__to__docRevC3_pcbRevC
    # output: doc_id, v_old, v_new
    if "__to__" not in pair_id:
        # Fallback/Hack for simpler IDs or if format differs
        return "doc", "vA", "vB"
    
    parts = pair_id.split("__to__")
    left = parts[0] # vdiff__bbb__docRevC_pcbB6
    right = parts[1] # docRevC3_pcbRevC
    
    # Left: vdiff__<doc>__<ver>
    # This is tricky because doc id might have underscores.
    # convention: vdiff__<doc_id>__<version_id>
    # But earlier we saw: vdiff__viola__pcbV1.0  __to__  pcbV1.1
    
    # Let's rely on the split logic being consistent with the filename expectation
    # Filename expectation: vdiff__<doc_id>__<ver_old>__to__<ver_new>__<role>_pXXXX.png
    
    # We just need to construct the filename correctly for Script 07 parsing.
    # The file name is ONLY used to parse back the info.
    # So we can construct a filename that contains the pair_id info.
    
    return pair_id

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-path", required=True)
    ap.add_argument("--out-coco", required=True)
    args = ap.parse_args()

    seeds = []
    with open(args.seed_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                seeds.append(json.loads(line))
    
    images = []
    annotations = []
    categories = [
        {"id": 1, "name": "TEXT_EDIT"},
        {"id": 2, "name": "SYMBOL_CHANGE"},
        {"id": 3, "name": "UNKNOWN"}
    ]
    
    img_id_counter = 1
    ann_id_counter = 1
    
    # We need to track created images to avoid duplicates
    # Key: (pair_id, page, role) -> image_id
    image_map = {}

    for i, seed in enumerate(seeds):
        pair_id = seed["pair_id"]
        page = seed["page"]
        bbox = seed.get("bbox_xyxy", seed.get("bbox", [0,0,10,10]))
        
        # Determine change type/label
        auto_type = seed.get("auto_type", "UNKNOWN")
        cat_id = 1 if "TEXT" in auto_type else 3
        if "SYMBOL" in auto_type: cat_id = 2
        
        change_id = f"change_seed_{i:04d}"
        
        # Ensure images exist for this page
        # We need "old" and "new" images
        for role in ["old", "new"]:
            key = (pair_id, page, role)
            if key not in image_map:
                # Construct filename
                # pair_id typically: vdiff__doc__vA__to__vB
                fname = f"{pair_id}__{role}_p{page:04d}.png"
                
                # We need to extract doc_id, v_old, v_new from pair_id for the record metadata?
                # Actually Script 07 parses the FILENAME.
                # Script 07 expects: vdiff__doc__vOld__to__vNew__role_pXXX
                # So we must ensure pair_id matches that structure.
                
                img_obj = {
                    "id": img_id_counter,
                    "file_name": fname,
                    "width": 2000, # Mock
                    "height": 2000 # Mock
                }
                images.append(img_obj)
                image_map[key] = img_id_counter
                img_id_counter += 1
            
        img_old_id = image_map[(pair_id, page, "old")]
        img_new_id = image_map[(pair_id, page, "new")]
        
        # Create Annotations (Two per seed)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        coco_bbox = [bbox[0], bbox[1], w, h]
        
        # Attributes
        attrs = {
            "change_id": change_id,
            "change_type": "text" if cat_id==1 else "unknown",
            "is_titleblock": False,
            "change_desc_gt": seed.get("text_delta", "Auto-seed change")
        }
        
        # Old Box
        ann_old = {
            "id": ann_id_counter,
            "image_id": img_old_id,
            "category_id": cat_id,
            "bbox": coco_bbox,
            "attributes": attrs,
            "iscrowd": 0,
            "area": w*h
        }
        annotations.append(ann_old)
        ann_id_counter += 1
        
        # New Box (Same bbox for seed)
        ann_new = {
            "id": ann_id_counter,
            "image_id": img_new_id,
            "category_id": cat_id,
            "bbox": coco_bbox,
            "attributes": attrs,
            "iscrowd": 0,
            "area": w*h
        }
        annotations.append(ann_new)
        ann_id_counter += 1

    coco_out = {
        "images": images,
        "annotations": annotations,
        "categories": categories
    }
    
    with open(args.out_coco, 'w', encoding='utf-8') as f:
        json.dump(coco_out, f, ensure_ascii=False, indent=2)
    
    print(f"Generated Mock COCO with {len(images)} images and {len(annotations)} annotations")

if __name__ == "__main__":
    main()
