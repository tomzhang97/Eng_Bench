#!/usr/bin/env python3
"""
09_build_microtext_engbench.py

Build canonical Eng_Bench microtext JSONL files from seed annotations.

Outputs:
  - microtext_items.jsonl: Ground-truth text regions with bboxes
  - microtext_questions.jsonl: Q/A layer over items

Schema follows Eng_Bench specification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional


def load_jsonl(path: str) -> List[dict]:
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def load_split_ids(path: Optional[str]) -> set:
    """Load split file containing doc_ids (one per line)."""
    if path is None:
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}


def make_microtext_question(item: dict) -> dict:
    """
    Generate Q/A from a microtext item using templates.
    
    Args:
        item: microtext_items record
    
    Returns:
        microtext_questions record
    """
    dimension_name = item.get("dimension_name")
    category = item.get("category", "value")
    
    # Template selection based on category
    if category == "tolerance_value":
        if dimension_name:
            query = f"What tolerance is specified for {dimension_name} on this drawing?"
        else:
            query = "What tolerance is specified in this small text region on the drawing?"
    elif category == "dimension_value":
        if dimension_name:
            query = f"What is the dimension value for {dimension_name}?"
        else:
            query = "What dimension value is shown in this region?"
    elif category == "instrument_tag":
        query = "What is the instrument tag shown in this region?"
    else:
        query = "What text is shown in this small region on the drawing?"
    
    answer = item["text_gt"]
    
    return {
        "question_id": f"q_{item['item_id']}",
        "item_ids": [item["item_id"]],
        "doc_id": item["doc_id"],
        "version_id": item["version_id"],
        "query_text": query,
        "answer_text": answer,
        "answer_type": "span",
        "split": item["split"]
    }


def main():
    ap = argparse.ArgumentParser(
        description="Build Eng_Bench microtext JSONL from seed annotations"
    )
    ap.add_argument("--seed-path", required=True, help="Path to seed JSONL (e.g., tolerance_values.jsonl)")
    ap.add_argument("--doc-id", required=True, help="Document ID (e.g., tolerances_table_iso)")
    ap.add_argument("--version-id", required=True, help="Version ID (e.g., iso)")
    ap.add_argument("--category", required=True, help="Category: tolerance_value, dimension_value, instrument_tag")
    ap.add_argument("--split-file", required=False, help="Split file (doc_ids, one per line)")
    ap.add_argument("--out-items", required=True, help="Output path for microtext_items.jsonl")
    ap.add_argument("--out-questions", required=True, help="Output path for microtext_questions.jsonl")
    args = ap.parse_args()
    
    # Load inputs
    seeds = load_jsonl(args.seed_path)
    split_ids = load_split_ids(args.split_file)
    
    # Determine split
    split = "train" if args.doc_id in split_ids else "test"
    
    items_out = []
    questions_out = []
    
    # Process each seed
    for idx, seed in enumerate(seeds):
        # Build item ID
        page_index = seed.get("page", 0)
        item_id = f"mt__{args.doc_id}__{args.version_id}__p{page_index:04d}__{idx:04d}"
        
        # Build page ID
        page_id = f"{args.doc_id}__{args.version_id}__p{page_index:04d}"
        
        # Extract bbox
        bbox = seed.get("bbox_xyxy", seed.get("bbox"))
        
        # Build item record
        item_obj = {
            "item_id": item_id,
            "doc_id": args.doc_id,
            "board_id": None,
            "version_id": args.version_id,
            "page_index": page_index,
            "page_id": page_id,
            "object_id": None,
            "bbox": bbox,
            "text_gt": seed.get("target_text", seed.get("text", "")),
            "category": args.category,
            "font_height_px": None,  # Can be estimated later
            "dimension_name": None,  # Can be filled manually
            "notes": seed.get("notes", ""),
            "split": split
        }
        items_out.append(item_obj)
        
        # Generate question
        q_obj = make_microtext_question(item_obj)
        questions_out.append(q_obj)
    
    # Write outputs
    Path(args.out_items).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_items, 'w', encoding='utf-8') as f:
        for item in items_out:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    Path(args.out_questions).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_questions, 'w', encoding='utf-8') as f:
        for q in questions_out:
            f.write(json.dumps(q, ensure_ascii=False) + '\n')
    
    print(f"[OK] Wrote {len(items_out)} items → {args.out_items}")
    print(f"[OK] Wrote {len(questions_out)} questions → {args.out_questions}")


if __name__ == "__main__":
    main()
