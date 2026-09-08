# Eng_Bench Baseline Report

- Model: dev_prior_visualdiff_test
- Split: test
- Task filter: visualdiff
- Rows scored: 962
- Missing predictions: 0
- Duplicate prediction IDs: 0
- Rows by task: {'visualdiff': 962}

## Microtext

| Metric | Value |
| --- | ---: |
| Total | 0 |
| Exact match | 0.0000 |
| Normalized exact match | 0.0000 |
| Character error rate | 0.0000 |
| Evidence IoU@0.5 | 0.0000 |

## Visualdiff

| Metric | Value |
| --- | ---: |
| Total | 962 |
| Evidence recall@IoU 0.3 | 0.0010 |
| Evidence recall@IoU 0.5 | 0.0000 |
| Old/new side hit rate | 0.0010 |
| Change-type accuracy | 0.0000 |
| Normalized description F1 | 0.0532 |

## Confidence Intervals

| Metric | Point | 95% CI Low | 95% CI High | Samples |
| --- | ---: | ---: | ---: | ---: |
| visualdiff.change_type_accuracy | 0.0000 | 0.0000 | 0.0000 | 200 |
| visualdiff.evidence_recall_iou_0_3 | 0.0010 | 0.0000 | 0.0042 | 200 |
| visualdiff.evidence_recall_iou_0_5 | 0.0000 | 0.0000 | 0.0000 | 200 |
| visualdiff.normalized_description_f1 | 0.0532 | 0.0483 | 0.0580 | 200 |
| visualdiff.old_new_side_hit_rate | 0.0010 | 0.0000 | 0.0042 | 200 |

## Domains

| Domain | Rows | Microtext | Visualdiff | Micro Norm EM | Micro CER | VDiff Recall@0.5 | Side Hit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| espressif_esp32_c5_devkitc_1 | 6 | 0 | 6 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| olimex_esp32_gateway | 13 | 0 | 13 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| olimex_esp32_poe | 7 | 0 | 7 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| sparkfun_micromod_artemis | 20 | 0 | 20 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| sparkfun_micromod_esp32 | 15 | 0 | 15 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| sparkfun_micromod_rp2040 | 25 | 0 | 25 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| sparkfun_micromod_samd51 | 2 | 0 | 2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| sparkfun_micromod_teensy | 9 | 0 | 9 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| viola | 865 | 0 | 865 | 0.0000 | 0.0000 | 0.0000 | 0.0012 |
