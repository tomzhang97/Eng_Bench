import unittest

from tools.merge_staged_split_plans import merge_plans


def plan(*rows):
    return {"valid": True, "reservations": list(rows)}


class MergeStagedSplitPlansTest(unittest.TestCase):
    def test_merges_disjoint_reservations(self) -> None:
        merged = merge_plans(
            [
                ("a", plan({"reservation_id": "a1", "task": "microtext", "unit_id": "doc-a", "split": "train"})),
                ("b", plan({"reservation_id": "b1", "task": "microtext", "unit_id": "doc-b", "split": "dev"})),
            ],
            date_label="test",
        )

        self.assertTrue(merged["valid"])
        self.assertEqual(2, merged["reservation_count"])
        self.assertEqual({"dev": 1, "train": 1}, merged["reservations_by_split"])

    def test_deduplicates_identical_reservations(self) -> None:
        row = {"reservation_id": "a1", "task": "microtext", "unit_id": "doc-a", "split": "train"}
        merged = merge_plans([("a", plan(row)), ("b", plan(row))], date_label="test")

        self.assertEqual(1, merged["reservation_count"])
        self.assertEqual(1, merged["duplicate_identical_reservations"])

    def test_rejects_conflicting_reservations(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicting reservation"):
            merge_plans(
                [
                    ("a", plan({"reservation_id": "a1", "task": "microtext", "unit_id": "doc-a", "split": "train"})),
                    ("b", plan({"reservation_id": "b1", "task": "microtext", "unit_id": "doc-a", "split": "test"})),
                ],
                date_label="test",
            )


if __name__ == "__main__":
    unittest.main()
