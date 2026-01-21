#!/usr/bin/env python3
"""
eval_microtext_baseline.py

OCR baseline for microtext recognition task.

Uses Tesseract OCR with 4x rotations to recognize tiny text in engineering documents.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List
import subprocess


def load_jsonl(path: str) -> List[dict]:
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def run_ocr_on_bbox(image_path: str, bbox: List[float]) -> str:
    """
    Run Tesseract OCR on a cropped bbox region.
    
    Args:
        image_path: Path to page image
        bbox: [x_min, y_min, x_max, y_max]
    
    Returns:
        Recognized text
    """
    # TODO: Implement actual OCR
    # 1. Load image
    # 2. Crop to bbox
    # 3. Try 4 rotations (0°, 90°, 180°, 270°)
    # 4. Run Tesseract on each
    # 5. Return best result (highest confidence)
    
    return "TODO_OCR_RESULT"


def calculate_cer(pred: str, gt: str) -> float:
    """
    Calculate Character Error Rate.
    
    Args:
        pred: Predicted text
        gt: Ground truth text
    
    Returns:
        CER (0.0 = perfect, 1.0 = completely wrong)
    """
    # TODO: Implement Levenshtein distance / CER calculation
    return 0.0


def main():
    ap = argparse.ArgumentParser(description="OCR baseline for microtext")
    ap.add_argument("--items", required=True, help="Path to microtext_items.jsonl")
    ap.add_argument("--pages-dir", required=True, help="Directory containing page PNGs")
    ap.add_argument("--out-results", required=True, help="Output results JSONL")
    args = ap.parse_args()
    
    items = load_jsonl(args.items)
    results = []
    
    total_cer = 0.0
    
    for item in items:
        # Build page image path
        page_id = item["page_id"]
        doc_id = item["doc_id"]
        version_id = item["version_id"]
        page_index = item["page_index"]
        
        # Assume page naming: derived/pages_300dpi/<doc_id>__<version_id>/page_XXX.png
        image_path = Path(args.pages_dir) / f"{doc_id}__{version_id}" / f"page_{page_index:03d}.png"
        
        if not image_path.exists():
            print(f"[WARN] Image not found: {image_path}")
            continue
        
        # Run OCR
        bbox = item["bbox"]
        pred_text = run_ocr_on_bbox(str(image_path), bbox)
        gt_text = item["text_gt"]
        
        # Calculate CER
        cer = calculate_cer(pred_text, gt_text)
        total_cer += cer
        
        result = {
            "item_id": item["item_id"],
            "pred_text": pred_text,
            "gt_text": gt_text,
            "cer": cer
        }
        results.append(result)
    
    # Write results
    with open(args.out_results, 'w', encoding='utf-8') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    
    # Print summary
    avg_cer = total_cer / len(results) if results else 0.0
    print(f"[OK] Processed {len(results)} items")
    print(f"[OK] Average CER: {avg_cer:.4f}")
    print(f"[OK] Results → {args.out_results}")


if __name__ == "__main__":
    main()
