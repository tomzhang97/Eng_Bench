from __future__ import annotations

import unittest

from tools.build_release_safe_agreement_contract import diverse_order, identity


class ReleaseSafeAgreementContractTests(unittest.TestCase):
    def test_identity_prefers_record_id(self) -> None:
        self.assertEqual(
            "record",
            identity({"record_id": "record", "candidate_id": "candidate", "pair_id": "pair"}),
        )

    def test_visual_order_uses_distinct_families_first(self) -> None:
        rows = [
            {"pair_id": "a2", "project_id": "family-a"},
            {"pair_id": "a1", "project_id": "family-a"},
            {"pair_id": "b1", "project_id": "family-b"},
        ]
        ordered = diverse_order(rows, "visualdiff", "seed")
        self.assertNotEqual(ordered[0]["project_id"], ordered[1]["project_id"])
        self.assertEqual(ordered, diverse_order(reversed(rows), "visualdiff", "seed"))

    def test_microtext_order_is_deterministic(self) -> None:
        rows = [
            {
                "candidate_id": "a",
                "reserved_split": "dev",
                "category": "pin_label",
                "doc_id": "doc-a",
            },
            {
                "candidate_id": "b",
                "reserved_split": "test",
                "category": "dimension_value",
                "doc_id": "doc-b",
            },
        ]
        self.assertEqual(
            diverse_order(rows, "microtext", "seed"),
            diverse_order(reversed(rows), "microtext", "seed"),
        )


if __name__ == "__main__":
    unittest.main()
