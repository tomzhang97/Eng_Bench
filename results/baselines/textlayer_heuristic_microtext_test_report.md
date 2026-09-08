# Eng_Bench Baseline Report

- Model: textlayer_heuristic_microtext_test
- Split: test
- Task filter: microtext
- Rows scored: 638
- Missing predictions: 0
- Duplicate prediction IDs: 0
- Rows by task: {'microtext': 638}

## Microtext

| Metric | Value |
| --- | ---: |
| Total | 638 |
| Exact match | 0.0141 |
| Normalized exact match | 0.0298 |
| Character error rate | 1.0632 |
| Evidence IoU@0.5 | 0.0235 |

## Visualdiff

| Metric | Value |
| --- | ---: |
| Total | 0 |
| Evidence recall@IoU 0.3 | 0.0000 |
| Evidence recall@IoU 0.5 | 0.0000 |
| Old/new side hit rate | 0.0000 |
| Change-type accuracy | 0.0000 |
| Normalized description F1 | 0.0000 |

## Microtext Categories

| Category | Total | Exact | Normalized Exact |
| --- | ---: | ---: | ---: |
| component_value | 3 | 0.0000 | 0.0000 |
| dimension_value | 325 | 0.0000 | 0.0308 |
| equipment_tag | 47 | 0.0000 | 0.0000 |
| instrument_tag | 1 | 0.0000 | 0.0000 |
| pin_label | 47 | 0.0000 | 0.0000 |
| pipe_line_tag | 2 | 0.0000 | 0.0000 |
| process_label | 1 | 0.0000 | 0.0000 |
| room_label | 210 | 0.0429 | 0.0429 |
| tolerance_value | 2 | 0.0000 | 0.0000 |

## Confidence Intervals

| Metric | Point | 95% CI Low | 95% CI High | Samples |
| --- | ---: | ---: | ---: | ---: |
| microtext.character_error_rate | 1.0632 | 1.0377 | 1.0900 | 200 |
| microtext.evidence_iou_0_5 | 0.0235 | 0.0110 | 0.0345 | 200 |
| microtext.normalized_exact_match | 0.0298 | 0.0172 | 0.0408 | 200 |

## Domains

