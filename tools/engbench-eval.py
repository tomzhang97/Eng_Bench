import argparse
import json
import ast
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from engbench import load_dataset, evaluate

def main():
    parser = argparse.ArgumentParser(description="Evaluate Eng_Bench predictions")
    parser.add_argument("--predictions", type=str, required=True, help="Path to predictions JSONL")
    parser.add_argument("--split", type=str, default="test", help="Dataset split to evaluate on")
    parser.add_argument("--data-dir", type=str, default=".", help="Eng_Bench root directory")
    
    args = parser.parse_args()
    
    print(f"Loading dataset from {args.data_dir} (split: {args.split})...")
    dataset = load_dataset(args.data_dir, split=args.split)
    
    print(f"Reading predictions from {args.predictions}...")
    predictions = []
    with open(args.predictions, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    pred = json.loads(line)
                except json.JSONDecodeError:
                    pred = ast.literal_eval(line)
                predictions.append(pred)
                
    print("Evaluating...")
    results = evaluate(predictions, dataset)
    
    print("\n--- Results ---")
    for k, v in results.items():
        if isinstance(v, float):
            print(f"{k}: {v:.4f}")
        else:
            print(f"{k}: {v}")

if __name__ == "__main__":
    main()
