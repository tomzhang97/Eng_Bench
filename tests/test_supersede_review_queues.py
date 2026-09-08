from __future__ import annotations

import unittest

from tools.supersede_review_queues import supersede_rows


class SupersedeReviewQueuesTest(unittest.TestCase):
    def test_only_open_rows_are_superseded(self) -> None:
        rows = [
            {"candidate_id": "a", "review_status": "needs_review"},
            {"candidate_id": "b", "review_status": "accepted"},
        ]

        updated, count = supersede_rows(rows, "final.jsonl")

        self.assertEqual(count, 1)
        self.assertEqual(updated[0]["review_status"], "machine_superseded")
        self.assertEqual(updated[0]["superseded_by"], "final.jsonl")
        self.assertEqual(updated[1]["review_status"], "accepted")


if __name__ == "__main__":
    unittest.main()
