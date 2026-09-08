from __future__ import annotations

import unittest

from tools import materialize_review_supersession as module


class MaterializeReviewSupersessionTests(unittest.TestCase):
    def test_marks_pending_rows_as_machine_superseded(self) -> None:
        rows = [
            {
                "candidate_id": "row-1",
                "review_status": "needs_review",
                "safe_to_merge_gold": True,
            }
        ]

        output = module.materialize(
            rows,
            replacement="canonical.jsonl",
            reason="obsolete_orientation",
        )

        self.assertEqual(rows[0]["review_status"], "needs_review")
        self.assertEqual(output[0]["review_status"], "machine_superseded")
        self.assertEqual(output[0]["superseded_by"], "canonical.jsonl")
        self.assertEqual(output[0]["machine_hold_reason"], "obsolete_orientation")
        self.assertFalse(output[0]["safe_to_merge_gold"])

    def test_refuses_rows_with_human_decisions(self) -> None:
        with self.assertRaisesRegex(ValueError, "human_decisions"):
            module.materialize(
                [{"candidate_id": "row-1", "reviewed_by": "intern"}],
                replacement="canonical.jsonl",
                reason="obsolete_orientation",
            )


if __name__ == "__main__":
    unittest.main()
