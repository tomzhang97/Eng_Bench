from __future__ import annotations

import unittest

from tools.process_review_batch_workbook_return import (
    extract_task_decisions,
    normalize_decision,
)


class ReviewBatchWorkbookReturnTests(unittest.TestCase):
    def test_normalize_decision_accepts_excel_numeric_text(self) -> None:
        self.assertEqual(normalize_decision("1.0"), "accepted")
        self.assertEqual(normalize_decision(2), "edited")
        self.assertEqual(normalize_decision("4"), "needs_full_page")
        self.assertEqual(normalize_decision("accepted"), "")

    def test_extract_visualdiff_edit_preserves_binding(self) -> None:
        rows = [
            {
                "pair_id": "vd_1",
                "replacement_for_split": "dev",
                "replacement_source_unit": "family_1",
                "description": "old description",
                "description_rewrite_required": False,
            }
        ]
        review = {
            (7, 5): "2",
            (7, 6): "text",
            (7, 7): "A changed to B.",
            (7, 8): "checked",
            (7, 10): "vd_1",
        }
        control = {
            (2, 2): "vd_1",
            (2, 3): "dev",
            (2, 4): "family_1",
            (2, 9): "false",
            (2, 10): "needs_review",
            (2, 11): "false",
        }
        checklist, counts, binding, decisions = extract_task_decisions(
            task="visualdiff",
            manifest_rows=rows,
            review_cells=review,
            control_cells=control,
        )
        self.assertEqual(binding, [])
        self.assertEqual(decisions, [])
        self.assertEqual(counts, {"edited": 1})
        self.assertEqual(checklist[0]["human_status"], "edit")
        self.assertEqual(checklist[0]["corrected_change_type"], "text")
        self.assertEqual(checklist[0]["human_description"], "A changed to B.")

    def test_extract_blank_is_incomplete_but_binding_safe(self) -> None:
        rows = [
            {
                "candidate_id": "mt_1",
                "replacement_for_split": "dev",
                "replacement_source_unit": "doc_1",
                "proposed_text": "R10",
            }
        ]
        review = {(7, 10): "mt_1"}
        control = {
            (2, 2): "mt_1",
            (2, 3): "dev",
            (2, 4): "doc_1",
            (2, 9): "false",
            (2, 10): "needs_review",
            (2, 11): "false",
        }
        _, counts, binding, decisions = extract_task_decisions(
            task="microtext",
            manifest_rows=rows,
            review_cells=review,
            control_cells=control,
        )
        self.assertEqual(binding, [])
        self.assertEqual(counts, {"blank_or_invalid": 1})
        self.assertEqual(decisions, ["invalid_or_missing_decision:1"])


if __name__ == "__main__":
    unittest.main()
