# Eng_Bench: Engineering Document Benchmark

> **[SEE v0.9 SILVER RELEASE NOTES](BENCHMARK_RELEASE.md)**  
> Comparison stats, loading snippets, and split details are in `BENCHMARK_RELEASE.md`.

A comprehensive benchmark for evaluating vision-language models on engineering document understanding tasks, including visual-diff detection and microtext recognition.

## Quick Start

### Dataset Statistics

| Property             | Value |
| -------------------- | ----- |
| #active source docs  | 307   |
| #referenced images   | 652   |
| #questions           | 5694  |
| % micro-text         | 72.0% |
| % multi-page         | 0%    |
| % revision           | 28.0% |
| avg evidence regions | 1.28  |

Current active data contains 1,596 VisualDiff and 4,098 MicroText rows. The
unified split is train 2,789, dev 1,121, and test 1,784. The public GitHub snapshot
is deliberately narrower: it keeps labeled train/dev rows only when every
source payload is paper-ready and release-ready, and strips all answer and
evidence fields from test rows. See
[`release/public_dataset/`](release/public_dataset/) and
[`PUBLIC_REPOSITORY_POLICY.md`](PUBLIC_REPOSITORY_POLICY.md).

Latest verified checkpoint (2026-09-09): active Gold remains 5,694 rows after
the 1,112-row primary-reviewed MicroText promotion. A source-identity-aware
provenance plan now provides exact one-for-one capacity for all 1,430 rows tied
to nine blocked active documents. The plan is structurally ready, with 1,171
replacement reviews reusable and 259 still outstanding; no replacement has
been applied. Legacy and strict-v2 validation pass with zero missing images,
split leakage, question leakage, provenance regression, or source-hash
mismatches. Gold v2.0 Global remains 5/9 gates PASS: total scale, frozen
challenge scale, formal agreement, and complete provenance are still open. See
[`BENCHMARK_RELEASE.md`](BENCHMARK_RELEASE.md) for the exact gate counts.
All nine blocked active documents now have dated, authoritative-policy,
hash-bound rights decisions covering 2,333 active source references. Every
decision remains a hold, so this evidence closes ambiguity rather than the
provenance gate. Wave2356 is the current formal gate report.
The post-promotion machine audit reuses 25 exact calibration decisions and
identifies 799 non-pin train MicroText rows that pass every strict forecast gate
but remain outside Gold until the remaining 275-row calibration is complete.
The current primary-review handoff is the verified flat 533-action ZIP recorded
in `AUDITOR_RETURN_STATUS.md`; it supersedes the earlier 288-action package.
Four additional release-safe NASA engineering-system sources are now imported
as review-only capacity. Eleven selected turbine-test, vacuum-system, silicon-
flowsheet, and connected BCL silicon-process P&ID pages produced 298 OCR
candidates; machine visual curation retained 84 complete engineering labels
and held 214 fragments, stamps, title-block text, captions, overlaps, and
taxonomy mismatches. All 84 retained rows have complete crop evidence and zero
active-Gold, assignment-history, payload-alias, or near-region collision, but
remain outside Gold pending human review. The validated staged upper bound is
5,778 rows, not the current Gold count. The source-conversion readiness ledger
now recognizes all machine-held, duplicate-held, superseded, and explicitly
excluded review rows. This removes 39,201 stale or already resolved
representations from the apparent fresh queue, leaving 22,108 genuinely fresh
local candidates for further machine triage. This is a queue-quality correction,
not a Gold row increase.

### Unified Loader

Install locally:

```powershell
python -m pip install -e . --no-deps
```

Load rows:

```python
from engbench import load_eng_bench

rows = load_eng_bench(root=".", task="microtext", split="test", verify_images=True)
```

Smoke check:

```powershell
python tools\loader_smoke.py --root .
python tools\build_dataset_infos.py --root . --input eng_bench.jsonl --output dataset_infos.json
python tools\audit_question_leakage.py --root .
python tools\export_public_inputs.py --root . --split test
python tools\build_release_manifest.py --root .
```

### Directory Structure

