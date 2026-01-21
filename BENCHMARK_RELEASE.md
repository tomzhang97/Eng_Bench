# Eng_Bench v0.9 (Silver) Release Notes

**Date:** 2026-01-16
**Status:** Silver (Automated Seeds + Mock Validation)
**Total Samples:** 1488 Visual-Diff Pairs, 57 Microtext Items
**License:** [CC-BY-4.0](DATACARD.md)

## 1. Overview
Eng_Bench is a benchmark for engineering diagram understanding, focusing on **Visual Diff** (detecting changes between revisions) and **Microtext** (reading dense technical text). See [DATACARD.md](DATACARD.md) for full details. 

This "Silver" release validates the complete end-to-end pipeline, dataset schema, and evaluation infrastructure. Annotations are currently derived from automated seeds (mocked as human labels); the structure is 100% ready for "Gold" human refinement.

## 2. Directory Structure
The dataset is self-contained in the `Eng_Bench` root:

```text
Eng_Bench/
├── images/                          # Canonical Image Repository
│   ├── viola__pcbV1.0/              # {doc_id}__{version_id}
│   │   ├── page_0000.png            # 300 DPI Rendering
│   │   └── ...
│   ├── viola__pcbV1.1/
│   ├── bbb__C/
│   └── bbb__C3/
├── visualdiff/
│   └── annotations/
│       ├── visualdiff_pairs.jsonl      # Main Dataset (Pairs)
│       └── visualdiff_questions.jsonl  # VQA Format (Q&A)
├── microtext/
│   └── annotations/
│       ├── microtext_items.jsonl       # Main Dataset (Items)
│       └── microtext_questions.jsonl   # VQA Format (Q&A)
└── splits/
    ├── visualdiff_train.txt            # Training Set (BBB Schematics)
    └── visualdiff_test.txt             # Test Set (Viola Datasheets)
```

## 3. Usage Guide

### Loading Images
Images are referenced by `doc_id`, `version_id`, and `page_index`.
**Resolution Rule:** `images/{doc_id}__{version_id}/page_{page_index:04d}.png`

```python
import json
import os

ROOT = "Eng_Bench"

with open(os.path.join(ROOT, "visualdiff/annotations/visualdiff_pairs.jsonl"), "r") as f:
    for line in f:
        row = json.loads(line)
        # Construct Image Path
        img_name = f"page_{row['page_index_old']:04d}.png"
        img_path = os.path.join(ROOT, "images", f"{row['doc_id']}__{row['version_id_old']}", img_name)
        
        print(f"Loading {img_path} for change {row['change_id']}")
```

### Splits
To prevent data leakage, we strictly separate by **Document Family**:
*   **Train**: `bbb` (BeagleBone Black Schematics). Same model, different revisions.
*   **Test**: `viola` (Toradex Viola Datasheets). Completely different board and diagram style.
*   **Split File Format**: Simple list of `pair_id`s.

## 4. Statistics (v0.9 Silver)

| Dataset Content | Count | Source |
| :--- | :--- | :--- |
| **Visual-Diff Pairs** | **1488** | `05_seed_diff` + `07_cvat` + `manual_v1.1_v1.2` |
| - Train (BBB) | 526 | Schematics |
| - Test (Viola) | 962 | Datasheets |
| **Microtext Items** | **57** | `08_seed` + `09_build` |

## 5. From Silver to Gold
To upgrade this dataset to v1.0 (Gold):
1.  **Import to CVAT**: Upload the images and `seeds` to CVAT (Task 6 in `next_steps.md`).
2.  **Human Annotation**: Correct the `change_desc`, refine `bbox`, and assign correct attributes.
3.  **Export**: Export "COCO 1.0" from CVAT.
4.  **Convert**: Run `07_cvat_coco_to_engbench_jsonl.py` pointing to the real export.

The resulting JSONL files will be drop-in replacements for these Silver files.

## 6. Usage (v1.0 Gold)

### Installation

```bash
pip install -r requirements.txt
```

### Loading Data (Python)

We provide a simple loader that returns HuggingFace `Dataset` objects:

```python
from eng_bench import load_eng_bench

# Load Visual Diff Test Set
ds = load_eng_bench("visualdiff", split="test")

# Access Data
item = ds[0]
print(f"Question: {item['query_text']}")
print(f"Image: {item['image_new']}") # PIL Image
```

### Running Evaluation

Use the `benchmark_runner.py` tool to evaluate your predictions:

```bash
python tools/benchmark_runner.py --gt ground_truth.jsonl --pred predictions.jsonl --task visualdiff
```
