from collections import Counter
import unittest

from tools import prepare_fresh_auditor_round as fresh


def row(identifier: str, group: str, evidence_hash: str) -> dict:
    return {
        "record_id": identifier,
        "source_group": group,
        "evidence_sha256": evidence_hash,
    }


class PrepareFreshAuditorRoundTests(unittest.TestCase):
    def test_select_diverse_skips_used_evidence_and_balances_groups(self) -> None:
        rows = [
            row("a1", "a", "used"),
            row("a2", "a", "ha2"),
            row("b1", "b", "hb1"),
            row("c1", "c", "hc1"),
        ]
        selected = fresh.select_diverse(rows, 3, {"used"}, Counter())
        self.assertEqual(3, len(selected))
        self.assertEqual(
            {"a2", "b1", "c1"}, {item["record_id"] for item in selected}
        )

    def test_select_diverse_fails_closed_when_unique_evidence_is_insufficient(
        self,
    ) -> None:
        rows = [row("a1", "a", "same"), row("b1", "b", "same")]
        with self.assertRaisesRegex(ValueError, "only selected 1 of 2"):
            fresh.select_diverse(rows, 2, set(), Counter())

    def test_normalized_row_is_unanswered_and_not_gold_mergeable(self) -> None:
        source = {
            "task": "microtext",
            "candidate_id": "mt-1",
            "source_group": "doc-1",
        }
        result = fresh.normalized_row(source, 7)
        self.assertEqual("7", result["display_index"])
        self.assertEqual("", result["preserved_answer_code"])
        self.assertIs(False, result["safe_to_merge_gold"])
        self.assertEqual(
            "independent_audit_only", result["auditor_assignment_status"]
        )


if __name__ == "__main__":
    unittest.main()