```text
eng-bench/
|-- images/                      # Canonical 300 DPI images
|-- visualdiff/
|   |-- docs/                    # Source PDFs
|   `-- annotations/
|       |-- visualdiff_pairs.jsonl
|       `-- visualdiff_questions.jsonl
|-- microtext/
|   |-- docs/                    # Source PDFs/images
|   `-- annotations/
|       |-- microtext_items.jsonl
|       `-- microtext_questions.jsonl
|-- derived/                     # Intermediate build and QA artifacts
|-- results/                     # Health reports and baseline reports
|-- splits/
|-- tools/                       # Pipeline scripts
|-- dataset_infos.json           # HF-style metadata
|-- BENCHMARK_RELEASE.md         # Release notes and usage
|-- SCHEMA.md                    # JSONL specification
`-- CVAT_TAXONOMY.md             # Annotation guide
```

## Infrastructure Complete

### Core Converters

**Script 07 v2 - `07_cvat_coco_to_engbench_jsonl.py`**

* Parses CVAT COCO exports
* Groups annotations by `change_id` to pair old/new boxes into logical changes
* Maps CVAT labels/attributes to Eng_Bench `change_type`, `severity`, `is_titleblock`, etc.
* Outputs:
  * `visualdiff_pairs.jsonl`
  * `visualdiff_questions.jsonl`
* Validated:
  * Filename parsing (doc, version_old/new, page, role)
  * COCO bbox to `[x_min, y_min, x_max, y_max]`
  * `change_type` mapping consistent with `CVAT_TAXONOMY.md`

**Script 09 - `09_build_microtext_engbench.py`**

* Converts microtext/tolerance seeds to canonical `microtext_items.jsonl`
* Generates Q/A with category-specific templates to `microtext_questions.jsonl`
* Current Track B pass: 1,290 reviewed items + 1,290 questions pass validation.

**Image finalization - `finalize_all_images.py`**

* Copies every active visualdiff/microtext page image into the canonical `images/` layout when it exists only under `derived/pages_300dpi`.
* Normalizes filenames to `page_{index:04d}.png`.
* Writes `results/health/image_finalization_report.json`.

**Validation - `validate_engbench.py`**

* Checks:
  * All required fields are present (per `SCHEMA.md`)
  * Bboxes are well-formed (`x_min < x_max`, `y_min < y_max`)
  * Every pair has at least 1 question; every microtext item is referenced
* Result: current microtext JSONLs pass all checks

### Documentation

* **`BENCHMARK_RELEASE.md`** - **Start Here**. Usage guide, splits, and stats.
* **`SCHEMA.md`** - Canonical JSONL spec for all four output files
* **`CVAT_TAXONOMY.md`** - Label set + attributes, including critical `change_id`
* **`next_steps.md`** - End-to-end workflow from CVAT to TraceRAG

### Key Insight: `change_id` Drives Pairing

In CVAT, **`change_id` is the key that binds the pipeline together**:

* Each logical change gets a unique `change_id` (e.g., `"change_047"`)
* The old and new boxes for that change **both** carry this `change_id`
* Script 07 groups by (`pair_prefix`, `change_id`) to form one `visualdiff_pairs` entry per logical change

**Example:**

> R47 value change across revisions
> old box + new box both annotated with `change_id = "change_047"`
> Script 07 outputs a single `pair_id` with `bbox_old`, `bbox_new` and a generated question:
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
* Continue collecting baseline vs TraceRAG metrics

At this point, the visualdiff slice is family-split for Track B work and the microtext slice has crossed the 1,000-row volume gate with a frozen document-disjoint train/dev/test assignment. The latest safe expansions raise the USDA dairy/facility-plan train slice to 100 reviewed rows, the WSDOT civil dev slice to 75 reviewed dimension rows, the Peel Region P&ID dev slice to 35 reviewed equipment/instrument labels, and the Wikimedia Commons P&ID train slice to 35 reviewed equipment/instrument/line/process crops. The main remaining dataset-shaping work is to add more release-safe full P&ID/process sheets so split-level category metrics are less pin-label heavy.

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
* `10_finalize_dataset_images.py` - Legacy canonical image organizer for seed sources
* `finalize_all_images.py` - Active dataset image finalizer and packaging check
* `import_image_pages.py` - Normalize raster source sheets into `derived/pages_300dpi/`
* `propose_microtext_regions.py` - Propose OCR-less raster microtext crop regions for review
* `benchmark_health_report.py` - v0.95/v1.0 release-gate health report
* `benchmark_runner.py` - Unified prediction scoring for visualdiff and microtext baselines
* `build_baseline_table.py` - Aggregates counted baseline reports
* `build_dataset_infos.py` - Writes HF-style dataset metadata to `dataset_infos.json`
* `build_release_manifest.py` - Writes hash/size manifests for release-critical files
* `build_v1_expansion_queue.py` - Ranks release-safe source candidates for v1.0 expansion
* `apply_visualdiff_polish.py` - Applies filled visualdiff human-polish checklists and quarantines unclear rows
* `microtext_review_checklist.py` - Converts microtext review JSONL to and from human-fillable CSV checklists
* `review_packet_status.py` - Summarizes completion and issues in human handoff CSV packets
* `apply_question_templates.py` - Applies deterministic question wording diversity without changing labels
* `question_diversity_report.py` - Audits repeated question templates
* `audit_question_leakage.py` - Audits prompts for accidental answer leakage
* `export_public_inputs.py` - Exports dev/test JSONL inputs with labels removed
* `loader_smoke.py` - Verifies the public unified loader and image paths
* `validate_engbench.py` - Validate JSONL files

## License

Provisional mixed-source candidate. See `DATACARD.md`, `SOURCE_INVENTORY.csv`, and `manifest.jsonl` before public redistribution.
