# Eng_Bench Paper Tables

Snapshot: `2026-08-29-wave960-post-wsdot-ds2-migration`  
Target: **Gold v2.0 Global**  
Claim status: **NOT RELEASE CLAIM READY**

> Active Gold and staged capacity are reported separately. Staged rows are never paper results.

## Active Dataset Statistics

| Measure | Count |
| --- | ---: |
| Total rows | 4507 |
| Task: microtext | 2946 |
| Task: visualdiff | 1561 |
| Split: dev | 896 |
| Split: test | 1600 |
| Split: train | 2011 |

## Task by Split

| Task | Train | Dev | Test | Total |
| --- | ---: | ---: | ---: | ---: |
| microtext | 1460 | 848 | 638 | 2946 |
| visualdiff | 551 | 48 | 962 | 1561 |

## Active MicroText Categories

| Category | Rows |
| --- | ---: |
| component_value | 15 |
| dimension_value | 616 |
| equipment_tag | 165 |
| instrument_tag | 62 |
| pin_label | 1741 |
| pipe_line_tag | 20 |
| process_label | 95 |
| process_value | 19 |
| room_label | 211 |
| tolerance_value | 2 |

## Active VisualDiff Change Types

| Change type | Mentions |
| --- | ---: |
| addition | 113 |
| deletion | 52 |
| layout | 20 |
| symbol | 641 |
| text | 776 |
| unknown | 26 |
| value | 12 |

## Active Source Domains

| Domain | Source docs | Paper-ready docs | Active row references |
| --- | ---: | ---: | ---: |
| civil_architectural | 28 | 26 | 619 |
| civil_hydraulic | 2 | 2 | 6 |
| civil_structural | 4 | 4 | 9 |
| datasheet_spec | 1 | 1 | 6 |
| mechanical | 9 | 9 | 27 |
| mechanical_cad | 12 | 12 | 207 |
| pcb_schematic | 96 | 91 | 4911 |
| pid | 36 | 34 | 283 |

## Formal Gate Status (5/9)

| Gate | Current | Target | Status |
| --- | --- | --- | --- |
| baselines_or_external_submissions | 29 | 20 | PASS |
| gold_source_docs | 177 | 150 | PASS |
| hidden_public_test_examples | 1600 | 5000 | OPEN |
| human_agreement_audit | 0/185 complete | all sampled rows complete, release-safe, reference-hash linked, and agreement thresholds pass | OPEN |
| leaderboard_infrastructure | 7 | 7 | PASS |
| paper_ready_provenance | 179/188 paper-ready | all active source documents release-ready with matching recorded SHA-256 | OPEN |
| release_safe_inventory_docs | 513 | 150 | PASS |
| total_rows | 4507 | 25000-50000 | OPEN |
| visualdiff_revision_families | 33 | 30 | PASS |

## Counted Baselines

| Baseline | Task | Rows | MicroText NEM | VisualDiff description F1 | VisualDiff evidence R@0.5 |
| --- | --- | ---: | ---: | ---: | ---: |
| dev_prior_all_test | all | 1600 | 0.0031 | 0.0532 | 0.0000 |
| dev_prior_visualdiff_test | visualdiff | 962 | - | 0.0532 | 0.0000 |
| edge_diff_visualdiff_test | visualdiff | 962 | - | 0.0332 | 0.0021 |
| largest_cc_diff_visualdiff_test | visualdiff | 962 | - | 0.0357 | 0.0073 |
| metadata_template_test | all | 1600 | 0.0000 | 0.0358 | 0.0000 |
| orb_residual_diff_visualdiff_test | visualdiff | 962 | - | 0.0227 | 0.0000 |
| phase_diff_visualdiff_test | visualdiff | 962 | - | 0.0000 | 0.0042 |
| simple_diff_visualdiff_all | visualdiff | 1561 | - | 0.0147 | 0.0026 |
| ssim_diff_visualdiff_test | visualdiff | 962 | - | 0.0332 | 0.0031 |
| textlayer_center_span_microtext_test | microtext | 638 | 0.0031 | - | - |
| textlayer_diff_visualdiff_test | visualdiff | 962 | - | 0.1444 | 0.0052 |
| textlayer_heuristic_microtext_test | microtext | 638 | 0.0298 | - | - |
| textlayer_page_frequency_microtext_test | microtext | 638 | 0.0204 | - | - |
| textlayer_smallest_span_microtext_test | microtext | 638 | 0.0063 | - | - |
| tile_zncc_diff_visualdiff_test | visualdiff | 962 | - | 0.0521 | 0.0000 |
| train_dev_prior_all_test | all | 1600 | 0.0031 | 0.0034 | 0.0000 |
| train_prior_all_test | all | 1600 | 0.0000 | 0.0000 | 0.0000 |
| train_prior_microtext_test | microtext | 638 | 0.0000 | - | - |
| weak_center_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_full_page_test | all | 1600 | 0.0000 | 0.0308 | 0.0021 |
| weak_grid_cell_0_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_1_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_2_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_3_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_4_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_5_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_6_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_7_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |
| weak_grid_cell_8_test | all | 1600 | 0.0000 | 0.0308 | 0.0000 |

