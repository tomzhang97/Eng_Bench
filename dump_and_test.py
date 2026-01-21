
import json
import argparse
import sys
# from datasets import load_dataset # DEPRECATED
from eng_bench import load_eng_bench # NEW
from tools.benchmark_runner import run_benchmark

def dump_and_test():
    print("Step 1: Loading Visual Diff via HF loader...")
    try:
        # ds_diff = load_dataset("eng_bench.py", "visualdiff", split="test", trust_remote_code=True)
        ds_diff = load_eng_bench("visualdiff", split="test")
    except Exception as e:
        print(f"Failed to load dataset: {e}")
        import traceback
        traceback.print_exc()
        return

    print("Step 2: Dumping to JSONL (acting as GT and Pred)...")
    dump_path = "temp_visualdiff_gt.jsonl"
    with open(dump_path, 'w', encoding='utf-8') as f:
        for item in ds_diff:
            # Reconstruct format expected by runner
            
            evidence_list = []
            if item.get('bbox_old') and sum(item['bbox_old']) > 0:
                 evidence_list.append({'bbox': item['bbox_old']})
            if item.get('bbox_new') and sum(item['bbox_new']) > 0:
                 evidence_list.append({'bbox': item['bbox_new']})
            
            pred_bbox = item.get('bbox_new')
            if not pred_bbox or sum(pred_bbox) == 0:
                pred_bbox = item.get('bbox_old')
                
            record = {
                "question_id": item['question_id'],
                "evidence_list": evidence_list,
                "bbox": pred_bbox, # For prediction
                "score": 1.0
            }
            f.write(json.dumps(record) + '\n')
            
    print("Step 3: Running Benchmark Runner...")
    print("--- Self-Test Results ---")
    run_benchmark(dump_path, dump_path, task="visualdiff")
    
    # Microtext
    print("\nStep 4: Microtext Self-Test...")
    # ds_micro = load_dataset("eng_bench.py", "microtext", split="test", trust_remote_code=True)
    ds_micro = load_eng_bench("microtext", split="test", cache_dir="./temp_hf_cache")
    
    dump_micro_path = "temp_microtext_gt.jsonl"
    with open(dump_micro_path, 'w', encoding='utf-8') as f:
        for item in ds_micro:
            record = {
                "question_id": item['question_id'],
                "answer_text": item['answer_text']
            }
            f.write(json.dumps(record) + '\n')
            
    run_benchmark(dump_micro_path, dump_micro_path, task="microtext")

if __name__ == "__main__":
    dump_and_test()
