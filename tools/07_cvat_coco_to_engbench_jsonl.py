#!/usr/bin/env python3
"""
07_cvat_coco_to_engbench_jsonl.py (v2)

Convert CVAT COCO exports to canonical Eng_Bench JSONL format.

Outputs:
  - visualdiff_pairs.jsonl: Ground-truth change pairs
  - visualdiff_questions.jsonl: Q/A layer over pairs

Schema follows Eng_Bench specification for publication-ready datasets.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional


def load_json(path: str) -> dict:
    """Load JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_jsonl(path: str) -> List[dict]:
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def load_split_ids(path: Optional[str]) -> set:
    """Load split file containing pair_ids (one per line)."""
    if path is None:
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}


def parse_file_name(fname: str) -> dict:
    """
    Parse CVAT image filename to extract metadata.
    
    Example: vdiff__viola__pcbV1.0__to__pcbV1.1__old_p0002.png
    
    Returns dict with:
      - pair_prefix: "vdiff__viola__pcbV1.0__to__pcbV1.1"
      - doc_id: "viola"
      - version_old: "pcbV1.0"
      - version_new: "pcbV1.1"
      - rev_role: "old" or "new"
      - page_index: 2 (0-based)
    """
    stem = Path(fname).stem
    parts = stem.split("__")
    
    # Expected: ["vdiff", "doc_id", "version_old", "to", "version_new", "role_pXXXX"]
    assert parts[0] == "vdiff", f"Expected 'vdiff' prefix, got: {parts[0]}"
    assert parts[3] == "to", f"Expected 'to' separator, got: {parts[3]}"
    
    doc_id = parts[1]
    version_old = parts[2]
    version_new = parts[4]
    tail = parts[5]  # e.g., "old_p0002"
    
    # Split role and page
    rev_role, page_str = tail.split("_p")
    page_index = int(page_str)
    
    pair_prefix = "__".join(parts[0:5])
    
    return {
        "pair_prefix": pair_prefix,
        "doc_id": doc_id,
        "version_old": version_old,
        "version_new": version_new,
        "rev_role": rev_role,
        "page_index": page_index
    }


def build_pair_id(prefix: str, local_idx: int) -> str:
    """Build canonical pair_id."""
    return f"{prefix}__{local_idx:04d}"


def coco_bbox_to_xyxy(bbox: List[float]) -> List[float]:
    """Convert COCO bbox [x, y, w, h] to [x_min, y_min, x_max, y_max]."""
    x, y, w, h = bbox
    return [x, y, x + w, y + h]


def map_change_type(label: str, attrs: dict) -> List[str]:
    """
    Map CVAT label and attributes to Eng_Bench change_type list.
    
    Args:
        label: CVAT category name (TEXT_EDIT, SYMBOL_CHANGE, etc.)
        attrs: Annotation attributes dict
    
    Returns:
        List of change types (e.g., ["text", "symbol"])
    """
    types = []
    
    # Map from CVAT labels
    if label in ["TEXT_EDIT", "ADD_TEXT", "REMOVE_TEXT"]:
        types.append("text")
    if label == "SYMBOL_CHANGE":
        types.append("symbol")
    
    # Map from attributes
    attr_change_type = attrs.get("change_type", "")
    if attr_change_type == "wiring":
        types.append("wiring")
    elif attr_change_type == "table":
        types.append("table")
    elif attr_change_type == "titleblock":
        types.append("titleblock")
    
    # Handle titleblock flag
    if attrs.get("is_titleblock") in [True, "true", "True"]:
        if "titleblock" not in types:
            types.append("titleblock")
    
    return sorted(set(types)) if types else ["unknown"]


