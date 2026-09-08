from __future__ import annotations

import unittest

from tools.partition_review_queue_by_overlay import partition_rows


class PartitionReviewQueueByOverlayTest(unittest.TestCase):
    def test_priority_rows_are_first_and_annotated(self) -> None:
        rows = [
            {"candidate_id": "a", "category": "equipment_tag"},
            {"candidate_id": "b", "category": "pipe_line_tag"},
            {"candidate_id": "c", "category": "process_value"},
        ]
        overlay = [{"candidate_id": "c"}, {"candidate_id": "a"}]

        priority, deferred, ordered = partition_rows(rows, overlay)

        self.assertEqual(["a", "c"], [row["candidate_id"] for row in priority])
        self.assertEqual(["b"], [row["candidate_id"] for row in deferred])
        self.assertEqual(["a", "c", "b"], [row["candidate_id"] for row in ordered])
        self.assertEqual(
            [1, 2], [row["human_review_priority_rank"] for row in priority]
        )
        self.assertEqual("deferred_reserve", deferred[0]["human_review_priority"])

    def test_rejects_duplicate_input_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate row identities"):
            partition_rows(
                [{"candidate_id": "a"}, {"candidate_id": "a"}],
                [{"candidate_id": "a"}],
            )

    def test_rejects_blank_overlay_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "blank row identity"):
            partition_rows([{"candidate_id": "a"}], [{}])


if __name__ == "__main__":
    unittest.main()
