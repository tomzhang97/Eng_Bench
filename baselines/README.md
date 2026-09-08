# Eng_Bench Baselines

Baseline models for Eng_Bench evaluation tasks.

Use `tools/benchmark_runner.py` for the canonical scoring path. Generated
baseline reports belong under `results/baselines/`; see
`results/baselines/README.md` for the current v1.0 baseline inventory and
counting rules.

## Weak Heuristic Baselines

**Script**: `weak_heuristic_baselines.py`

**Approaches**:

- `center`: predicts a generic answer and center-page evidence boxes.
- `full_page`: predicts a generic answer and whole-page evidence boxes as a no-label localization negative control.
- `grid`: predicts a generic answer with one fixed grid-cell evidence box; use
  `--grid-cell` to generate distinct localization-prior controls.
- `metadata_template`: predicts simple category/change-type templates from public metadata.
- `label_prior`: learns majority answers from a specified calibration split, then predicts a target split without reading target labels.

**Usage**:

```powershell
python baselines\weak_heuristic_baselines.py `
  --root . `
  --input eng_bench.jsonl `
  --mode metadata_template `
  --split test `
  --task all `
  --output results\baselines\metadata_template_test_predictions.jsonl
```

These are deliberately weak lower-bound baselines. They help expose whether a model beats image-center, whole-page evidence, metadata-template, and label-prior shortcuts; they do not replace OCR, VLM, or TraceRAG baselines.

The `weak_grid_cell_0_test` through `weak_grid_cell_8_test` reports are a
3-by-3 localization-prior sweep over the public test split. They are counted as
distinct diagnostic baselines because their evidence boxes differ for every row;
they exist to make per-row baseline coverage audits robust, not as competitive
systems.

## Visual-Diff Baselines: Image Difference

**Script**: `simple_diff_baseline.py`

**Approaches**:

- `absolute`: localizes the strongest raw grayscale pixel-difference region.
- `edge`: localizes differences between Canny edge maps, providing a distinct structural-change baseline.
- `ssim`: localizes the lowest Gaussian-window structural-similarity region, a local-statistics alternative to raw differencing.
- `phase`: compensates global translation with phase correlation before differencing, so small registration offsets do not dominate the predicted region.
- `orb_residual`: localizes the densest cluster of new-image ORB keypoints with no cross-checked old-image match (evidence recall `0.0` on test - keypoint mismatch alone does not find document changes).
- `largest_cc`: localizes the largest connected component of the thresholded difference (evidence recall `0.0218`@IoU0.3).
- `tile_zncc`: localizes the tile with the lowest zero-normalized cross-correlation (evidence recall `0.0` on test).

**Usage**:

```powershell
python baselines\simple_diff_baseline.py `
  --root . `
  --input eng_bench.jsonl `
  --split test `
  --mode edge `
  --output results\baselines\edge_diff_visualdiff_test_predictions.jsonl
```

Score generated predictions through `tools/benchmark_runner.py`. The current
counted image-difference reports cover all `871` visualdiff test rows. Consult
`results/baselines/BASELINE_TABLE.md` for metrics from the current regenerated
test split rather than relying on historical row-count snapshots.

## Visual-Diff Baseline: Text-Layer Span Diff

**Script**: `textlayer_diff_visualdiff_baseline.py`

Diffs the exact embedded text layers of the old/new pages, localizes the
largest changed-span cluster on each side, and answers with a template naming
the removed/added texts. This exercises a different input modality than the
pixel baselines: it is strong on text-value changes and blind to purely
graphical edits. The current counted test report covers all `871` visualdiff
test rows; its metrics are recorded in `results/baselines/BASELINE_TABLE.md`.

```powershell
python baselines\textlayer_diff_visualdiff_baseline.py `
  --root . `
  --input eng_bench.jsonl `
  --split test `
  --output results\baselines\textlayer_diff_visualdiff_test_predictions.jsonl
```

## Microtext Baseline: Text-Layer Selection

**Script**: `textlayer_microtext_baseline.py`

**Approaches**:

- `category_heuristic` (default): scores page spans with category-specific
  regex/format heuristics and answers with the best span.
- `smallest_span`: size-prior control that always answers with the smallest
  readable span on the page, testing whether "microtext is the tiniest text"
  is an exploitable shortcut.
- `center_span`: location-prior control answering with the span nearest the
  page centroid.
- `page_frequency`: repetition-prior control answering with the most
  repeated text on the page.

The current counted microtext reports cover all `359` test rows. See
`results/baselines/BASELINE_TABLE.md` for current metrics.

```powershell
python baselines\textlayer_microtext_baseline.py `
  --root . `
  --input eng_bench.jsonl `
  --split test `
  --mode smallest_span `
  --output results\baselines\textlayer_smallest_span_microtext_test_predictions.jsonl
```

## Microtext Baseline: OCR

**Script**: `eval_microtext_baseline.py`

**Approach**: Tesseract OCR with 4x rotations (0, 90, 180, 270 degrees)

**Metric**: Character Error Rate (CER), normalized exact match, evidence IoU@0.5.

**Usage**:
```bash
python baselines/eval_microtext_baseline.py \
  --items microtext/annotations/microtext_items.jsonl \
  --pages-dir derived/pages_300dpi \
  --out-results baselines/results/microtext_ocr.jsonl
```

**TODO**:
- `tesseract_microtext_baseline.py` now emits unified prediction JSONL when a local `tesseract` executable is installed.
- In this workspace, the executable is not installed, so the report remains uncounted.
- Score its output with `tools/benchmark_runner.py` after generating `results/baselines/tesseract_microtext_test_predictions.jsonl`.

## Planned Visual-Diff Baseline: Siamese ResNet50

**Script**: `eval_visualdiff_baseline.py`

**Approach**: Siamese CNN with ResNet50 backbone for change detection

**Metric**: Evidence recall@IoU 0.3/0.5 and old/new side hit rate.

**Usage**:
```bash
python baselines/eval_visualdiff_baseline.py \
  --pairs visualdiff/annotations/visualdiff_pairs.jsonl \
  --pages-dir derived/pages_300dpi \
  --out-results baselines/results/visualdiff_siamese.jsonl
```

**TODO**:
- Implement Siamese architecture
- Add pretrained weights
- Implement change map generation
- Add bbox extraction from heatmap
- Emit unified prediction JSONL with image-indexed evidence boxes.

## Expected Results

These baselines are designed to **fail** on Eng_Bench, demonstrating the difficulty of the benchmark:

- **Microtext OCR**: Expected CER > 0.5 (poor recognition of tiny text)
- **Visual-Diff Siamese**: Expected IoU@0.5 < 0.3 (struggles with semantic changes)

This proves that Eng_Bench is challenging and motivates the need for TraceRAG.
