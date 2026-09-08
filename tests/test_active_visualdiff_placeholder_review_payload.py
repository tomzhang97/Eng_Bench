import unittest

from tools.build_active_visualdiff_placeholder_review_payload import proposal, spans_in_box


class ActiveVisualDiffPlaceholderProposalTests(unittest.TestCase):
    def test_one_sided_text_requires_alignment_review(self):
        change_type, description, relation, lane = proposal("SWD_CLK", "")
        self.assertEqual("unclear", change_type)
        self.assertIn("SWD_CLK", description)
        self.assertIn("alignment drift", description)
        self.assertEqual("old_only_text", relation)
        self.assertEqual("unilateral_text_review_required", lane)

    def test_proposes_text_change(self):
        change_type, description, relation, lane = proposal("2GB", "4GB")
        self.assertEqual("text_change", change_type)
        self.assertIn("2GB", description)
        self.assertIn("4GB", description)
        self.assertEqual("different_text", relation)
        self.assertEqual("text_grounded_candidate", lane)

    def test_reverse_extraction_order_is_not_a_text_change(self):
        change_type, description, relation, lane = proposal(
            "VCC3 | VCC2 | VCC1 | VCC0",
            "VCC0 | VCC1 | VCC2 | VCC3",
        )
        self.assertEqual("symbol_component_change", change_type)
        self.assertIn("unchanged", description)
        self.assertEqual("same_text", relation)
        self.assertEqual("graphic_review_required", lane)

    def test_requires_graphic_review_without_text(self):
        change_type, _, relation, lane = proposal("", "")
        self.assertEqual("symbol_component_change", change_type)
        self.assertEqual("no_text", relation)
        self.assertEqual("graphic_review_required", lane)

    def test_span_requires_meaningful_overlap(self):
        spans = [
            {"page": 0, "text": "R10", "bbox_px": [10, 10, 50, 30]},
            {"page": 0, "text": "R11", "bbox_px": [100, 100, 140, 120]},
        ]
        findings = spans_in_box(spans, 0, [10, 10, 30, 30])
        self.assertEqual(["R10"], [row["text"] for row in findings])


if __name__ == "__main__":
    unittest.main()
