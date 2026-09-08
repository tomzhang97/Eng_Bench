from __future__ import annotations

import unittest

from tools.audit_agreement_candidate_completion import build_completion


def contract_row() -> dict:
    return {
        "agreement_contract_index": 1,
        "record_id": "candidate-a",
        "task": "microtext",
        "reserved_split": "dev",
        "agreement_channel": "completed_independent_screen",
        "agreement_evidence_fingerprint": "microtext:sha256:abc",
        "safe_to_merge_gold": False,
    }


class AgreementCandidateCompletionTests(unittest.TestCase):
    def test_pairs_valid_primary_and_independent_screen_without_formal_claim(self) -> None:
        screen = {
            "record_id": "candidate-a",
            "task_type": "microtext",
            "reviewer_id": "auditor_01",
            "reviewer_role": "independent_auditor",
            "decision_code": "1",
        }
        primary = {
            "record_id": "candidate-a",
            "task_type": "microtext",
            "reviewer_id": "primary_reviewer",
            "reviewer_role": "primary",
            "decision_code": "1",
        }
        ledger, report = build_completion(
            contract_rows=[contract_row()],
            screen_decisions={"candidate-a": screen},
            primary_decisions={"candidate-a": primary},
            contract_sha256="contract-hash",
        )
        self.assertEqual("PASS", report["status"])
        self.assertTrue(report["candidate_returns_complete"])
        self.assertTrue(ledger[0]["candidate_dual_check_complete"])
        self.assertTrue(ledger[0]["candidate_pair_pass"])
        self.assertEqual("pass", ledger[0]["candidate_pair_outcome"])
        self.assertEqual(1, report["candidate_pair_pass"])
        self.assertEqual({}, report["screen_pending_by_channel"])
        self.assertFalse(ledger[0]["formal_detailed_agreement_complete"])

    def test_completed_issue_pair_is_not_counted_as_passing_capacity(self) -> None:
        screen = {
            "record_id": "candidate-a",
            "task_type": "microtext",
            "reviewer_id": "auditor_01",
            "reviewer_role": "independent_auditor",
            "decision_code": "2",
        }
        primary = {
            "record_id": "candidate-a",
            "task_type": "microtext",
            "reviewer_id": "primary_reviewer",
            "reviewer_role": "primary",
            "decision_code": "1",
        }

        ledger, report = build_completion(
            contract_rows=[contract_row()],
            screen_decisions={"candidate-a": screen},
            primary_decisions={"candidate-a": primary},
            contract_sha256="contract-hash",
        )

        self.assertTrue(report["candidate_returns_complete"])
        self.assertEqual(1, report["candidate_pair_issue"])
        self.assertEqual(0, report["candidate_pair_pass"])
        self.assertFalse(ledger[0]["candidate_pair_pass"])
        self.assertEqual("issue", ledger[0]["candidate_pair_outcome"])

    def test_task_mismatch_fails_closed(self) -> None:
        screen = {
            "record_id": "candidate-a",
            "task_type": "visualdiff",
            "reviewer_id": "auditor_01",
            "decision_code": "1",
        }
        _, report = build_completion(
            contract_rows=[contract_row()],
            screen_decisions={"candidate-a": screen},
            primary_decisions={},
            contract_sha256="contract-hash",
        )
        self.assertEqual("FAIL", report["status"])
        self.assertIn("screen_decision_invalid:candidate-a", report["issues"])


if __name__ == "__main__":
    unittest.main()