def make_visualdiff_question(pair: dict) -> dict:
    """
    Generate Q/A from a visualdiff pair using templates.
    
    Args:
        pair: visualdiff_pairs record
    
    Returns:
        visualdiff_questions record
    """
    doc = pair["doc_id"]
    v_old = pair["version_id_old"]
    v_new = pair["version_id_new"]
    ent = pair.get("entity_id") or "this component"
    ctype = pair.get("change_type", [])
    
    base_prefix = f"between {v_old} and {v_new}"
    
    # Template selection based on change type
    if "wiring" in ctype:
        query = f"How did the wiring for {ent} change {base_prefix}?"
    elif "text" in ctype or "table" in ctype:
        query = f"How did the specification or value for {ent} change {base_prefix}?"
    elif "symbol" in ctype:
        query = f"What changed about the symbol or representation of {ent} {base_prefix}?"
    else:
        query = f"What changed about {ent} {base_prefix}?"
    
    # Answer from gold description
    answer = pair["change_desc_gt"]
    
    return {
        "question_id": f"q_{pair['pair_id']}",
        "pair_id": pair["pair_id"],
        "doc_id": doc,
        "version_id_old": v_old,
        "version_id_new": v_new,
        "query_text": query,
        "answer_text": answer,
        "answer_type": "diff_description",
        "requires_connectivity": "wiring" in ctype,
        "requires_position": "layout" in ctype or "position" in ctype,
        "split": pair["split"]
    }


