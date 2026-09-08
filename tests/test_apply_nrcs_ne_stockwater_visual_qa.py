from __future__ import annotations

import unittest

from tools import apply_nrcs_ne_stockwater_visual_qa as visual_qa


class ApplyNrcsNeStockwaterVisualQaTests(unittest.TestCase):
    def row(self, candidate_id: str, text: str = "value") -> dict:
        return {
            "candidate_id": candidate_id,
            "page_index": 1,
            "bbox": [1, 2, 30, 40],
            "category": "process_value",
            "proposed_text": text,
        }

    def test_records_corrections_and_holds(self) -> None:
        correction_id = next(iter(visual_qa.TEXT_CORRECTIONS))
        hold_id = next(iter(visual_qa.VISUAL_HOLDS))
        rows = [self.row(correction_id, "old"), self.row(hold_id), self.row("plain")]

        selected, held, report = visual_qa.apply_visual_qa(rows)

        self.assertEqual(len(selected), 2)
        self.assertEqual(len(held), 1)
        corrected = next(row for row in selected if row["candidate_id"] == correction_id)
        self.assertEqual(corrected["proposed_text"], visual_qa.TEXT_CORRECTIONS[correction_id])
        self.assertEqual(corrected["machine_visual_original_text"], "old")
        self.assertFalse(corrected["safe_to_merge_gold"])
        self.assertTrue(report["missing_recorded_decisions"])

    def test_full_decision_set_has_no_missing_ids(self) -> None:
        rows = [self.row(candidate_id) for candidate_id in visual_qa.TEXT_CORRECTIONS]
        rows.extend(self.row(candidate_id) for candidate_id in visual_qa.VISUAL_HOLDS)
        _, _, report = visual_qa.apply_visual_qa(rows)
        self.assertFalse(report["missing_recorded_decisions"])


if __name__ == "__main__":
    unittest.main()
