# Eng_Bench v2.0 Global Gate Audit

- audit date label: `2026-09-08-github-sync-preflight`
- v2.0 global complete: `False`

## Gate Checklist

| Gate | Current | Target | Pass |
| --- | ---: | ---: | --- |
| total_rows | 4560 | 25000-50000 | `False` |
| gold_source_docs | 199 | 150 | `True` |
| release_safe_inventory_docs | 552 | 150 | `True` |
| visualdiff_revision_families | 43 | 30 | `True` |
| hidden_public_test_examples | 1600 | 5000 | `False` |
| baselines_or_external_submissions | 29 | 20 | `True` |
| leaderboard_infrastructure | 7 | 7 | `True` |
| human_agreement_audit | 0/185 complete | all sampled rows complete, release-safe, reference-hash linked, and agreement thresholds pass | `False` |
| paper_ready_provenance | 201/210 paper-ready | all active source documents release-ready with matching recorded SHA-256 | `False` |

## Split Counts

- dev: `932`
- test: `1613`
- train: `2015`

## Counted Baselines And Submissions

- `baseline:dev_prior_all_test`
- `baseline:dev_prior_visualdiff_test`
- `baseline:edge_diff_visualdiff_test`
- `baseline:largest_cc_diff_visualdiff_test`
- `baseline:metadata_template_test`
- `baseline:orb_residual_diff_visualdiff_test`
- `baseline:phase_diff_visualdiff_test`
- `baseline:simple_diff_visualdiff_all`
- `baseline:ssim_diff_visualdiff_test`
- `baseline:textlayer_center_span_microtext_test`
- `baseline:textlayer_diff_visualdiff_test`
- `baseline:textlayer_heuristic_microtext_test`
- `baseline:textlayer_page_frequency_microtext_test`
- `baseline:textlayer_smallest_span_microtext_test`
- `baseline:tile_zncc_diff_visualdiff_test`
- `baseline:train_dev_prior_all_test`
- `baseline:train_prior_all_test`
- `baseline:train_prior_microtext_test`
- `baseline:weak_center_test`
- `baseline:weak_full_page_test`
- `baseline:weak_grid_cell_0_test`
- `baseline:weak_grid_cell_1_test`
- `baseline:weak_grid_cell_2_test`
- `baseline:weak_grid_cell_3_test`
- `baseline:weak_grid_cell_4_test`
- `baseline:weak_grid_cell_5_test`
- `baseline:weak_grid_cell_6_test`
- `baseline:weak_grid_cell_7_test`
- `baseline:weak_grid_cell_8_test`

## Release Constraints

- microtext_category_balance: `pin share 58.84%; 8 category floors open` -> `pin_label <=45% and inherited Gold category minimums`; pass `False`
  - open category shortfalls: `{'component_value': 284, 'dimension_value': 264, 'equipment_tag': 132, 'instrument_tag': 238, 'pipe_line_tag': 130, 'process_label': 55, 'process_value': 131, 'tolerance_value': 148}`
- visualdiff_description_finality: `1482` -> `1574`; pass `False`
- active_auditor_return_holds: `49` -> `0 unresolved active audit flags`; pass `False`

## Leaderboard Infrastructure

- make_public_private_split_tool: `True`
- submission_validator: `True`
- leaderboard_registration_tool: `True`
- leaderboard_protocol_doc: `True`
- leaderboard_dir: `True`
- hidden_test_inputs: `True`
- hidden_private_labels: `True`

## Machine Certification

- status: `not_supplied`
- auto-eligible pending calibration: `0`
- balance-closing non-pin rows: `0`
- deferred pin rows: `0`
- calibration rows total: `0`
- calibration rows completed by reusable human evidence: `0`
- calibration rows remaining: `0`
- calibration reuse evidence: `not_supplied`
- net row-by-row human decisions avoided: `0`
- active Gold modified: `False`

## Machine-First Responsibility

- status: `not_supplied`
- historically calibrated rows: `0`
- non-pin-only calibration rows: `0`
- calibrated-lane row decisions avoided: `0`
- source-intake row decisions avoided: `0`
- total machine row decisions avoided: `0`
- strict-unique balance-deferred rows: `0`

## Interpretation

v2.0 is a scale, trust, and adoption gate. Machine certification can remove repetitive train-MicroText work, but it does not count before calibration and strict promotion. Evaluation labels, agreement, source/test scale, provenance, and semantic review remain human- or release-gated.