## Staged Capacity Projection (Not Gold)

| Gate | Active | Staged upper bound | Target | Can close if accepted |
| --- | ---: | ---: | ---: | --- |
| Rows | 3745 | 33589 | 25000 | True |
| Source payloads | 49 | 379 | 150 | True |
| VisualDiff families | 12 | 46 | 30 | True |
| Test rows | 1230 | 5198 | 5000 | True |

Canonical non-pin rows still needed for the balance-compliant row target: `3407`.

## External Benchmark Comparison

Scale is contextual only. DocVQA is image-based document QA, whereas HotpotQA and MuSiQue are text multi-hop QA; none is task-equivalent to Eng_Bench VisualDiff.

| Dataset | Modality | Primary task | Reported scale | Source units | Supervision | Source |
| --- | --- | --- | --- | --- | --- | --- |
| Eng_Bench (active Gold) | engineering document images | MicroText extraction and engineering VisualDiff | 4,507 rows | 188 active source documents; 177 canonical paper-ready payloads | region evidence with text/category or visible-change labels | Audited Eng_Bench snapshot |
| DocVQA | document images | single-document extractive visual question answering | 50,000 questions | 12,767 document images | question, answer variants, and document image | [Document Visual Question Answering Challenge 2020](https://arxiv.org/abs/2008.08899) |
| HotpotQA | Wikipedia text | explainable multi-document multi-hop question answering | 113k question-answer pairs | multiple supporting Wikipedia documents per question | answers and sentence-level supporting facts | [HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering](https://arxiv.org/abs/1809.09600) |
| MuSiQue | multi-paragraph text | connected 2-4 hop question answering | 25K answerable questions | composed multi-paragraph contexts | answers, supporting paragraphs, and constituent question structure | [MuSiQue: Multihop Questions via Single-hop Question Composition](https://aclanthology.org/2022.tacl-1.31/) |

## Reproducibility Inputs

| Input | SHA-256 |
| --- | --- |
| `eng_bench.jsonl` | `3E720037C56C67216C5DA167F59853711E685E2DA275B083202D92DCDDFA2F7B` |
| `derived/quality/v2_0_gate_audit_2026-08-29-wave960-post-wsdot-ds2-migration.json` | `A9824B33AE6A27BA83E1D8F276BDD6205F7E0AB696CE69D367A5DEA75F1FF8A9` |
| `derived/quality/v2_0_staged_capacity_2026-08-29-wave902-full-canonical-plus-wave888-nasa-split.json` | `A45EDBA57CA5E840B23FDCD97CB11C107DB7EFC6A5FCA6469123F059D922FC27` |
| `derived/quality/v2_0_active_gold_provenance_2026-08-29-wave958.json` | `D0DFDFC249552CE1BD1BBFD018542A5332B2DD2679763CD89974FEE1C80A6B57` |
| `paper/data/benchmark_comparison_sources.json` | `45CC1212ABE2A127FFB2755C653AC83C34415B4277FB4D3065A39E5BB6D5B88F` |
