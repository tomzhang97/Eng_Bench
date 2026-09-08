# Eng_Bench Question Templates

Question wording is deterministic and semantically equivalent within each answer type. The goal is to reduce template memorization without changing answers, evidence, splits, or source labels.

## Current Status

- Template assignment tool: `tools/apply_question_templates.py`
- Diversity audit tool: `tools/question_diversity_report.py`
- Latest report: `derived/quality/question_diversity_report.md`
- Snapshot before rewrite: `derived/snapshots/2026-05-15-before-question-templates/`

Latest diversity gate:

| Task | Unique Templates | Max Template Share |
| --- | ---: | ---: |
| microtext | 46 | 8.76% |
| visualdiff | 23 | 8.28% |

## Rules

- Template selection is deterministic from question ID.
- Microtext templates are selected by item category.
- Visualdiff templates are selected from local-change wording using old/new version IDs.
- Templates must not add requirements that are not already present in the row.
- Templates must not reveal the answer.
- Answer text, evidence boxes, IDs, split, provenance, and review status remain unchanged.

## Commands

```powershell
python tools\apply_question_templates.py --root .
python tools\unify_dataset.py
python tools\question_diversity_report.py --root . --input eng_bench.jsonl
python tools\validate_engbench_v2.py --root . --input eng_bench.jsonl --manifest manifest.jsonl --skip-textlayer
```
