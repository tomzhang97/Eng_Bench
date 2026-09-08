from __future__ import annotations

import unittest

from tools.extend_primary_intern_to_4000 import (
    ENGINEERING_MICRO_CATEGORIES,
    classify_priority_coverage,
    diverse_order,
    engineering_reason,
    micro_candidate_order,
    primary_micro_candidate_order,
    primary_visual_candidate_order,
    visual_candidate_order,
)


def micro(candidate_id: str, category: str, doc_id: str) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "category": category,
        "doc_id": doc_id,
        "reserved_split": "train",
    }


def visual(pair_id: str, project_id: str) -> dict[str, object]:
    return {
        "pair_id": pair_id,
        "project_id": project_id,
        "reserved_split": "test",
    }


class ExtendPrimaryInternTests(unittest.TestCase):
    def test_diverse_order_round_robins_groups(self) -> None:
        rows = [
            micro("a1", "dimension_value", "a"),
            micro("a2", "dimension_value", "a"),
            micro("b1", "dimension_value", "b"),
        ]
        ordered = diverse_order(rows, lambda row: str(row["doc_id"]))
        self.assertEqual([row["candidate_id"] for row in ordered], ["a1", "b1", "a2"])

    def test_micro_candidate_order_excludes_pin_and_cycles_categories(self) -> None:
        rows = [
            micro("d1", "dimension_value", "doc1"),
            micro("d2", "dimension_value", "doc2"),
            micro("e1", "equipment_tag", "doc3"),
            micro("p1", "pin_label", "doc4"),
        ]
        ordered = micro_candidate_order(rows)
        self.assertEqual([row["candidate_id"] for row in ordered], ["d1", "e1", "d2"])
        self.assertTrue(
            all(str(row["category"]) in ENGINEERING_MICRO_CATEGORIES for row in ordered)
        )

    def test_visual_order_and_engineering_reasons_are_explicit(self) -> None:
        rows = [
            visual("a1", "family_a"),
            visual("a2", "family_a"),
            visual("b1", "family_b"),
        ]
        ordered = visual_candidate_order(rows)
        self.assertEqual([row["pair_id"] for row in ordered], ["a1", "b1", "a2"])
        self.assertIn("工程专项", engineering_reason(ordered[0]))
        self.assertIn(
            "类别平衡工程专项",
            engineering_reason(micro("d1", "dimension_value", "doc1")),
        )

    def test_primary_order_prioritizes_exact_replacements(self) -> None:
        priority_pin = micro("p1", "pin_label", "doc4")
        priority_pin["provenance_replacement_candidate"] = True
        regular_pin = micro("p2", "pin_label", "doc5")
        rows = [
            micro("d1", "dimension_value", "doc1"),
            regular_pin,
            priority_pin,
            micro("e1", "equipment_tag", "doc3"),
        ]
        ordered = primary_micro_candidate_order(rows)
        self.assertEqual([row["candidate_id"] for row in ordered], ["p1", "d1", "e1"])

        priority_visual = visual("a2", "family_a")
        priority_visual["provenance_replacement_candidate"] = True
        visual_rows = [
            visual("a1", "family_a"),
            visual("b1", "family_b"),
            priority_visual,
        ]
        visual_ordered = primary_visual_candidate_order(visual_rows)
        self.assertEqual(
            [row["pair_id"] for row in visual_ordered],
            ["a2", "a1", "b1"],
        )

    def test_priority_metadata_overlay_contract_is_preserved_by_ordering(self) -> None:
        canonical = micro("p1", "pin_label", "doc4")
        contract = dict(canonical)
        contract["provenance_replacement_candidate"] = True
        merged = dict(canonical)
        merged.update(contract)
        ordered = primary_micro_candidate_order(
            [micro("d1", "dimension_value", "doc1"), merged]
        )
        self.assertEqual([row["candidate_id"] for row in ordered], ["p1", "d1"])
        self.assertTrue(ordered[0]["provenance_replacement_candidate"])

    def test_priority_coverage_counts_primary_specialist_and_exact_alias(self) -> None:
        coverage = classify_priority_coverage(
            {"direct", "specialist", "alias", "missing"},
            {"direct"},
            {"specialist"},
            [
                {
                    "record_id": "alias",
                    "review_via_record_id": "direct",
                    "coverage_basis": "exact_rendered_evidence_sha256",
                }
            ],
        )
        self.assertEqual(coverage["direct_primary"], ["direct"])
        self.assertEqual(coverage["specialist"], ["specialist"])
        self.assertEqual(coverage["exact_evidence_alias"], ["alias"])
        self.assertEqual(coverage["missing"], ["missing"])


if __name__ == "__main__":
    unittest.main()
