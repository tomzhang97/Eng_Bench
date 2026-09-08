from __future__ import annotations

import unittest

from tools import shortlist_pid_table_microtext


def row(text: str, page: int, bbox: list[int], category: str = "unknown_microtext") -> dict:
    return {
        "candidate_id": f"cand_{page}_{bbox[1]}",
        "page_index": page,
        "bbox": bbox,
        "proposed_text": text,
        "category": category,
        "review_status": "needs_review",
    }


class ShortlistPidTableMicrotextTest(unittest.TestCase):
    def test_selects_guarded_categories_and_deduplicates_answers(self) -> None:
        rows = [
            row("FPT01", 83, [425, 900, 545, 960]),
            row("FPT01", 88, [425, 600, 545, 660]),
            row("0 to 75 psia", 88, [646, 600, 833, 668]),
            row("COOLANT SUPPLY TO REACTOR", 81, [2100, 1600, 2400, 1650]),
            row("Turbomolecular pump", 81, [400, 1000, 700, 1060], "equipment_tag"),
            row("TABLE B.2", 88, [900, 300, 1200, 360]),
        ]

        selected, held, report = shortlist_pid_table_microtext.build_shortlist(
            rows,
            pid_pages={79, 80, 81},
            table_pages=set(range(82, 92)),
            identifier_x_min=400,
            identifier_x_max=580,
            category_caps={
                "instrument_tag": 100,
                "process_value": 100,
                "pipe_line_tag": 50,
                "equipment_tag": 30,
            },
        )

        self.assertEqual(len(selected), 4)
        self.assertEqual(len(held), 2)
        self.assertEqual(report["selected_categories"]["instrument_tag"], 1)
        self.assertEqual(report["selected_categories"]["process_value"], 1)
        self.assertEqual(report["selected_categories"]["pipe_line_tag"], 1)
        self.assertEqual(report["selected_categories"]["equipment_tag"], 1)
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))

    def test_rejects_clipped_identifier_and_generic_equipment(self) -> None:
        rows = [
            row("FPT01", 83, [350, 900, 470, 960]),
            row("PUMP", 81, [400, 1000, 500, 1060], "equipment_tag"),
        ]
        selected, held, _report = shortlist_pid_table_microtext.build_shortlist(
            rows,
            pid_pages={81},
            table_pages={83},
            identifier_x_min=400,
            identifier_x_max=580,
            category_caps={},
        )
        self.assertEqual(selected, [])
        self.assertEqual(len(held), 2)


if __name__ == "__main__":
    unittest.main()
