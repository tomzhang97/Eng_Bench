#!/usr/bin/env python3
"""
validate_engbench.py

Light validation for Eng_Bench JSONL files.

Checks:
  - Required fields present
  - Bbox validity (x_min < x_max, y_min < y_max)
  - Split values valid
  - Question-pair/item linkage
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Set


def load_jsonl(path: str) -> List[dict]:
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def validate_bbox(bbox: List[float], name: str) -> List[str]:
    """Validate bbox format and constraints."""
    errors = []
    if not isinstance(bbox, list) or len(bbox) != 4:
        errors.append(f"{name}: bbox must be 4-element list, got {bbox}")
        return errors
    
    x_min, y_min, x_max, y_max = bbox
    if x_min >= x_max:
        errors.append(f"{name}: x_min ({x_min}) >= x_max ({x_max})")
    if y_min >= y_max:
        errors.append(f"{name}: y_min ({y_min}) >= y_max ({y_max})")
    
    return errors


def validate_visualdiff_pairs(pairs: List[dict]) -> List[str]:
    """Validate visualdiff_pairs.jsonl."""
    errors = []
    required_fields = [
        "pair_id", "doc_id", "version_id_old", "version_id_new",
        "page_index_old", "page_index_new", "page_id_old", "page_id_new",
        "bbox_old", "bbox_new", "change_type", "change_desc_gt", "split"
    ]
    
    for i, pair in enumerate(pairs):
        prefix = f"Pair {i} ({pair.get('pair_id', 'UNKNOWN')})"
        
        # Check required fields
        for field in required_fields:
            if field not in pair:
                errors.append(f"{prefix}: missing required field '{field}'")
        
        # Validate bboxes
        if "bbox_old" in pair:
            errors.extend(validate_bbox(pair["bbox_old"], f"{prefix} bbox_old"))
        if "bbox_new" in pair:
            errors.extend(validate_bbox(pair["bbox_new"], f"{prefix} bbox_new"))
        
        # Validate change_type
        if "change_type" in pair:
            if not isinstance(pair["change_type"], list) or len(pair["change_type"]) == 0:
                errors.append(f"{prefix}: change_type must be non-empty list")
        
        # Validate split
        if "split" in pair and pair["split"] not in ["train", "dev", "test"]:
            errors.append(f"{prefix}: split must be 'train', 'dev', or 'test', got '{pair['split']}'")
    
    return errors


def validate_visualdiff_questions(questions: List[dict], pair_ids: Set[str]) -> List[str]:
    """Validate visualdiff_questions.jsonl."""
    errors = []
    required_fields = ["question_id", "pair_id", "query_text", "answer_text", "split"]
    
    for i, q in enumerate(questions):
        prefix = f"Question {i} ({q.get('question_id', 'UNKNOWN')})"
        
        # Check required fields
        for field in required_fields:
            if field not in q:
                errors.append(f"{prefix}: missing required field '{field}'")
        
        # Check pair_id linkage
        if "pair_id" in q and q["pair_id"] not in pair_ids:
            errors.append(f"{prefix}: pair_id '{q['pair_id']}' not found in pairs")
        
        # Validate split
        if "split" in q and q["split"] not in ["train", "dev", "test"]:
            errors.append(f"{prefix}: split must be 'train', 'dev', or 'test', got '{q['split']}'")
    
    return errors


def validate_microtext_items(items: List[dict]) -> List[str]:
    """Validate microtext_items.jsonl."""
    errors = []
    required_fields = [
        "item_id", "doc_id", "version_id", "page_index", "page_id",
        "bbox", "text_gt", "category", "split"
    ]
    
    for i, item in enumerate(items):
        prefix = f"Item {i} ({item.get('item_id', 'UNKNOWN')})"
        
        # Check required fields
        for field in required_fields:
            if field not in item:
                errors.append(f"{prefix}: missing required field '{field}'")
        
        # Validate bbox
        if "bbox" in item:
            errors.extend(validate_bbox(item["bbox"], f"{prefix} bbox"))
        
        # Validate split
        if "split" in item and item["split"] not in ["train", "dev", "test"]:
            errors.append(f"{prefix}: split must be 'train', 'dev', or 'test', got '{item['split']}'")
    
    return errors


def validate_microtext_questions(questions: List[dict], item_ids: Set[str]) -> List[str]:
    """Validate microtext_questions.jsonl."""
    errors = []
    required_fields = ["question_id", "item_ids", "query_text", "answer_text", "split"]
    
    for i, q in enumerate(questions):
        prefix = f"Question {i} ({q.get('question_id', 'UNKNOWN')})"
        
        # Check required fields
        for field in required_fields:
            if field not in q:
                errors.append(f"{prefix}: missing required field '{field}'")
        
        # Check item_ids linkage
        if "item_ids" in q:
            if not isinstance(q["item_ids"], list) or len(q["item_ids"]) == 0:
                errors.append(f"{prefix}: item_ids must be non-empty list")
            else:
                for item_id in q["item_ids"]:
                    if item_id not in item_ids:
                        errors.append(f"{prefix}: item_id '{item_id}' not found in items")
        
        # Validate split
        if "split" in q and q["split"] not in ["train", "dev", "test"]:
            errors.append(f"{prefix}: split must be 'train', 'dev', or 'test', got '{q['split']}'")
    
    return errors


def main():
    ap = argparse.ArgumentParser(description="Validate Eng_Bench JSONL files")
    ap.add_argument("--visualdiff-pairs", help="Path to visualdiff_pairs.jsonl")
    ap.add_argument("--visualdiff-questions", help="Path to visualdiff_questions.jsonl")
    ap.add_argument("--microtext-items", help="Path to microtext_items.jsonl")
    ap.add_argument("--microtext-questions", help="Path to microtext_questions.jsonl")
    args = ap.parse_args()
    
    all_errors = []
    
    # Validate visual-diff
    if args.visualdiff_pairs:
        print(f"[*] Validating {args.visualdiff_pairs}...")
        pairs = load_jsonl(args.visualdiff_pairs)
        errors = validate_visualdiff_pairs(pairs)
        all_errors.extend(errors)
        
        if not errors:
            print(f"    ✓ {len(pairs)} pairs valid")
        
        # Validate questions if provided
        if args.visualdiff_questions:
            print(f"[*] Validating {args.visualdiff_questions}...")
            questions = load_jsonl(args.visualdiff_questions)
            pair_ids = {p["pair_id"] for p in pairs if "pair_id" in p}
            errors = validate_visualdiff_questions(questions, pair_ids)
            all_errors.extend(errors)
            
            if not errors:
                print(f"    ✓ {len(questions)} questions valid")
    
    # Validate microtext
    if args.microtext_items:
        print(f"[*] Validating {args.microtext_items}...")
        items = load_jsonl(args.microtext_items)
        errors = validate_microtext_items(items)
        all_errors.extend(errors)
        
        if not errors:
            print(f"    ✓ {len(items)} items valid")
        
        # Validate questions if provided
        if args.microtext_questions:
            print(f"[*] Validating {args.microtext_questions}...")
            questions = load_jsonl(args.microtext_questions)
            item_ids = {i["item_id"] for i in items if "item_id" in i}
            errors = validate_microtext_questions(questions, item_ids)
            all_errors.extend(errors)
            
            if not errors:
                print(f"    ✓ {len(questions)} questions valid")
    
    # Report results
    if all_errors:
        print(f"\n[!] Found {len(all_errors)} validation errors:")
        for error in all_errors:
            print(f"    - {error}")
        return 1
    else:
        print("\n[✓] All validations passed!")
        return 0


if __name__ == "__main__":
    exit(main())
