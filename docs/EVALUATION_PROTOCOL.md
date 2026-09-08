# Eng_Bench Evaluation Protocol

This document defines the scoring surface required before Eng_Bench can be called v1.0 Gold.

## Prediction Schema

Every baseline or model submission should emit one JSONL row per benchmark question:

```json
{
  "id": "q_example",
  "answer": "predicted text or visual-diff description",
  "evidence": [{"image_index": 0, "bbox": [0, 0, 10, 10]}],
  "metadata": {"model": "baseline_name"}
}
```

The `id` must match `eng_bench.jsonl`. Evidence bboxes use pixel coordinates in the referenced image.

## Microtext Metrics

Required:

- Exact match.
- Normalized exact match.
- Character error rate.
- Evidence IoU@0.5 when evidence is predicted.
- Per-category accuracy.

Report categories with fewer than 100 dev/test examples as diagnostic, not headline.

## Visualdiff Metrics

Required:

- Evidence recall@IoU 0.3.
- Evidence recall@IoU 0.5.
- Old/new side hit rate.
- Change-type accuracy when predicted.
- Normalized description F1 or keyword recall as secondary only.

Description text is useful for qualitative comparison, but evidence localization and change-type correctness are the primary benchmark signals.

## Dataset Health Metrics

Every reported baseline table must include the dataset health snapshot used for scoring:

- missing image path count
- duplicate ID count
- source-family leakage count
- unresolved rights blocker count
- total rows and rows by split/task

Use:

```powershell
python tools\benchmark_health_report.py --root . --release-target v1.0
```

## Baseline Requirements

v1.0 Gold requires at least five systems:

- OCR-only text baseline.
- Tesseract or equivalent OCR baseline.
- Simple pixel/absdiff visualdiff baseline.
- Open-source VLM baseline.
- TraceRAG baseline.

Store predictions and reports under `results/baselines/`.

The Tesseract slot has a runnable script at
`baselines/tesseract_microtext_baseline.py`, but it remains uncounted in this
workspace because the `tesseract` executable is not installed locally.

## Report Format

Each baseline should produce:

- `results/baselines/{system}_{split}_predictions.jsonl`
- `results/baselines/{system}_{split}_report.json`
- `results/baselines/{system}_{split}_report.md`

The Markdown report should be directly reusable in a paper appendix or release note.
Current reports include per-category microtext metrics, per-domain metric summaries,
and optional bootstrap confidence intervals for headline metrics.

Use the unified scorer:

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

Smoke/oracle reports can be used to debug the evaluator, but they do not count
toward the v1.0 five-baseline gate. Counted baseline artifacts must come from a
real system and should not include `smoke` or `oracle` in the filename.
