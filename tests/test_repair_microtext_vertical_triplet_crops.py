from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import repair_microtext_vertical_triplet_crops as repair


class RepairMicrotextVerticalTripletCropsTests(unittest.TestCase):
    def test_centered_vertical_bbox_reduces_only_height(self) -> None:
        self.assertEqual(
            repair.centered_vertical_bbox([10, 40, 90, 160], 44, 100, 200),
            [10, 78, 90, 122],
        )
        with self.assertRaises(ValueError):
            repair.centered_vertical_bbox([10, 40, 90, 80], 44, 100, 200)

    def test_parse_row_numbers_is_one_based_sorted_and_unique(self) -> None:
        self.assertEqual(repair.parse_row_numbers(["3,1", "2", "3"]), [1, 2, 3])
        with self.assertRaises(ValueError):
            repair.parse_row_numbers(["0"])

    def test_build_repairs_is_deterministic_and_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "page.png"
            Image.new("RGB", (100, 200), "white").save(image_path)
            input_path = root / "rows.jsonl"
            row = {
                "candidate_id": "ocrcand__source",
                "doc_id": "doc",
                "page_index": 7,
                "bbox": [10, 40, 90, 160],
                "image_path": "page.png",
                "proposed_text": "4.176",
                "category": "dimension_value",
                "reserved_split": "test",
                "review_status": "needs_review",
                "safe_to_merge_gold": False,
            }
            input_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            first, report = repair.build_repairs(
                input_path,
                [1],
                root=root,
                crop_height=44,
            )
            second, _ = repair.build_repairs(
                input_path,
                [1],
                root=root,
                crop_height=44,
            )
            self.assertEqual(first, second)
            self.assertEqual(first[0]["bbox"], [10, 78, 90, 122])
            self.assertEqual(first[0]["source_candidate_id"], "ocrcand__source")
            self.assertTrue(first[0]["candidate_id"].startswith("repaircand__"))
            self.assertFalse(first[0]["safe_to_merge_gold"])
            self.assertEqual(report["repaired_rows"], 1)
            self.assertEqual(report["reserved_splits"], ["test"])


if __name__ == "__main__":
    unittest.main()
