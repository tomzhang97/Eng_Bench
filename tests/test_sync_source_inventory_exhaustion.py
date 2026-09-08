from __future__ import annotations

import unittest

from tools.sync_source_inventory_exhaustion import synchronize_inventory_rows


class SyncSourceInventoryExhaustionTests(unittest.TestCase):
    def test_synchronizes_exhaustion_fields_without_losing_other_values(self) -> None:
        inventory = [
            {
                "doc_id": "doc_a",
                "notes": "preserve me",
                "conversion_exhaustion_status": "",
                "conversion_exhaustion_evidence": "",
                "next_step": "mine_candidates",
                "priority_score": "42",
                "public_status": "public_domain_us_federal_candidate",
            },
            {"doc_id": "doc_b", "notes": "untouched"},
        ]
        exhaustion = [
            {
                "doc_id": "doc_a",
                "status": "machine_exhausted_after_reviewed_pass",
                "evidence_path": "derived/quality/doc_a.json",
            }
        ]

        rows, report = synchronize_inventory_rows(inventory, exhaustion)

        self.assertTrue(report["valid"])
        self.assertEqual(report["drifted_rows"], 1)
        self.assertEqual(rows[0]["notes"], "preserve me")
        self.assertEqual(
            rows[0]["conversion_exhaustion_status"],
            "machine_exhausted_after_reviewed_pass",
        )
        self.assertEqual(
            rows[0]["conversion_exhaustion_evidence"],
            "derived/quality/doc_a.json",
        )
        self.assertEqual(rows[0]["next_step"], "machine_exhausted_after_reviewed_pass")
        self.assertEqual(rows[0]["priority_score"], "-100")
        self.assertEqual(rows[1], inventory[1])

    def test_rights_hold_remains_the_next_step_for_exhausted_source(self) -> None:
        inventory = [
            {
                "doc_id": "doc_a",
                "public_status": "release_review_needed_embedded_diagram_provenance",
                "conversion_exhaustion_status": "machine_exhausted_no_candidate",
                "conversion_exhaustion_evidence": "evidence.json",
                "next_step": "rights_review_or_hold",
                "priority_score": "-100",
            }
        ]
        exhaustion = [
            {
                "doc_id": "doc_a",
                "status": "machine_exhausted_no_candidate",
                "evidence_path": "evidence.json",
            }
        ]

        rows, report = synchronize_inventory_rows(inventory, exhaustion)

        self.assertTrue(report["valid"])
        self.assertEqual(report["drifted_rows"], 0)
        self.assertEqual(rows[0]["next_step"], "rights_review_or_hold")

    def test_fails_closed_for_missing_inventory_doc(self) -> None:
        rows, report = synchronize_inventory_rows(
            [{"doc_id": "doc_a"}],
            [
                {
                    "doc_id": "doc_missing",
                    "status": "machine_exhausted_no_candidate",
                    "evidence_path": "evidence.json",
                }
            ],
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["missing_inventory_doc_ids"], ["doc_missing"])
        self.assertEqual(rows, [{"doc_id": "doc_a"}])

    def test_fails_closed_for_duplicate_or_unknown_exhaustion_state(self) -> None:
        _, report = synchronize_inventory_rows(
            [{"doc_id": "doc_a"}],
            [
                {"doc_id": "doc_a", "status": "unknown", "evidence_path": "a"},
                {"doc_id": "doc_a", "status": "unknown", "evidence_path": "b"},
            ],
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["exhaustion_duplicate_doc_ids"], ["doc_a"])
        self.assertEqual(report["invalid_status_doc_ids"], ["doc_a", "doc_a"])

    def test_synchronizes_all_inventory_assets_for_one_document(self) -> None:
        rows, report = synchronize_inventory_rows(
            [
                {"doc_id": "doc_a", "path": "one.pdf"},
                {"doc_id": "doc_a", "path": "two.svg"},
            ],
            [
                {
                    "doc_id": "doc_a",
                    "status": "machine_exhausted_no_candidate",
                    "evidence_path": "evidence.json",
                }
            ],
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["inventory_duplicate_doc_ids"], ["doc_a"])
        self.assertEqual(report["matched_inventory_rows"], 2)
        self.assertEqual(
            {row["conversion_exhaustion_status"] for row in rows},
            {"machine_exhausted_no_candidate"},
        )


if __name__ == "__main__":
    unittest.main()
