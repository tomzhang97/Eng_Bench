#!/usr/bin/env python3
"""
eval_visualdiff_baseline.py

Siamese ResNet50 baseline for visual-diff detection.

Uses a simple Siamese CNN to detect changes between document revisions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List
import numpy as np


def load_jsonl(path: str) -> List[dict]:
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def load_siamese_model():
    """
    Load pretrained Siamese ResNet50 model.
    
    Returns:
        Model instance
    """
    # TODO: Implement model loading
    # 1. Load ResNet50 backbone
    # 2. Add Siamese architecture
    # 3. Load pretrained weights if available
    
    return None


def predict_change_map(model, image_old: np.ndarray, image_new: np.ndarray) -> np.ndarray:
    """
    Predict binary change map.
    
    Args:
        model: Siamese model
        image_old: Old revision image
        image_new: New revision image
    
    Returns:
        Binary change map (H x W)
    """
    # TODO: Implement prediction
    # 1. Preprocess images
    # 2. Forward pass through Siamese network
    # 3. Generate change heatmap
    # 4. Threshold to binary mask
    
    return np.zeros((100, 100))


def calculate_iou(pred_bbox: List[float], gt_bbox: List[float]) -> float:
    """
    Calculate IoU between predicted and ground truth bboxes.
    
    Args:
        pred_bbox: [x_min, y_min, x_max, y_max]
        gt_bbox: [x_min, y_min, x_max, y_max]
    
    Returns:
        IoU score (0.0 to 1.0)
    """
    # TODO: Implement IoU calculation
    x1_min, y1_min, x1_max, y1_max = pred_bbox
    x2_min, y2_min, x2_max, y2_max = gt_bbox
    
    # Intersection
    inter_x_min = max(x1_min, x2_min)
    inter_y_min = max(y1_min, y2_min)
    inter_x_max = min(x1_max, x2_max)
    inter_y_max = min(y1_max, y2_max)
    
    inter_area = max(0, inter_x_max - inter_x_min) * max(0, inter_y_max - inter_y_min)
    
    # Union
    area1 = (x1_max - x1_min) * (y1_max - y1_min)
    area2 = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = area1 + area2 - inter_area
    
    return inter_area / union_area if union_area > 0 else 0.0


def main():
    ap = argparse.ArgumentParser(description="Siamese CNN baseline for visual-diff")
    ap.add_argument("--pairs", required=True, help="Path to visualdiff_pairs.jsonl")
    ap.add_argument("--pages-dir", required=True, help="Directory containing page PNGs")
    ap.add_argument("--out-results", required=True, help="Output results JSONL")
    args = ap.parse_args()
    
    pairs = load_jsonl(args.pairs)
    model = load_siamese_model()
    
    results = []
    total_iou = 0.0
    
    for pair in pairs:
        # Load images
        # TODO: Implement image loading based on page_id_old/new
        
        # Predict change map
        # change_map = predict_change_map(model, image_old, image_new)
        
        # Extract bbox from change map
        # pred_bbox = extract_bbox_from_map(change_map)
        pred_bbox = [0, 0, 100, 100]  # Placeholder
        
        gt_bbox = pair["bbox_new"]
        
        # Calculate IoU
        iou = calculate_iou(pred_bbox, gt_bbox)
        total_iou += iou
        
        result = {
            "pair_id": pair["pair_id"],
            "pred_bbox": pred_bbox,
            "gt_bbox": gt_bbox,
            "iou": iou
        }
        results.append(result)
    
    # Write results
    with open(args.out_results, 'w', encoding='utf-8') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    
    # Print summary
    avg_iou = total_iou / len(results) if results else 0.0
    iou_at_50 = sum(1 for r in results if r["iou"] >= 0.5) / len(results) if results else 0.0
    
    print(f"[OK] Processed {len(results)} pairs")
    print(f"[OK] Average IoU: {avg_iou:.4f}")
    print(f"[OK] IoU@0.5: {iou_at_50:.4f}")
    print(f"[OK] Results → {args.out_results}")


if __name__ == "__main__":
    main()
