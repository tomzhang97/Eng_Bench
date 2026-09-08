import unittest

from tools.prepare_auditor_supported_batch import select_rows, subset_reservations


class SupportedBatchTests(unittest.TestCase):
    def row(self, **values):
        return {"candidate_id": "c", "review_status": "accepted", "category": "dimension_value",
                "safe_to_merge_gold": False, "independent_audit_support": {"decision_code": "1"}, **values}

    def test_only_supported_priority_rows_selected(self):
        rows = [self.row(), self.row(candidate_id="p", category="pin_label"),
                self.row(candidate_id="u", independent_audit_support={}),
                self.row(candidate_id="n", review_status="pending")]
        selected, holds = select_rows(rows, "microtext", {"dimension_value"})
        self.assertEqual(["c"], [row["candidate_id"] for row in selected])
        self.assertEqual(3, len(holds))

    def test_uncertain_descriptions_and_duplicate_ids_never_selected(self):
        row = self.row(pair_id="p", human_description="Localized text may have changed from 'A' to 'B'.")
        selected, holds = select_rows([row], "visualdiff", set())
        self.assertFalse(selected)
        self.assertIn("tentative_visualdiff_description", holds[0]["reasons"])
        with self.assertRaises(ValueError):
            select_rows([self.row(), self.row()], "microtext", {"dimension_value"})

    def test_machine_evidence_holds_are_deferred_before_preview(self):
        selected, holds = select_rows(
            [self.row(candidate_id="held"), self.row(candidate_id="ready")],
            "microtext",
            {"dimension_value"},
            {"held"},
        )
        self.assertEqual(["ready"], [row["candidate_id"] for row in selected])
        self.assertEqual(
            ["unresolved_machine_evidence_hold"], holds[0]["reasons"]
        )

    def test_subset_preserves_reservations_and_fails_for_missing_units(self):
        row = self.row(task_type="microtext", doc_id="doc")
        original = {"task": "microtext", "unit_id": "doc", "split": "test", "reservation_id": "keep"}
        records = {("microtext", "doc"): original, ("microtext", "other"): {"split": "dev"}}
        self.assertEqual([original], subset_reservations([row], records))
        with self.assertRaises(ValueError):
            subset_reservations([row], {})


if __name__ == "__main__":
    unittest.main()
