# Eng_Bench: Engineering Document Benchmark

> **[🚀 SEE v0.9 SILVER RELEASE NOTES](BENCHMARK_RELEASE.md)**  
> Comparison stats, loading snippets, and split details are in `BENCHMARK_RELEASE.md`.

A comprehensive benchmark for evaluating vision-language models on engineering document understanding tasks, including visual-diff detection and microtext recognition.

## 📦 Quick Start

### Directory Structure
```
eng-bench/
├── images/                      # Canonical 300 DPI Images (NEW)
│   ├── viola__pcbV1.0/
│   │   └── page_0000.png
│   └── ...
├── visualdiff/
│   ├── docs/                    # Source PDFs
│   └── annotations/
│       ├── visualdiff_pairs.jsonl
│       └── visualdiff_questions.jsonl
├── microtext/
│   ├── docs/                    # Source PDFs
│   └── annotations/
│       ├── microtext_items.jsonl
│       └── microtext_questions.jsonl
├── derived/                     # Intermediate build artifacts
├── splits/
├── tools/                       # Pipeline scripts
├── BENCHMARK_RELEASE.md         # Release Notes & Usage
├── SCHEMA.md                    # JSONL specification
└── CVAT_TAXONOMY.md             # Annotation guide
```

## ✅ Infrastructure Complete

### Core Converters

**Script 07 v2 – `07_cvat_coco_to_engbench_jsonl.py`**

* Parses CVAT COCO exports
* Groups annotations by `change_id` to pair old/new boxes into logical changes
* Maps CVAT labels/attributes → Eng_Bench `change_type`, `severity`, `is_titleblock`, etc.
* Outputs:
  * `visualdiff_pairs.jsonl`
  * `visualdiff_questions.jsonl`
* Validated:
  * Filename parsing (doc, version_old/new, page, role)
  * COCO bbox → `[x_min, y_min, x_max, y_max]`
  * `change_type` mapping consistent with `CVAT_TAXONOMY.md`

**Script 09 – `09_build_microtext_engbench.py`**

* Converts microtext/tolerance seeds → canonical `microtext_items.jsonl`
* Generates Q/A with category-specific templates → `microtext_questions.jsonl`
* Tested: 57 items + 57 questions generated and pass validation ✅

**Script 10 - `10_finalize_dataset_images.py`**

* Creates the canonical `images/` directory
* Normalizes filenames to `page_{index:04d}.png`
* Bridges the gap between internal `derived` names and public schema

**Validation – `validate_engbench.py`**

* Checks:
  * All required fields are present (per `SCHEMA.md`)
  * Bboxes are well-formed (`x_min < x_max`, `y_min < y_max`)
  * Every pair has ≥1 question; every microtext item is referenced
* Result: current microtext JSONLs pass all checks

### Documentation

* **`BENCHMARK_RELEASE.md`** - **Start Here**. Usage guide, splits, and stats.
* **`SCHEMA.md`** - Canonical JSONL spec for all four output files
* **`CVAT_TAXONOMY.md`** - Label set + attributes, including critical `change_id`
* **`next_steps.md`** - End-to-end workflow from CVAT → TraceRAG

### Key Insight: `change_id` Drives Pairing

In CVAT, **`change_id` is the key that binds the pipeline together**:

* Each logical change gets a unique `change_id` (e.g., `"change_047"`)
* The old and new boxes for that change **both** carry this `change_id`
* Script 07 groups by (`pair_prefix`, `change_id`) to form one `visualdiff_pairs` entry per logical change

**Example:**

> R47 value change across revisions
> → old box + new box both annotated with `change_id = "change_047"`
> → Script 07 outputs a single `pair_id` with `bbox_old`, `bbox_new` and a generated question:
> *"How did resistor R47 change between Rev A5 and Rev C?"*

## Path to Eng_Bench v1 (Frozen)

### 1. CVAT Annotation (Human Phase)

* Use `CVAT_TAXONOMY.md`
* For each logical change, annotate one `old` box + one `new` box
* Set shared `change_id` for the pair

### 2. Export & Convert

**Visual-Diff:**
```bash
python tools/07_cvat_coco_to_engbench_jsonl.py \
  --coco-path visualdiff/annotations/coco/<project>.json \
  --split-file splits/visualdiff_train.txt \
  --out-pairs visualdiff/annotations/visualdiff_pairs.jsonl \
  --out-questions visualdiff/annotations/visualdiff_questions.jsonl
```

**Microtext:**
```bash
python tools/09_build_microtext_engbench.py \
  --seed-path microtext/annotations/tolerance_values.jsonl \
  --doc-id tolerances_table_iso \
  --version-id iso \
  --category tolerance_value \
  --out-items microtext/annotations/microtext_items.jsonl \
  --out-questions microtext/annotations/microtext_questions.jsonl
```

### 3. Validate

```bash
python tools/validate_engbench.py \
  --visualdiff-pairs visualdiff/annotations/visualdiff_pairs.jsonl \
  --visualdiff-questions visualdiff/annotations/visualdiff_questions.jsonl \
  --microtext-items microtext/annotations/microtext_items.jsonl \
  --microtext-questions microtext/annotations/microtext_questions.jsonl
```

### 4. Integrate with TraceRAG

* Add simple loaders for the four JSONLs
* Plug into TraceRAG evaluation (visual-diff + microtext tasks)
* Start collecting baseline vs TraceRAG metrics

At this point, Eng_Bench v1 is reproducible, split-safe, and ready to be used as the primary evaluation suite for TraceRAG.

## Tools

* `01_render_pdf.py` - Render PDFs to PNGs at 300 DPI
* `02_extract_textlayer.py` - Extract embedded text layer
* `02b_convert_textlayer_to_jsonl.py` - Convert to JSONL format
* `03_align_pair_orb.py` - Align document pairs using ORB+RANSAC
* `04_diff_candidates_from_diffmap.py` - Generate candidate boxes from diffmaps
* `05_seed_diff_pairs_from_candidates_textlayer.py` - Generate seed annotations
* `06_make_cvat_coco_dataset_zip.py` - Create CVAT import ZIPs
* `07_cvat_coco_to_engbench_jsonl.py` - Convert CVAT exports to Eng_Bench format
* `08_microtext_seed_queries_from_textlayer.py` - Generate microtext seeds
* `09_build_microtext_engbench.py` - Build microtext JSONL files
* `10_finalize_dataset_images.py` - Canonical image organizer
* `validate_engbench.py` - Validate JSONL files

## License

[To be determined]
