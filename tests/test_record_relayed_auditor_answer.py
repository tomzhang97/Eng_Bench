import unittest

from tools.record_relayed_auditor_answer import bind_pending_answer


class RelayedAuditorAnswerTests(unittest.TestCase):
    def setUp(self):
        self.pending = [{"reviewer_id": "auditor_11", "display_index": 5, "record_id": "dimension"}]
        self.auditor = {"number": 11, "rows": [
            {"display_index": "5", "candidate_id": "dimension", "task": "microtext"}]}

    def test_explicit_relay_matches_only_the_frozen_blank(self):
        blank, assigned = bind_pending_answer(self.pending, self.auditor, 5, "1")
        self.assertEqual(blank["record_id"], assigned["candidate_id"])

    def test_cannot_supply_answer_to_a_nonpending_row(self):
        with self.assertRaisesRegex(ValueError, "exactly one verified pending blank"):
            bind_pending_answer(self.pending, self.auditor, 6, "1")

    def test_pending_identity_mismatch_fails_closed(self):
        self.pending[0]["record_id"] = "different"
        with self.assertRaisesRegex(ValueError, "frozen assignment identity"):
            bind_pending_answer(self.pending, self.auditor, 5, "1")

    def test_invalid_vote_is_never_normalized_to_accept(self):
        with self.assertRaisesRegex(ValueError, "invalid auditor decision"):
            bind_pending_answer(self.pending, self.auditor, 5, "4")

    def test_ambiguous_pending_duplicate_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "exactly one verified pending blank"):
            bind_pending_answer(self.pending * 2, self.auditor, 5, "1")


if __name__ == "__main__":
    unittest.main()
