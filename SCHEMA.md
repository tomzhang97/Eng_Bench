# Eng_Bench JSONL Schema Specification

## Overview

Eng_Bench uses four canonical JSONL files for publication-ready datasets:

1. **visualdiff_pairs.jsonl** - Ground-truth change pairs
2. **visualdiff_questions.jsonl** - Q/A layer over pairs
3. **microtext_items.jsonl** - Ground-truth text regions
4. **microtext_questions.jsonl** - Q/A layer over items

Each line is one JSON object following the schemas below.

## 1. visualdiff_pairs.jsonl

Ground-truth units of one logical change across two revisions.

**Required fields:**
- `pair_id`, `doc_id`, `version_id_old/new`
- `page_index_old/new`, `page_id_old/new`
- `bbox_old`, `bbox_new` (format: `[x_min, y_min, x_max, y_max]`)
- `change_type` (non-empty array)
- `change_desc_gt`
- `split` (`"train"` | `"dev"` | `"test"`)

**Optional fields:**
- `project_id`, `board_id`, `eco_id`
- `object_id_old/new`, `entity_id`, `entity_kind`
- `severity`, `is_titleblock`, `notes`, `source`

## 2. visualdiff_questions.jsonl

Q/A layer over pairs. One pair can have multiple questions.

**Required fields:**
- `question_id`, `pair_id`
- `query_text`, `answer_text`
- `split`

**Optional fields:**
- `doc_id`, `version_id_old/new`
- `answer_type`, `requires_connectivity`, `requires_position`

## 3. microtext_items.jsonl

Each item is one tiny text/tolerance region with ground-truth bbox + text.

**Required fields:**
- `item_id`, `doc_id`, `version_id`
- `page_index`, `page_id`
- `bbox` (format: `[x_min, y_min, x_max, y_max]`)
- `text_gt`, `category`
- `split`

**Optional fields:**
- `board_id`, `object_id`
- `font_height_px`, `dimension_name`, `notes`

## 4. microtext_questions.jsonl

Q/A layer over microtext items.

**Required fields:**
- `question_id`, `item_ids` (non-empty array)
- `query_text`, `answer_text`
- `split`

**Optional fields:**
- `doc_id`, `version_id`, `answer_type`

## Scripts

### Convert CVAT COCO → Eng_Bench
```bash
python tools/07_cvat_coco_to_engbench_jsonl.py \
  --coco-path visualdiff/annotations/coco/viola_v1.0_to_v1.1.json \
  --split-file splits/visualdiff_test.txt \
  --out-pairs visualdiff/annotations/visualdiff_pairs.jsonl \
  --out-questions visualdiff/annotations/visualdiff_questions.jsonl
```

### Build Microtext Eng_Bench
```bash
python tools/09_build_microtext_engbench.py \
  --seed-path microtext/annotations/tolerance_values.jsonl \
  --doc-id tolerances_table_iso \
  --version-id iso \
  --category tolerance_value \
  --out-items microtext/annotations/microtext_items.jsonl \
  --out-questions microtext/annotations/microtext_questions.jsonl
```
