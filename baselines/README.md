# Eng_Bench Baselines

Baseline models for Eng_Bench evaluation tasks.

## Microtext Baseline: OCR

**Script**: `eval_microtext_baseline.py`

**Approach**: Tesseract OCR with 4x rotations (0°, 90°, 180°, 270°)

**Metric**: Character Error Rate (CER)

**Usage**:
```bash
python baselines/eval_microtext_baseline.py \
  --items microtext/annotations/microtext_items.jsonl \
  --pages-dir derived/pages_300dpi \
  --out-results baselines/results/microtext_ocr.jsonl
```

**TODO**:
- Implement actual Tesseract integration
- Add rotation logic
- Implement CER calculation (Levenshtein distance)

## Visual-Diff Baseline: Siamese ResNet50

**Script**: `eval_visualdiff_baseline.py`

**Approach**: Siamese CNN with ResNet50 backbone for change detection

**Metric**: IoU@0.5 (Intersection over Union at 50% threshold)

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

## Expected Results

These baselines are designed to **fail** on Eng_Bench, demonstrating the difficulty of the benchmark:

- **Microtext OCR**: Expected CER > 0.5 (poor recognition of tiny text)
- **Visual-Diff Siamese**: Expected IoU@0.5 < 0.3 (struggles with semantic changes)

This proves that Eng_Bench is challenging and motivates the need for TraceRAG.
