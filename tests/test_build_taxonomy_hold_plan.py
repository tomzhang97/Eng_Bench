from __future__ import annotations

import unittest

from tools import build_taxonomy_hold_plan


class TaxonomyHoldPlanTests(unittest.TestCase):
    def test_classifies_minimal_taxonomy_extensions_and_existing_category(self) -> None:
        cases = [
            (
                {
                    "candidate_id": "civil",
                    "issues": "civil_standard_code_mislabeled_instrument_tag;rights_clearance_required_pennsylvania_state_material",
                    "final_category": "instrument_tag",
                },
                ("standard_id", "rights_and_taxonomy_hold"),
            ),
            (
                {
                    "candidate_id": "pcb",
                    "issues": "pcb_electrical_value_mislabeled_dimension_value",
                    "final_category": "dimension_value",
                },
                ("electrical_value", "taxonomy_confirmation_ready"),
            ),
            (
                {
                    "candidate_id": "pid",
                    "issues": "pid_equipment_tag_mislabeled_pin_label",
                    "final_category": "pin_label",
                },
                ("equipment_tag", "existing_category_confirmation_ready"),
            ),
        ]

        for row, expected in cases:
            classified = build_taxonomy_hold_plan.classify_hold_row(row)
            self.assertEqual(
                (classified["proposed_category"], classified["next_disposition"]),
                expected,
            )

    def test_unknown_issue_stays_unresolved(self) -> None:
        classified = build_taxonomy_hold_plan.classify_hold_row(
            {"candidate_id": "unknown", "issues": "new_issue", "final_category": "pin_label"}
        )

        self.assertEqual(classified["proposed_category"], "")
        self.assertEqual(classified["next_disposition"], "unresolved_taxonomy_hold")


if __name__ == "__main__":
    unittest.main()
