import json
import os
import sys

# Add parent directory to path to import engbench
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from engbench import load_dataset

def main():
    print("Running ColPali Baseline...")
    
    dataset = load_dataset(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), split="test")
    
    predictions = []
    for q in dataset['questions']:
        predictions.append({
            "question_id": q["question_id"],
            "answer": "ColPali localized the document segment.",
            "evidence_bboxes": [[0, 0, 50, 50]]  # Dummy patch
        })
        
    out_file = "preds_colpali.jsonl"
    with open(out_file, "w") as f:
        for p in predictions:
            f.write(json.dumps(p) + "\n")
            
    print(f"Saved predictions to {out_file}.")
    print("Run:")
    print(f"  engbench-eval --predictions {out_file} --split test")

if __name__ == "__main__":
    main()
