# Copyright 2024 The Eng_Bench Authors.

"""
Eng_Bench: A Unified Benchmark for Engineering Document Understanding.

Provides a HuggingFace-compatible loader for the unified eng_bench.jsonl.
"""

import json
import os
import datasets

def _generate_examples(base_path, split, task_filter=None):
    """Yields examples from the unified eng_bench.jsonl."""
    data_path = os.path.join(base_path, "eng_bench.jsonl")
    
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"Unified dataset not found at {data_path}. Run tools/unify_dataset.py first.")
    
    with open(data_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            
            # Filter by split
            if item.get("split") != split:
                continue
            
            # Optionally filter by task
            if task_filter and item.get("task") != task_filter:
                continue
            
            # Resolve image paths to absolute
            images = []
            for img_rel in item.get("images", []):
                img_abs = os.path.join(base_path, img_rel)
                images.append(img_abs)
            
            yield {
                "id": item["id"],
                "task": item["task"],
                "question": item["question"],
                "answer": item["answer"],
                "images": images,
                "evidence": item.get("evidence", []),
                "split": item["split"],
                "metadata": item.get("metadata", {}),
            }

def load_eng_bench(split="test", task=None, base_path=None, **kwargs):
    """
    Load Eng_Bench unified dataset.
    
    Args:
        split (str): "train" or "test".
        task (str, optional): Filter by task: "visualdiff" or "microtext". None = all.
        base_path (str, optional): Path to Eng_Bench root. Defaults to script directory.
        **kwargs: Additional arguments for datasets.Dataset.from_generator.
    
    Returns:
        datasets.Dataset
    """
    if base_path is None:
        base_path = os.path.abspath(os.path.dirname(__file__))

    features = datasets.Features({
        "id": datasets.Value("string"),
        "task": datasets.Value("string"),
        "question": datasets.Value("string"),
        "answer": datasets.Value("string"),
        "images": datasets.Sequence(datasets.Value("string")),
        "evidence": datasets.Sequence({
            "bbox": datasets.Sequence(datasets.Value("int32")),
            "image_index": datasets.Value("int32"),
        }),
        "split": datasets.Value("string"),
        "metadata": {
            "pair_id": datasets.Value("string"),
            "item_id": datasets.Value("string"),
            "doc_id": datasets.Value("string"),
            "change_type": datasets.Sequence(datasets.Value("string")),
            "category": datasets.Value("string"),
        },
    })

    generator = lambda: _generate_examples(base_path, split, task)

    return datasets.Dataset.from_generator(generator, features=features, **kwargs)
