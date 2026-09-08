# Eng_Bench Source Quotas

This tracks source-domain breadth for v1.0 and v1.5 expansion. Counts use release-safe active inventory rows; reference-only, unknown, restricted, and proprietary-marked rows do not count as active release-safe sources.

| Domain | Inventory Total | Release-Safe Active | Candidates | Intake-First | remaining_to_v1_0 | remaining_to_v1_5 | candidate_target_v1_5 | remaining_candidate_gap_v1_5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pcb_schematic | 7 | 7 | 56 | 22 | 0 | 8 | 30 | 0 |
| datasheet_spec | 0 | 0 | 63 | 11 | 7 | 15 | 30 | 0 |
| mechanical_cad | 4 | 2 | 44 | 9 | 5 | 13 | 30 | 0 |
| civil_architectural | 13 | 10 | 36 | 19 | 0 | 5 | 30 | 0 |
| pid | 8 | 3 | 51 | 5 | 4 | 12 | 30 | 0 |

Next import priority should favor domains with high `remaining_to_v1_0` and available `intake_first` candidates.
