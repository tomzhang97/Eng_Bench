# Dataset Card for Eng_Bench

## Table of Contents
- [Dataset Description](#dataset-description)
- [Dataset Structure](#dataset-structure)
- [Dataset Creation](#dataset-creation)
- [Considerations for Using the Data](#considerations-for-using-the-data)
- [Additional Information](#additional-information)

## Dataset Description

- **Homepage:** https://github.com/tomzhang97/Eng_Bench
- **Repository:** https://github.com/tomzhang97/Eng_Bench. The repository publishes a rights-screened Silver snapshot; it is not a completed Gold v2.0 Global release.
- **Paper:** Not released yet.
- **Leaderboard:** Not released yet.
- **Point of Contact:** Not public in this local candidate.

### Dataset Summary

Eng_Bench is a comprehensive benchmark designed to evaluate Vision-Language Models (VLMs) on **Engineering Document Understanding** tasks. It focuses on two core challenges:

1.  **Visual Diff**: Identifying and describing semantic changes between two revisions of a complex technical drawing (e.g., schematics, datasheets).
2.  **Microtext**: Accurately reading and associating dense, small-font text (e.g., tolerance values, component labels) in engineering diagrams.

### Supported Tasks and Leaderboards

- `visualdiff`: Revision-difference localization and description. Headline metrics include evidence recall@IoU 0.3/0.5, old/new side hit rate, and description F1 as a secondary text metric.
- `microtext`: Visual Question Answering (VQA) over dense technical text. Headline metrics include exact match, normalized exact match, character error rate, and evidence IoU.

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
  "item_id": "mt__bbb_schematic_revC__C__p0004__0000",
  "image": <PIL.PngImagePlugin.PngImageFile>,
  "query_text": "Read the pin or component label in this region.",
  "answer_text": "J5",
  "category": "pin_label",
  "bbox": [1355, 2627, 1399, 2753]
}
```

### Data Splits

| Config | Split | Samples | Source |
| :--- | :--- | :--- | :--- |
| **visualdiff** | Train | 545 | PCB schematic revision families |
| | Dev | 38 | Held-out revision families |
| | Test | 871 | Held-out revision families |
| **microtext** | Train | 1146 | PCB, architectural/civil, mechanical, and process-sheet sources |
| | Dev | 730 | Document- and family-disjoint engineering sources |
| | Test | 359 | Held-out document families across multiple engineering domains |

The 2,235-row microtext set spans 39 source documents and is frozen into document- and family-disjoint train/dev/test splits. The split prevents source leakage, but train/dev remain dominated by PCB pin labels. Full P&ID/process-sheet labels remain low-volume. The earlier ISO tolerance seed rows remain outside active gold pending source-rights review. Invalid visualdiff evidence and human-polish failures remain quarantined from the active benchmark.

## Dataset Creation

### Curation Rationale
Engineering documents require high-precision visual reasoning that general-purpose VLMs often lack. Eng_Bench provides a specialized testbed for "needle-in-a-haystack" retrieval and difference analysis.

### Source Data
- **BeagleBone Black**: Open-source hardware schematics (Creative Commons).
- **Toradex Viola**: Carrier board datasheets (Publicly available technical docs).
- **Cornell Mechanical Drawing / Internet Archive**: Public-domain mechanical drawing scans imported through Wikimedia Commons.
- **Aquila / BBB Schematics**: Public vendor design resources and CC/open-hardware schematic pages used for high-density component and pin labels.
- **USDA / municipal / Commons public documents**: Architectural, civil, and P&ID/instrumentation public documents used for non-PCB domain coverage.
- **ISO Tolerances**: Standard reference tables retained as source provenance; rotated bboxes have been repaired and reviewed, but rows remain rights-held outside active gold pending release review.

### Annotations
- **Visual Diff**: Semi-automated pipeline. Initial candidates generated via diff-maps (ORB alignment), then refined by CVAT/human review and a 2026-05-14 Codex-assisted description pass. The assisted pass cleared all dev/test pending descriptions and retains `desc_source`, `review_confidence`, and `review_evidence` for audit. The 2026-05-18 human polish pass adjudicated the 98 low-confidence dev/test rows: 4 were retained, 77 no-change rows were quarantined, and 17 bbox-mismatch rows were quarantined. The remaining visualdiff TODO rows are 125 train-only descriptions.
- **Microtext**: Candidate generation from text layers and spatial matching. Current active rows entered through crop-level review. Future objective train-only rows may use the calibrated machine-certification policy in `docs/MACHINE_CERTIFICATION_POLICY.md`; dev/test, semantic labels, and ambiguous evidence remain human-reviewed, and certification provenance is retained per row.

## Considerations for Using the Data

### Social Impact of Dataset
Improves automation in hardware engineering and manufacturing, potentially reducing errors in design review.

### Discussion of Biases
The current split is broader than the original PCB-only seed and now includes mechanical, architectural/civil, and P&ID/instrumentation rows. It is still pin-label heavy in train/dev, and full process-sheet/P&ID coverage remains low-volume.

## Additional Information

### Dataset Curators
Eng_Bench project maintainers.

### Licensing Information
Provisional mixed-source candidate. Active source status is tracked in `SOURCE_INVENTORY.csv` and `manifest.jsonl`; the 2026-07-31 provenance audit resolves all 48 active source documents and verifies matching local-file SHA-256 values. Final public redistribution language and attribution packaging are still pending.

### Citation Information
```bibtex
@misc{engbench2026,
  title={Eng_Bench: A Benchmark for Engineering Document Understanding},
  author={Eng_Bench maintainers},
  year={2026}
}
```