| Domain | Rows | Microtext | Visualdiff | Micro Norm EM | Micro CER | VDiff Recall@0.5 | Side Hit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| adafruit_esp32_s3_tft_feather_pinout | 4 | 4 | 0 | 0.0000 | 1.1905 | 0.0000 | 0.0000 |
| adafruit_esp32_s3_tft_feather_sch | 1 | 1 | 0 | 0.0000 | 3.5000 | 0.0000 | 0.0000 |
| adafruit_feather_esp32_s3_pinout | 4 | 4 | 0 | 0.0000 | 1.0476 | 0.0000 | 0.0000 |
| adafruit_feather_esp32_s3_sch | 3 | 3 | 0 | 0.0000 | 2.1818 | 0.0000 | 0.0000 |
| adafruit_feather_m0_express_pinout | 4 | 4 | 0 | 0.0000 | 2.2222 | 0.0000 | 0.0000 |
| adafruit_feather_m0_express_sch | 4 | 4 | 0 | 0.0000 | 0.9474 | 0.0000 | 0.0000 |
| adafruit_feather_m4_express_pinout | 4 | 4 | 0 | 0.0000 | 3.0000 | 0.0000 | 0.0000 |
| adafruit_feather_m4_express_sch | 4 | 4 | 0 | 0.0000 | 1.4444 | 0.0000 | 0.0000 |
| adafruit_huzzah32_esp32_feather_pinout | 4 | 4 | 0 | 0.0000 | 1.4737 | 0.0000 | 0.0000 |
| adafruit_huzzah32_esp32_feather_sch | 4 | 4 | 0 | 0.0000 | 1.7059 | 0.0000 | 0.0000 |
| adafruit_itsybitsy_32u4_3v_2025 | 4 | 4 | 0 | 0.0000 | 2.2000 | 0.0000 | 0.0000 |
| adafruit_metro_m0_express_2019 | 4 | 4 | 0 | 0.0000 | 2.7273 | 0.0000 | 0.0000 |
| arch_012_1_13_third_floor_plan_second_floor_plan_first_floor_plan_grou | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_012_1_first_floor_plan_attic_floor_plan_second_floor_plan_section | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_012_1_first_floor_plan_second_floor_plan_basement_floor_plan_sout | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_012_3_evolution_1950_preliminary_first_floor_plan_1950_constructe | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_012_3_second_floor_plan_basement_floor_plan_observation_level_flo | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_013_2_lock_tender_s_shelter_south_elevation_north_elevation_east_ | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_013_two_and_three_bedroom_units_first_floor_plan_second_floor_pla | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_014_1_plan_section_a_a_section_b_b_section_c_c_gate_details_timbe | 4 | 4 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_014_cross_sections_no_1_hold_section_at_fr_24_looking_fwd_no_1_ho | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_014_roof_plans_section_c_c_roof_plan_roof_framing_plans_section_c | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_014_section_at_frame_195_section_at_frame_154_section_at_midship_ | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_014_section_at_frame_19_looking_forward_section_at_frame_25_looki | 2 | 2 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_016_1_statement_of_significance_location_plan_floor_plan_roof_pla | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_016_1_statement_of_significance_site_plan_floor_plan_elevations_s | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| arch_016_building_43_old_rain_shed_roof_plan_roof_truss_plan_hawaii_vo | 2 | 2 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| book_of_house_plans_buttrich | 163 | 163 | 0 | 0.0368 | 0.9656 | 0.0000 | 0.0000 |
| floor_plan_4_bedroom_rural_dwelling | 12 | 12 | 0 | 0.0000 | 1.5000 | 0.0000 | 0.0000 |
| floor_plan_5_bedroom_usda | 44 | 44 | 0 | 0.0682 | 0.9091 | 0.0000 | 0.0000 |
| loc22_bridge_truss_2_east_elevation_bridge_plan_truss_details_chickama | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_bridge_truss_5_queenpost_truss_details_morgan_bridge_spanning_no | 2 | 2 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_bridge_truss_truss_details_covered_bridge_carlisle_warren_county | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_bridge_truss_truss_details_covered_bridge_wickecheoke_creek_serg | 1 | 1 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_canal_lock_1_lock_gate_typical_elevation_section_a_a_section_b_b | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_canal_lock_2_lock_1_savannah_river_lock_elevation_of_north_wall_ | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_steam_engine_1_16_engine_room_piping_diagram_steam_schooner_wapa | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_steam_engine_1_steam_engine_maquina_de_vapor_hacienda_azucarera_ | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_steam_engine_engine_north_elevation_north_east_elevation_estate_ | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_turbine_1_site_plan_mill_room_turbine_room_plans_north_elevation | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_turbine_3_plan_turbines_3_4_side_view_turbines_3_4_section_a_a_a | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_turbine_7_cotton_flour_mill_section_a_a_section_b_b_plan_view_le | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_turbine_machine_location_plan_and_section_turbine_area_reheater_ | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_turbine_machine_location_turbine_area_sections_haddam_neck_nucle | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc22_turbine_machine_location_turbine_area_sections_haddam_neck_nucle_2 | 3 | 3 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc_habs_crystal_palace_saloon | 7 | 7 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc_habs_woodlawn_architecture | 39 | 39 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| loc_haer_fort_belvoir_bridge | 73 | 73 | 0 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| mechanical_drawing_cornell | 152 | 152 | 0 | 0.0658 | 1.2835 | 0.0000 | 0.0000 |
| sandiego_stormwater_pump_station_guidelines | 6 | 6 | 0 | 0.0000 | 2.5556 | 0.0000 | 0.0000 |
| sparkfun_micromod_artemis_final | 3 | 3 | 0 | 0.0000 | 1.1818 | 0.0000 | 0.0000 |
| sparkfun_micromod_esp32_v11update | 3 | 3 | 0 | 0.0000 | 1.8000 | 0.0000 | 0.0000 |
