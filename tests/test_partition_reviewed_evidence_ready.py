import unittest

from tools.partition_reviewed_evidence_ready import partition


class PartitionReviewedEvidenceReadyTests(unittest.TestCase):
    def test_partitions_alias_held_rows_without_changing_values(self):
        held = {"candidate-held"}
        rows = [
            {"item_id": "public-held", "source_candidate_id": "candidate-held", "text_gt": "A"},
            {"candidate_id": "ready", "proposed_text": "B"},
        ]
        ready, holds = partition(rows, "microtext", held)
        self.assertEqual(["ready"], [row["candidate_id"] for row in ready])
        self.assertEqual("public-held", holds[0]["identity"])
        self.assertEqual("A", holds[0]["row"]["text_gt"])

    def test_duplicate_identity_fails_closed(self):
        with self.assertRaises(ValueError):
            partition(
                [{"candidate_id": "same"}, {"candidate_id": "same"}],
                "microtext",
                set(),
            )


if __name__ == "__main__":
    unittest.main()
