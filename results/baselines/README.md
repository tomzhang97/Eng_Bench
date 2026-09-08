# Eng_Bench Baseline Results

This directory stores baseline prediction reports used by the v1.0 gate.
It also includes the counted v1.5/v2.0 diagnostic baseline set.

Counting rule:

- Counted: non-smoke `*_report.json` and `*_predictions.jsonl` files from real baseline systems.
- Not counted: oracle, smoke, schema-only, or README files.

Current counted artifacts:

| Baseline | Task | Report | Notes |
| --- | --- | --- | --- |
| `simple_diff` | visualdiff | `simple_diff_visualdiff_all_report.json` | Absolute image-difference baseline; full 1,454-row visualdiff coverage; 200 bootstrap samples. |
| `edge_diff` | visualdiff | `edge_diff_visualdiff_test_report.json` | Canny edge-map-difference baseline; full 871-row visualdiff test coverage; 200 bootstrap samples. |
| `textlayer_heuristic` | microtext | `textlayer_heuristic_microtext_test_report.json` | Text-layer/category heuristic baseline; full 359-row microtext test coverage; 200 bootstrap samples. |
| `weak_center` | all | `weak_center_test_report.json` | Image-center sanity baseline using no labels; full 1,230-row test coverage; 200 bootstrap samples. |
| `weak_full_page` | all | `weak_full_page_test_report.json` | Whole-page evidence negative-control baseline using no labels; full 1,230-row test coverage; 200 bootstrap samples. |
| `metadata_template` | all | `metadata_template_test_report.json` | Public-metadata template baseline; full 1,230-row test coverage; 200 bootstrap samples. |
| `train_prior_microtext` | microtext | `train_prior_microtext_test_report.json` | Train-split label-prior baseline for microtext only; full 359-row microtext test coverage. |
| `dev_prior_visualdiff` | visualdiff | `dev_prior_visualdiff_test_report.json` | Dev-split label-prior baseline for visualdiff only; full 871-row visualdiff test coverage. |
| `train_prior_all` | all | `train_prior_all_test_report.json` | Train-split label-prior baseline for all test rows; full 1,230-row test coverage; 200 bootstrap samples. |
| `dev_prior_all` | all | `dev_prior_all_test_report.json` | Dev-split label-prior baseline for all test rows; full 1,230-row test coverage; 200 bootstrap samples. |
| `train_dev_prior_all` | all | `train_dev_prior_all_test_report.json` | Train+dev label-prior baseline for all test rows; full 1,230-row test coverage; 200 bootstrap samples. |
| `weak_grid_cell_0..8` | all | `weak_grid_cell_{0..8}_test_report.json` | 3-by-3 fixed-grid localization-prior sweep; full 1,230-row test coverage for each grid cell. |

Required v1.0 baseline set:

| Slot | Status | Output target |
| --- | --- | --- |
| Simple pixel/absdiff visualdiff | Complete baseline pass | `simple_diff_visualdiff_all_report.*` |
| Structural edge-difference visualdiff | Complete baseline pass | `edge_diff_visualdiff_test_report.*` |
| OCR-only text baseline | Started via text-layer heuristic | `textlayer_heuristic_microtext_test_report.*` |
| Tesseract or equivalent OCR baseline | Script ready; local executable missing in this environment | `tesseract_microtext_test_report.*` |
| Weak no-label sanity baselines | Complete baseline pass | `weak_center_test_report.*`, `weak_full_page_test_report.*`, `metadata_template_test_report.*` |
| Label-prior calibration baselines | Complete baseline pass | `train_prior_microtext_test_report.*`, `dev_prior_visualdiff_test_report.*`, `train_prior_all_test_report.*`, `dev_prior_all_test_report.*`, `train_dev_prior_all_test_report.*` |
| Open-source VLM baseline | Missing | `open_vlm_devtest_report.*` |
| TraceRAG baseline | Missing | `tracerag_devtest_report.*` |

The weak and prior baselines are counted as real lower-bound systems, but they are not substitutes for open-source VLM, OCR, or TraceRAG reports.

Summary table:

- `BASELINE_TABLE.md`

Coverage audit:

- `results/baselines/baseline_coverage_audit_2026-07-31-post-return-final.md`
- Current strict test split coverage: all `1,230` test rows meet the `>=20`
  diagnostic condition; microtext rows have `20` counted predictions and
  visualdiff rows have `24`.
- v2.0 baseline/submission gate: `29/20` counted reports as of the
  `2026-07-31-post-return-final` gate audit.

Use the unified runner:

```powershell
python tools\benchmark_runner.py `
  --gt eng_bench.jsonl `
  --pred results\baselines\{system}_{split}_predictions.jsonl `
  --split test `
  --model-name {system} `
  --report-json results\baselines\{system}_{split}_report.json `
  --report-md results\baselines\{system}_{split}_report.md `
  --bootstrap-samples 200
```
