from __future__ import annotations

import unittest

from tools import build_multi_reviewer_handoff


def candidate(index: int) -> dict[str, object]:
    return {
        "record_id": f"micro_{index:03d}",
        "candidate_id": f"micro_{index:03d}",
        "task": "microtext",
        "pack_name": f"pack_{index % 6}",
        "category": ("dimension_value", "room_label", "equipment_tag", "pin_label")[
            index % 4
        ],
        "primary_index": index + 1,
    }


def pair(index: int) -> dict[str, object]:
    return {
        "record_id": f"visual_{index:03d}",
        "pair_id": f"visual_{index:03d}",
        "task": "visualdiff",
        "pack_name": "visual_pack",
        "project_id": f"family_{index % 15}",
        "change_type": ("text_change_candidate", "schematic_change_candidate")[index % 2],
        "primary_index": 201 + index,
    }


class MultiReviewerHandoffTest(unittest.TestCase):
    def test_assigns_ten_disjoint_balanced_twelve_row_audits(self) -> None:
        rows = [candidate(index) for index in range(140)] + [pair(index) for index in range(60)]

        first = build_multi_reviewer_handoff.assign_auditors(
            rows,
            auditor_count=10,
            microtext_per_auditor=9,
            visualdiff_per_auditor=3,
            seed="unit-test-seed",
        )
        second = build_multi_reviewer_handoff.assign_auditors(
            rows,
            auditor_count=10,
            microtext_per_auditor=9,
            visualdiff_per_auditor=3,
            seed="unit-test-seed",
        )

        self.assertEqual(
            [row["assignment_id"] for row in first],
            [row["assignment_id"] for row in second],
        )
        self.assertEqual(len(first), 120)
        self.assertEqual(len({row["record_id"] for row in first}), 120)
        for number in range(1, 11):
            auditor_id = f"auditor_{number:02d}"
            assignment = [row for row in first if row["auditor_id"] == auditor_id]
            self.assertEqual(len(assignment), 12)
            self.assertEqual(sum(row["task"] == "microtext" for row in assignment), 9)
            self.assertEqual(sum(row["task"] == "visualdiff" for row in assignment), 3)

    def test_instructions_cover_independence_and_return_contract(self) -> None:
        root = build_multi_reviewer_handoff.root_instructions(498, 10, 12)
        primary = build_multi_reviewer_handoff.primary_instructions()
        auditor = build_multi_reviewer_handoff.auditor_instructions(12)

        self.assertIn("Gold v2.0 Global", root)
        self.assertIn("完整解压", root)
        self.assertIn("不得互看", root)
        self.assertIn("PRIMARY_REVIEW_498.xlsx", root)
        self.assertIn("OCR 猜出了完整文字", primary)
        self.assertIn("完全相同不是 layout", primary)
        self.assertIn("盲审", auditor)
        self.assertIn("9 条 MicroText + 3 条 VisualDiff", root)


if __name__ == "__main__":
    unittest.main()
