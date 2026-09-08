import unittest

from tools.apply_staged_split_reservations import apply_reservations


class ApplyStagedSplitReservationsTests(unittest.TestCase):
    def test_applies_microtext_and_visualdiff_reservations(self) -> None:
        rows = [
            {"candidate_id": "m1", "doc_id": "doc_a", "safe_to_merge_gold": True},
            {
                "pair_id": "v1",
                "project_id": "family_a",
                "image_old": "old.png",
                "image_new": "new.png",
            },
        ]
        plan = {
            "valid": True,
            "reservations": [
                {
                    "reservation_id": "r1",
                    "task": "microtext",
                    "unit_id": "doc_a",
                    "split": "test",
                    "assignment_basis": "planned_deficit_balance",
                },
                {
                    "reservation_id": "r2",
                    "task": "visualdiff",
                    "unit_id": "family_a",
                    "split": "dev",
                    "assignment_basis": "forced_review_reservation",
                },
            ],
        }

        output, report = apply_reservations(rows, plan, plan_path="plan.json")

        self.assertTrue(report["valid"])
        self.assertEqual([row["reserved_split"] for row in output], ["test", "dev"])
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in output))
        self.assertEqual(output[1]["split_reservation_basis"], "forced_review_reservation")

    def test_missing_reservation_invalidates_output(self) -> None:
        output, report = apply_reservations(
            [{"candidate_id": "m1", "doc_id": "missing"}],
            {"valid": True, "reservations": []},
            plan_path="plan.json",
        )

        self.assertFalse(report["valid"])
        self.assertEqual(output, [])
        self.assertEqual(report["issues"][0]["type"], "missing_split_reservation")

    def test_existing_split_conflict_is_rejected(self) -> None:
        rows = [{"candidate_id": "m1", "doc_id": "doc_a", "reserved_split": "train"}]
        plan = {
            "valid": True,
            "reservations": [
                {"task": "microtext", "unit_id": "doc_a", "split": "test"}
            ],
        }

        output, report = apply_reservations(rows, plan, plan_path="plan.json")

        self.assertFalse(report["valid"])
        self.assertEqual(output, [])
        self.assertEqual(report["issues"][0]["type"], "existing_split_conflict")

    def test_invalid_plan_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "plan is not valid"):
            apply_reservations([], {"valid": False}, plan_path="plan.json")


if __name__ == "__main__":
    unittest.main()
