from __future__ import annotations

import unittest

from tools import filter_nrcs_nd_stockwater_candidates as candidate_filter


class FilterNrcsNdStockwaterCandidatesTests(unittest.TestCase):
    def row(self, text: str, category: str, candidate_id: str, confidence: float = 0.99) -> dict:
        return {
            "doc_id": "nrcs_nd_stockwater_doc",
            "page_index": 0,
            "bbox": [1, 2, 3, 4],
            "proposed_text": text,
            "category": category,
            "candidate_id": candidate_id,
            "ocr_confidence": confidence,
        }

    def test_keeps_only_high_precision_labels_and_deduplicates(self) -> None:
        rows = [
            self.row("13'-6\"", "dimension_value", "a"),
            self.row("11.4", "dimension_value", "b"),
            self.row("Float Valve", "equipment_tag", "c", 0.999),
            self.row("Float Valve", "equipment_tag", "d", 0.98),
            self.row("Treated Wood Post", "pin_label", "e"),
            self.row("molded into the tank edges", "equipment_tag", "f"),
            self.row("Pump House", "room_label", "g"),
        ]

        selected, held, report = candidate_filter.filter_rows(rows)

        self.assertEqual(report["selected_rows"], 4)
        self.assertEqual(report["held_rows"], 3)
        self.assertEqual({row["category"] for row in selected}, {"dimension_value", "equipment_tag", "component_value", "room_label"})
        self.assertEqual(sum(row["machine_qa_notes"] == "duplicate_normalized_text" for row in held), 1)


if __name__ == "__main__":
    unittest.main()
