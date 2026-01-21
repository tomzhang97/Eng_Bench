#!/usr/bin/env python3
"""
unify_dataset.py

Consolidates visualdiff and microtext into a single unified JSONL file
following standard benchmark conventions (HotpotQA, Musique).

Output Schema:
{
    "id": str,           # Unique question ID
    "task": str,         # "visualdiff" or "microtext"
    "question": str,     # The query text
    "answer": str,       # Ground truth answer
    "images": [str],     # List of relative image paths
    "evidence": [dict],  # Bboxes or other supporting evidence
    "split": str,        # "train" or "test"
    "metadata": dict     # Task-specific metadata
}
"""
import json
import os
from pathlib import Path

def load_jsonl(path):
    """Load JSONL file."""
    items = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
    return items

def process_visualdiff(root):
    """Process visualdiff pairs and questions into unified format."""
    pairs_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    questions_path = root / "visualdiff" / "annotations" / "visualdiff_questions.jsonl"
    
    pairs = {p["pair_id"]: p for p in load_jsonl(pairs_path)}
    questions = load_jsonl(questions_path)
    
    unified = []
    for q in questions:
        pair = pairs.get(q["pair_id"], {})
        
        # Build image paths
        page_idx = pair.get("page_index_old", 0)
        img_old = f"images/{pair.get('doc_id', '')}__{pair.get('version_id_old', '')}/page_{page_idx:04d}.png"
        img_new = f"images/{pair.get('doc_id', '')}__{pair.get('version_id_new', '')}/page_{page_idx:04d}.png"
        
        # Build evidence
        evidence = []
        if pair.get("bbox_old"):
            evidence.append({"bbox": pair["bbox_old"], "image_index": 0})
        if pair.get("bbox_new"):
            evidence.append({"bbox": pair["bbox_new"], "image_index": 1})
        
        unified.append({
            "id": q.get("question_id", q["pair_id"]),
            "task": "visualdiff",
            "question": q.get("query_text", ""),
            "answer": q.get("answer_text", ""),
            "images": [img_old, img_new],
            "evidence": evidence,
            "split": pair.get("split", "test"),
            "metadata": {
                "pair_id": q["pair_id"],
                "doc_id": pair.get("doc_id"),
                "change_type": pair.get("change_type", []),
            }
        })
    
    return unified

def process_microtext(root):
    """Process microtext items and questions into unified format."""
    items_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    questions_path = root / "microtext" / "annotations" / "microtext_questions.jsonl"
    
    items = {i["item_id"]: i for i in load_jsonl(items_path)}
    questions = load_jsonl(questions_path)
    
    # If no questions file, generate from items directly
    if not questions:
        questions = [{"item_id": i["item_id"], "query_text": f"Read the text at this location.", "answer_text": i.get("text_gt", "")} for i in items.values()]
    
    unified = []
    for q in questions:
        item = items.get(q.get("item_id"), {})
        
        # Build image path
        page_idx = item.get("page_index", 0)
        img_path = f"images/{item.get('doc_id', '')}__{item.get('version_id', '')}/page_{page_idx:04d}.png"
        
        # Build evidence
        evidence = []
        if item.get("bbox"):
            evidence.append({"bbox": item["bbox"], "image_index": 0})
        
        unified.append({
            "id": q.get("question_id", q.get("item_id", "")),
            "task": "microtext",
            "question": q.get("query_text", ""),
            "answer": q.get("answer_text", item.get("text_gt", "")),
            "images": [img_path],
            "evidence": evidence,
            "split": item.get("split", "test"),
            "metadata": {
                "item_id": q.get("item_id"),
                "doc_id": item.get("doc_id"),
                "category": item.get("category", ""),
            }
        })
    
    return unified

def main():
    root = Path(".")
    output_path = root / "eng_bench.jsonl"
    
    print("[*] Processing Visual Diff...")
    vdiff_data = process_visualdiff(root)
    print(f"    Found {len(vdiff_data)} Visual Diff questions.")
    
    print("[*] Processing Microtext...")
    micro_data = process_microtext(root)
    print(f"    Found {len(micro_data)} Microtext questions.")
    
    all_data = vdiff_data + micro_data
    
    print(f"[*] Writing unified dataset to {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        for item in all_data:
            f.write(json.dumps(item) + '\n')
    
    # Stats
    train_count = sum(1 for d in all_data if d["split"] == "train")
    test_count = sum(1 for d in all_data if d["split"] == "test")
    
    print(f"[OK] Unified dataset created:")
    print(f"     Total: {len(all_data)}")
    print(f"     Train: {train_count}")
    print(f"     Test:  {test_count}")
    print(f"     Visual Diff: {len(vdiff_data)}")
    print(f"     Microtext:   {len(micro_data)}")

if __name__ == "__main__":
    main()
