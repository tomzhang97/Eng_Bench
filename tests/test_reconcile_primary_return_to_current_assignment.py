import unittest

from tools.reconcile_primary_return_to_current_assignment import decision_export


class ReconcilePrimaryReturnTests(unittest.TestCase):
    def test_decision_export_keeps_only_reviewer_cells_and_status(self) -> None:
        decision = {
            "record_id": "mt-1",
            "primary_index": 4,
            "task": "microtext",
            "decision_code": 2,
            "engineering_basis": "The tag follows the instrument convention.",
            "ready": True,
            "status": "edited",
            "decision_class": "keep",
            "blocking_reasons": [],
            "corrected_text": "PT-101",
            "corrected_category": "instrument_tag",
            "effective_text": "PT-101",
        }
        exported = decision_export(decision)
        self.assertEqual("PT-101", exported["corrected_text"])
        self.assertEqual("instrument_tag", exported["corrected_category"])
        self.assertNotIn("effective_text", exported)

    def test_visual_export_uses_visual_correction_fields(self) -> None:
        decision = {
            "record_id": "vd-1",
            "primary_index": 8,
            "task": "visualdiff",
            "decision_code": 2,
            "engineering_basis": "The nominal value changed.",
            "ready": True,
            "status": "edit",
            "decision_class": "keep",
            "blocking_reasons": [],
            "corrected_change_type": "dimension_change",
            "corrected_description": "The value changed from 10 to 12 mm.",
        }
        exported = decision_export(decision)
        self.assertEqual("dimension_change", exported["corrected_change_type"])
        self.assertIn("10 to 12", exported["corrected_description"])


if __name__ == "__main__":
    unittest.main()
