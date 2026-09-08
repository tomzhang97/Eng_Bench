#!/usr/bin/env python3
"""
split_gold_todo.py

Separates visualdiff annotations into:
- GOLD: Complete samples (have real change descriptions)
- TODO: Incomplete samples (contain CHANGE_DESC_GT_TODO placeholders)

This enables focused annotation work on the TODO subset.
"""

import json
import argparse
from pathlib import Path
from collections import Counter

PLACEHOLDER = "CHANGE_DESC_GT_TODO"

def load_jsonl(path):
    """Load JSONL file."""
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items

def save_jsonl(items, path):
    """Save items to JSONL file."""
    with open(path, 'w', encoding='utf-8') as f:
        for item in items:
            f.write(json.dumps(item) + '\n')

def is_complete(item):
    """Check if an item has complete annotations (no placeholders)."""
    # Check change_desc_gt field
    desc = item.get("change_desc_gt", "")
    if desc == PLACEHOLDER or desc == "":
        return False
    
    # Check answer_text field (for questions)
    answer = item.get("answer_text", "")
    if answer == PLACEHOLDER or answer == "":
        return False
    
    # Check severity is specified
    severity = item.get("severity", "unknown")
    if severity == "unknown":
        return False
    
    return True

def analyze_pairs(pairs):
    """Analyze pairs and classify as GOLD or TODO."""
    gold = []
    todo = []
    
    for pair in pairs:
        desc = pair.get("change_desc_gt", "")
        if desc == PLACEHOLDER or desc == "":
            todo.append(pair)
        else:
            gold.append(pair)
    
    return gold, todo

def analyze_questions(questions, todo_pair_ids):
    """Split questions based on their pair's status."""
    gold_q = []
    todo_q = []
    
    for q in questions:
        if q.get("pair_id") in todo_pair_ids:
            todo_q.append(q)
        else:
            gold_q.append(q)
    
    return gold_q, todo_q

def main():
    parser = argparse.ArgumentParser(description="Split annotations into GOLD and TODO")
    parser.add_argument("--pairs", default="visualdiff/annotations/visualdiff_pairs.jsonl",
                        help="Path to pairs JSONL")
    parser.add_argument("--questions", default="visualdiff/annotations/visualdiff_questions.jsonl",
                        help="Path to questions JSONL")
    parser.add_argument("--output-dir", default="visualdiff/annotations",
                        help="Output directory for split files")
    args = parser.parse_args()
    
    base = Path(".")
    pairs_path = base / args.pairs
    questions_path = base / args.questions
    output_dir = base / args.output_dir
    
    print(f"[*] Loading pairs from {pairs_path}...")
    pairs = load_jsonl(pairs_path)
    print(f"    Loaded {len(pairs)} pairs")
    
    print(f"[*] Loading questions from {questions_path}...")
    questions = load_jsonl(questions_path)
    print(f"    Loaded {len(questions)} questions")
    
    # Analyze pairs
    print("\n[*] Analyzing pairs...")
    gold_pairs, todo_pairs = analyze_pairs(pairs)
    
    print(f"    GOLD (complete): {len(gold_pairs)}")
    print(f"    TODO (incomplete): {len(todo_pairs)}")
    print(f"    Completion rate: {len(gold_pairs) / len(pairs) * 100:.1f}%")
    
    # Get TODO pair IDs for question splitting
    todo_pair_ids = {p["pair_id"] for p in todo_pairs}
    
    # Analyze questions
    print("\n[*] Splitting questions...")
    gold_questions, todo_questions = analyze_questions(questions, todo_pair_ids)
    print(f"    GOLD questions: {len(gold_questions)}")
    print(f"    TODO questions: {len(todo_questions)}")
    
    # Save splits
    print("\n[*] Saving split files...")
    
    gold_pairs_path = output_dir / "visualdiff_pairs_GOLD.jsonl"
    todo_pairs_path = output_dir / "visualdiff_pairs_TODO.jsonl"
    gold_q_path = output_dir / "visualdiff_questions_GOLD.jsonl"
    todo_q_path = output_dir / "visualdiff_questions_TODO.jsonl"
    
    save_jsonl(gold_pairs, gold_pairs_path)
    print(f"    Saved {len(gold_pairs)} pairs to {gold_pairs_path}")
    
    save_jsonl(todo_pairs, todo_pairs_path)
    print(f"    Saved {len(todo_pairs)} pairs to {todo_pairs_path}")
    
    save_jsonl(gold_questions, gold_q_path)
    print(f"    Saved {len(gold_questions)} questions to {gold_q_path}")
    
    save_jsonl(todo_questions, todo_q_path)
    print(f"    Saved {len(todo_questions)} questions to {todo_q_path}")
    
    # Statistics
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    print(f"Total pairs:     {len(pairs)}")
    print(f"  GOLD:          {len(gold_pairs)} ({len(gold_pairs)/len(pairs)*100:.1f}%)")
    print(f"  TODO:          {len(todo_pairs)} ({len(todo_pairs)/len(pairs)*100:.1f}%)")
    print(f"Total questions: {len(questions)}")
    print(f"  GOLD:          {len(gold_questions)}")
    print(f"  TODO:          {len(todo_questions)}")
    print("="*50)
    
    # Breakdown by change_type
    print("\nTODO breakdown by change_type:")
    change_types = Counter()
    for p in todo_pairs:
        for ct in p.get("change_type", ["unknown"]):
            change_types[ct] += 1
    for ct, count in change_types.most_common():
        print(f"  {ct}: {count}")

if __name__ == "__main__":
    main()
