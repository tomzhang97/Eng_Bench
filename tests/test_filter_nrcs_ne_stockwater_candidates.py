from __future__ import annotations

import unittest

from tools import filter_nrcs_ne_stockwater_candidates as candidate_filter


class FilterNrcsNeStockwaterCandidatesTests(unittest.TestCase):
    def row(self, text: str, category: str, candidate_id: str, confidence: float = 0.99) -> dict:
        return {
            "doc_id": candidate_filter.DOC_ID,
            "page_index": 1,
            "bbox": [1, 2, 30, 40],
            "proposed_text": text,
            "category": category,
            "candidate_id": candidate_id,
            "ocr_confidence": confidence,
        }

    def test_routes_only_distinct_high_precision_labels(self) -> None:
        rows = [
            self.row('24"', "dimension_value", "a", 0.97),
            self.row("25.7", "dimension_value", "b"),
            self.row("40 PSI", "process_value", "c"),
            self.row("Float Valve", "equipment_tag", "d", 0.999),
            self.row("Float Valve", "equipment_tag", "e", 0.98),
            self.row("Valve Sleeve", "equipment_tag", "f"),
            self.row("Guard post", "pin_label", "g"),
            self.row("8.4 PRESSURE TANKS", "equipment_tag", "h"),
            self.row("Pressure tank", "equipment_tag", "i", 0.97),
        ]

        selected, held, report = candidate_filter.filter_rows(rows)

        self.assertEqual(report["selected_rows"], 5)
        self.assertEqual(report["held_rows"], 4)
        self.assertEqual(report["selected_by_route"]["ocr_objective_human_verification"], 2)
        self.assertEqual(report["selected_by_route"]["human_semantic_review"], 3)
        self.assertEqual(sum(row["machine_qa_notes"] == "duplicate_normalized_text" for row in held), 1)
        self.assertEqual(
            {row["category"] for row in selected},
            {"dimension_value", "process_value", "equipment_tag", "component_value"},
        )

    def test_rejects_other_sources(self) -> None:
        row = self.row("Float Valve", "equipment_tag", "x")
        row["doc_id"] = "other"
        selected, held, _ = candidate_filter.filter_rows([row])
        self.assertFalse(selected)
        self.assertEqual(held[0]["machine_qa_notes"], "unexpected_source")


if __name__ == "__main__":
    unittest.main()
