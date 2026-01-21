# Dataset Card for Eng_Bench

## Table of Contents
- [Dataset Description](#dataset-description)
- [Dataset Structure](#dataset-structure)
- [Dataset Creation](#dataset-creation)
- [Considerations for Using the Data](#considerations-for-using-the-data)
- [Additional Information](#additional-information)

## Dataset Description

- **Homepage:** [Needs Update]
- **Repository:** [Needs Update]
- **Paper:** [Needs Update]
- **Leaderboard:** [Needs Update]
- **Point of Contact:** [Needs Update]

### Dataset Summary

Eng_Bench is a comprehensive benchmark designed to evaluate Vision-Language Models (VLMs) on **Engineering Document Understanding** tasks. It focuses on two core challenges:

1.  **Visual Diff**: Identifying and describing semantic changes between two revisions of a complex technical drawing (e.g., schematics, datasheets).
2.  **Microtext**: Accurately reading and associating dense, small-font text (e.g., tolerance values, component labels) in engineering diagrams.

### Supported Tasks and Leaderboards

- `visualdiff`: Object Detection and Description. Metric: mAP@0.5, Traceability Score.
- `microtext`: Visual Question Answering (VQA). Metric: Accuracy (Exact Match).

### Languages

English (Technical).

## Dataset Structure

### Data Instances

#### visualdiff
```json
{
  "pair_id": "vdiff__viola__pcbV1.0__to__pcbV1.1__0000",
  "image_old": <PIL.PngImagePlugin.PngImageFile>,
  "image_new": <PIL.PngImagePlugin.PngImageFile>,
  "query_text": "What changed about the symbol or representation of this component?",
  "answer_text": "R47 value changed from 10k to 22k",
  "change_type": ["value", "text"],
  "bbox_old": [100, 100, 200, 200],
  "bbox_new": [100, 100, 200, 200]
}
```

#### microtext
```json
{
  "item_id": "mt__tolerances_table_iso__iso__p0002__0000",
  "image": <PIL.PngImagePlugin.PngImageFile>,
  "query_text": "Read the tolerance value at row 3 column 2.",
  "answer_text": "H7/g6",
  "category": "tolerance_value",
  "bbox": [1355, 2627, 1399, 2753]
}
```

### Data Splits

| Config | Split | Samples | Source |
| :--- | :--- | :--- | :--- |
| **visualdiff** | Train | 526 | BeagleBone Black Schematics |
| | Test | 962 | Toradex Viola Datasheets |
| **microtext** | Test | 58 | ISO Tolerance Tables |

## Dataset Creation

### Curation Rationale
Engineering documents require high-precision visual reasoning that general-purpose VLMs often lack. Eng_Bench provides a specialized testbed for "needle-in-a-haystack" retrieval and difference analysis.

### Source Data
- **BeagleBone Black**: Open-source hardware schematics (Creative Commons).
- **Toradex Viola**: Carrier board datasheets (Publicly available technical docs).
- **ISO Tolerances**: Standard reference tables.

### Annotations
- **Visual Diff**: Semi-automated pipeline. Initial candidates generated via diff-maps (ORB alignment), then refined by human annotators (CVAT).
- **Microtext**: Seed-based generation using OCR text layers and spatial matching.

## Considerations for Using the Data

### Social Impact of Dataset
Improves automation in hardware engineering and manufacturing, potentially reducing errors in design review.

### Discussion of Biases
Primarily focused on electronics (PCBs) and tables. May not generalize to mechanical CAD drawings or architectural blueprints without further fine-tuning.

## Additional Information

### Dataset Curators
Google Deepmind Advanced Agentic Coding Team.

### Licensing Information
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) (Provisional).

### Citation Information
```bibtex
@misc{engbench2026,
  title={Eng_Bench: A Benchmark for Engineering Document Understanding},
  author={Google Deepmind},
  year={2026}
}
```
