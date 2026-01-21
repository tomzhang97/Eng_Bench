#!/usr/bin/env python3
"""
benchmark_runner.py

Unified benchmark evaluation for Eng_Bench.
Handles both visualdiff and microtext tasks from a single input stream.

Metrics:
- Visual Diff: mAP@0.5, Recall@0.5
- Microtext: Accuracy (Exact Match)
"""
import json
import argparse
from collections import defaultdict

def load_jsonl(path):
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items

def iou(box1, box2):
    """Calculate IoU between two boxes [x1, y1, x2, y2]."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    
    inter_area = max(0, x2 - x1) * max(0, y2 - y1)
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])
    
    union_area = box1_area + box2_area - inter_area
    if union_area == 0:
        return 0.0
    return inter_area / union_area

def evaluate_visualdiff(gt_items, pred_items, iou_threshold=0.5):
    """Evaluate visual diff predictions."""
    pred_map = {p["id"]: p for p in pred_items}
    
    tp, fp, fn = 0, 0, 0
    
    for gt in gt_items:
        pred = pred_map.get(gt["id"])
        if not pred:
            fn += len(gt.get("evidence", []))
            continue
        
        gt_bboxes = [e["bbox"] for e in gt.get("evidence", []) if e.get("bbox")]
        pred_bboxes = [e["bbox"] for e in pred.get("evidence", []) if e.get("bbox")]
        
        matched_gt = set()
        for pb in pred_bboxes:
            best_iou = 0
            best_idx = -1
            for i, gb in enumerate(gt_bboxes):
                if i in matched_gt:
                    continue
                current_iou = iou(pb, gb)
                if current_iou > best_iou:
                    best_iou = current_iou
                    best_idx = i
            
            if best_iou >= iou_threshold:
                tp += 1
                matched_gt.add(best_idx)
            else:
                fp += 1
        
        fn += len(gt_bboxes) - len(matched_gt)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    
    return {"precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn}

def evaluate_microtext(gt_items, pred_items):
    """Evaluate microtext predictions (exact match accuracy)."""
    pred_map = {p["id"]: p for p in pred_items}
    
    correct = 0
    total = 0
    
    for gt in gt_items:
        pred = pred_map.get(gt["id"])
        total += 1
        
        if not pred:
            continue
        
        gt_answer = str(gt.get("answer", "")).strip().lower()
        pred_answer = str(pred.get("answer", "")).strip().lower()
        
        if gt_answer == pred_answer:
            correct += 1
    
    accuracy = correct / total if total > 0 else 0
    return {"accuracy": accuracy, "correct": correct, "total": total}

def run_benchmark(gt_path, pred_path, task=None):
    """Run benchmark evaluation."""
    gt_items = load_jsonl(gt_path)
    pred_items = load_jsonl(pred_path)
    
    print(f"Loading GT: {gt_path}")
    print(f"Loading Preds: {pred_path}")
    print("-" * 30)
    
    # Group by task
    gt_by_task = defaultdict(list)
    for item in gt_items:
        gt_by_task[item.get("task", "unknown")].append(item)
    
    pred_by_task = defaultdict(list)
    for item in pred_items:
        pred_by_task[item.get("task", "unknown")].append(item)
    
    print("Benchmark Results:")
    
    # Visual Diff
    if (task is None or task == "visualdiff") and gt_by_task["visualdiff"]:
        print(f"\n[Visual Diff] ({len(gt_by_task['visualdiff'])} samples)")
        vd_results = evaluate_visualdiff(gt_by_task["visualdiff"], pred_by_task["visualdiff"])
        print(f"  Precision: {vd_results['precision']:.4f}")
        print(f"  Recall:    {vd_results['recall']:.4f}")
        print(f"  TP: {vd_results['tp']}, FP: {vd_results['fp']}, FN: {vd_results['fn']}")
    
    # Microtext
    if (task is None or task == "microtext") and gt_by_task["microtext"]:
        print(f"\n[Microtext] ({len(gt_by_task['microtext'])} samples)")
        mt_results = evaluate_microtext(gt_by_task["microtext"], pred_by_task["microtext"])
        print(f"  Accuracy: {mt_results['accuracy']:.4f}")
        print(f"  Correct: {mt_results['correct']}/{mt_results['total']}")

def main():
    ap = argparse.ArgumentParser(description="Eng_Bench Unified Benchmark Runner")
    ap.add_argument("--gt", type=str, required=True, help="Path to ground truth JSONL")
    ap.add_argument("--pred", type=str, required=True, help="Path to predictions JSONL")
    ap.add_argument("--task", type=str, default=None, choices=["visualdiff", "microtext"], help="Filter by task")
    args = ap.parse_args()
    
    run_benchmark(args.gt, args.pred, args.task)

if __name__ == "__main__":
    main()
