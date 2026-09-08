# Baseline Coverage Audit

- Split: `test`
- Required baselines per row: `1`
- Gate passed: `False`
- Gold rows checked: `1230`
- Baseline prediction files: `29`
- Failing rows: `199`
- Rows by task: `{'microtext': 359, 'visualdiff': 871}`
- Rows by coverage: `{'0': 199, '1': 636, '3': 13, '20': 153, '24': 229}`

## Prediction Files

- `results\baselines\dev_prior_all_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\dev_prior_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\edge_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\largest_cc_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\metadata_template_test_predictions.jsonl`: `395` rows, `0` outside split
- `results\baselines\orb_residual_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\phase_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\simple_diff_visualdiff_all_predictions.jsonl`: `1391` rows, `526` outside split
- `results\baselines\ssim_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\textlayer_center_span_microtext_test_predictions.jsonl`: `153` rows, `0` outside split
- `results\baselines\textlayer_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\textlayer_heuristic_microtext_test_predictions.jsonl`: `153` rows, `0` outside split
- `results\baselines\textlayer_page_frequency_microtext_test_predictions.jsonl`: `153` rows, `0` outside split
- `results\baselines\textlayer_smallest_span_microtext_test_predictions.jsonl`: `153` rows, `0` outside split
- `results\baselines\tile_zncc_diff_visualdiff_test_predictions.jsonl`: `229` rows, `0` outside split
- `results\baselines\train_dev_prior_all_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\train_prior_all_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\train_prior_microtext_test_predictions.jsonl`: `153` rows, `0` outside split
- `results\baselines\weak_center_test_predictions.jsonl`: `395` rows, `0` outside split
- `results\baselines\weak_full_page_test_predictions.jsonl`: `395` rows, `0` outside split
- `results\baselines\weak_grid_cell_0_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_1_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_2_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_3_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_4_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_5_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_6_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_7_test_predictions.jsonl`: `382` rows, `0` outside split
- `results\baselines\weak_grid_cell_8_test_predictions.jsonl`: `382` rows, `0` outside split

## Failing Examples

- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000082` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000083` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000084` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000085` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000086` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000087` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000088` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000089` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000090` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0072__000091` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000092` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000093` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000094` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000095` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000096` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000097` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000098` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000099` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000100` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000101` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000102` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000103` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000104` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000105` task `microtext` coverage `0` from []
- `q_mt__book_of_house_plans_buttrich__buttrich__p0084__000106` task `microtext` coverage `0` from []