def main():
    ap = argparse.ArgumentParser(
        description="Convert CVAT COCO exports to Eng_Bench JSONL format"
    )
    ap.add_argument("--coco-path", required=True, help="Path to COCO JSON export from CVAT")
    ap.add_argument("--seed-path", required=False, help="Optional: seed JSONL for mapping")
    ap.add_argument("--split-file", required=False, help="Split file (pair_ids, one per line)")
    ap.add_argument("--out-pairs", required=True, help="Output path for visualdiff_pairs.jsonl")
    ap.add_argument("--out-questions", required=True, help="Output path for visualdiff_questions.jsonl")
    args = ap.parse_args()
    
    # Load inputs
    coco = load_json(args.coco_path)
    seeds = load_jsonl(args.seed_path) if args.seed_path else []
    split_ids = load_split_ids(args.split_file)
    
    # Build seed lookup by (pair_id, page, bbox) for ECO enrichment
    seed_lookup = {}
    for seed in seeds:
        key = (seed.get("pair_id"), seed.get("page"), tuple(seed.get("bbox_xyxy", [])))
        seed_lookup[key] = seed
    
    # Build image metadata map
    image_meta = {}
    for img in coco["images"]:
        parsed = parse_file_name(img["file_name"])
        image_meta[img["id"]] = {**parsed, "file_name": img["file_name"]}
    
    # Build category map
    cat_map = {c["id"]: c["name"] for c in coco["categories"]}
    
    # Group annotations by (pair_prefix, change_id)
    # Use change_id attribute if available, otherwise use annotation ID
    grouped = {}
    for ann in coco["annotations"]:
        img_info = image_meta[ann["image_id"]]
        pair_prefix = img_info["pair_prefix"]
        
        # Try to get change_id from attributes
        attrs = ann.get("attributes", {})
        change_id = attrs.get("change_id")
        
        if change_id is None:
            # Fallback: use annotation ID
            change_id = f"ann_{ann['id']}"
        
        key = (pair_prefix, change_id)
        grouped.setdefault(key, []).append((ann, img_info))
    
    pairs_out = []
    questions_out = []
    
    # Process each logical change
    for (pair_prefix, change_id), ann_list in grouped.items():
        # Expect 2 annotations: one old, one new
        ann_old_data = next(((a, img) for a, img in ann_list if img["rev_role"] == "old"), None)
        ann_new_data = next(((a, img) for a, img in ann_list if img["rev_role"] == "new"), None)
        
        if ann_old_data is None or ann_new_data is None:
            # Skip incomplete pairs
            continue
        
        ann_old, img_old = ann_old_data
        ann_new, img_new = ann_new_data
        
        # Build pair ID
        pair_idx = len(pairs_out)
        pair_id = build_pair_id(pair_prefix, pair_idx)
        
        # Convert bboxes
        bbox_old = coco_bbox_to_xyxy(ann_old["bbox"])
        bbox_new = coco_bbox_to_xyxy(ann_new["bbox"])
        
        # Extract metadata
        label = cat_map[ann_old["category_id"]]
        attrs_old = ann_old.get("attributes", {})
        attrs_new = ann_new.get("attributes", {})
        
        change_type = map_change_type(label, attrs_old)
        severity = attrs_old.get("severity", "unknown")
        is_titleblock = attrs_old.get("is_titleblock", False)
        
        # Build page IDs
        doc_id = img_old["doc_id"]
        version_old = img_old["version_old"]
        version_new = img_old["version_new"]
        page_index_old = img_old["page_index"]
        page_index_new = img_new["page_index"]
        page_id_old = f"{doc_id}__{version_old}__p{page_index_old:04d}"
        page_id_new = f"{doc_id}__{version_new}__p{page_index_new:04d}"
        
        # Optional fields - try CVAT attributes first, then seed enrichment
        eco_id = attrs_old.get("eco_id")
        entity_id = attrs_old.get("entity_id")
        entity_kind = attrs_old.get("entity_kind")
        board_id = attrs_old.get("board_id")
        

        
        # Gold description (can be edited manually in CVAT or post-processed)
        change_desc_gt = attrs_old.get("change_desc_gt", "CHANGE_DESC_GT_TODO")
        notes = attrs_old.get("notes", "")

        # Enrich from seeds if available
        seed_key = (pair_prefix, page_index_old, tuple(bbox_old))
        if seed_key in seed_lookup:
            seed = seed_lookup[seed_key]
            eco_id = eco_id or seed.get("eco_id")
            entity_id = entity_id or seed.get("entity_id")
            if not change_desc_gt or change_desc_gt == "CHANGE_DESC_GT_TODO":
                change_desc_gt = seed.get("change_desc", "CHANGE_DESC_GT_TODO")
        
        # Determine split
        split = "train" if pair_prefix in split_ids else "test"
        
        # Build pair record
        pair_obj = {
            "pair_id": pair_id,
            "project_id": pair_prefix,
            "doc_id": doc_id,
            "board_id": board_id,
            "eco_id": eco_id,
            "version_id_old": version_old,
            "version_id_new": version_new,
            "page_index_old": page_index_old,
            "page_index_new": page_index_new,
            "page_id_old": page_id_old,
            "page_id_new": page_id_new,
            "bbox_old": bbox_old,
            "bbox_new": bbox_new,
            "object_id_old": None,
            "object_id_new": None,
            "change_type": change_type,
            "severity": severity,
            "is_titleblock": is_titleblock,
            "entity_id": entity_id,
            "entity_kind": entity_kind,
            "change_desc_gt": change_desc_gt,
            "notes": notes,
            "source": {
                "coco_project": Path(args.coco_path).stem,
                "coco_image_old": img_old["file_name"],
                "coco_image_new": img_new["file_name"]
            },
            "split": split
        }
        pairs_out.append(pair_obj)
        
        # Generate question
        q_obj = make_visualdiff_question(pair_obj)
        questions_out.append(q_obj)
    
    # Write outputs
    Path(args.out_pairs).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_pairs, 'w', encoding='utf-8') as f:
        for p in pairs_out:
            f.write(json.dumps(p, ensure_ascii=False) + '\n')
    
    Path(args.out_questions).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_questions, 'w', encoding='utf-8') as f:
        for q in questions_out:
            f.write(json.dumps(q, ensure_ascii=False) + '\n')
    
    print(f"[OK] Wrote {len(pairs_out)} pairs → {args.out_pairs}")
    print(f"[OK] Wrote {len(questions_out)} questions → {args.out_questions}")


if __name__ == "__main__":
    main()
