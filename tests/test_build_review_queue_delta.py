import unittest

from tools import build_review_queue_delta as delta


def row(identifier: str, fingerprint: str) -> dict:
    return {
        "candidate_id": identifier,
        "replacement_evidence_fingerprint": fingerprint,
        "review_status": "needs_review",
        "safe_to_merge_gold": False,
    }


class BuildReviewQueueDeltaTests(unittest.TestCase):
    def test_subtracts_identity_and_evidence_overlaps(self) -> None:
        selected, held, report = delta.build_delta(
            [row("a", "fp-a"), row("b", "fp-b"), row("c", "fp-c")],
            [[row("a", "fp-a"), row("old-b", "fp-b")]],
        )
        self.assertEqual([item["candidate_id"] for item in selected], ["c"])
        self.assertEqual(len(held), 2)
        self.assertEqual(report["hold_reasons"]["excluded_identity_and_evidence"], 1)
        self.assertEqual(report["hold_reasons"]["excluded_evidence"], 1)
        self.assertTrue(report["valid"])

    def test_rejects_duplicate_input_fingerprints(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate fingerprints=1"):
            delta.build_delta(
                [row("a", "same"), row("b", "same")],
                [],
            )


if __name__ == "__main__":
    unittest.main()
