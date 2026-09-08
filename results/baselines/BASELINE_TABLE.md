# Eng_Bench Baseline Table

| Model | Task | Split | Rows | Missing | Norm EM | CER | Micro IoU@0.5 | VDiff Recall@0.3 | VDiff Recall@0.5 | Side Hit | Desc F1 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dev_prior_all_test | all | test | 1600 | 0 | 0.0031 | 0.9874 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0532 |
| metadata_template_test | all | test | 1600 | 0 | 0.0000 | 3.6038 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0358 |
| train_dev_prior_all_test | all | test | 1600 | 0 | 0.0031 | 0.9174 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0034 |
| train_prior_all_test | all | test | 1600 | 0 | 0.0000 | 0.9484 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0000 |
| weak_center_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0308 |
| weak_full_page_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0042 | 0.0021 | 0.0042 | 0.0308 |
| weak_grid_cell_0_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| weak_grid_cell_1_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| weak_grid_cell_2_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| weak_grid_cell_3_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| weak_grid_cell_4_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0021 | 0.0000 | 0.0021 | 0.0308 |
| weak_grid_cell_5_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| weak_grid_cell_6_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| weak_grid_cell_7_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0308 |
| weak_grid_cell_8_test | all | test | 1600 | 0 | 0.0000 | 1.5698 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0308 |
| textlayer_center_span_microtext_test | microtext | test | 638 | 0 | 0.0031 | 1.3272 | 0.0016 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| textlayer_heuristic_microtext_test | microtext | test | 638 | 0 | 0.0298 | 1.0632 | 0.0235 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| textlayer_page_frequency_microtext_test | microtext | test | 638 | 0 | 0.0204 | 1.0775 | 0.0031 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| textlayer_smallest_span_microtext_test | microtext | test | 638 | 0 | 0.0063 | 1.0027 | 0.0031 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| train_prior_microtext_test | microtext | test | 638 | 0 | 0.0000 | 0.9484 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| dev_prior_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0532 |
| edge_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0062 | 0.0021 | 0.0062 | 0.0332 |
| largest_cc_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0114 | 0.0073 | 0.0114 | 0.0357 |
| orb_residual_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0010 | 0.0000 | 0.0010 | 0.0227 |
| phase_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0104 | 0.0042 | 0.0104 | 0.0000 |
| simple_diff_visualdiff_all | visualdiff | all | 1561 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0070 | 0.0026 | 0.0070 | 0.0147 |
| ssim_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0062 | 0.0031 | 0.0062 | 0.0332 |
| textlayer_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0073 | 0.0052 | 0.0031 | 0.1444 |
| tile_zncc_diff_visualdiff_test | visualdiff | test | 962 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0521 |

Notes:
- Rows with fewer than 100 examples are diagnostic, not headline.
- Smoke and oracle reports are intentionally excluded from this table.
