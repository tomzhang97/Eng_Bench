import unittest

from tools.build_review_queue_continuity import build_continuity


def row(identity: str, fingerprint: str) -> dict:
    return {
        "candidate_id": identity,
        "replacement_evidence_fingerprint": fingerprint,
        "review_status": "needs_review",
        "safe_to_merge_gold": False,
    }


class ReviewQueueContinuityTests(unittest.TestCase):
    def test_retains_current_copies_and_retires_obsolete_prior_rows(self) -> None:
        current = [row("a", "fa"), row("b", "fb"), row("c", "fc")]
        prior = [[row("a", "fa"), row("b", "fb"), row("old", "fold")], [row("a", "fa")]]

        retained, retired, report = build_continuity(current, prior)

        self.assertTrue(report["valid"])
        self.assertEqual(["a", "b"], [value["candidate_id"] for value in retained])
        self.assertEqual(1, report["duplicate_prior_rows"])
        self.assertEqual(1, len(retired))
        self.assertEqual("not_selected_by_current_plan", retired[0]["review_continuity_hold_reason"])

    def test_records_evidence_alias_without_reissuing_it(self) -> None:
        current = [row("new", "same")]
        retained, retired, report = build_continuity(current, [[row("old", "same")]])

        self.assertTrue(report["valid"])
        self.assertEqual([], retained)
        self.assertEqual("evidence_selected_under_different_identity", retired[0]["review_continuity_hold_reason"])
        self.assertEqual("new", retired[0]["review_continuity_current_identity"])

    def test_fails_on_identity_evidence_change(self) -> None:
        retained, retired, report = build_continuity(
            [row("same", "new-fingerprint")],
            [[row("same", "old-fingerprint")]],
        )

        self.assertFalse(report["valid"])
        self.assertEqual([], retained)
        self.assertEqual([], retired)
        self.assertIn("current_identity_evidence_changed:same", report["issues"])


if __name__ == "__main__":
    unittest.main()
