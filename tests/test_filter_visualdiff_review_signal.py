from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.filter_visualdiff_review_signal import filter_rows


class FilterVisualDiffReviewSignalTest(unittest.TestCase):
    def test_keeps_consistent_text_relations(self) -> None:
        rows = [
            {"pair_id": "add", "change_type": "addition+text", "old_text": "", "new_text": "R1"},
            {"pair_id": "delete", "change_type": "deletion+text", "old_text": "R1", "new_text": ""},
            {"pair_id": "replace", "change_type": "text", "old_text": "R1", "new_text": "R2"},
        ]

        passing, held, report = filter_rows(Path("."), rows)

        self.assertEqual([row["pair_id"] for row in passing], ["add", "delete", "replace"])
        self.assertEqual(held, [])
        self.assertEqual(report["passing_rows"], 3)
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in passing))
        self.assertTrue(all(row["review_status"] == "needs_review" for row in passing))

    def test_holds_inconsistent_text_relation(self) -> None:
        passing, held, _ = filter_rows(
            Path("."),
            [{"pair_id": "same", "change_type": "text", "old_text": "R1", "new_text": " R1 "}],
        )

        self.assertEqual(passing, [])
        self.assertEqual(held[0]["review_status"], "machine_held")

    def test_keeps_only_one_sided_symbol_ink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blank = root / "blank.png"
            marked = root / "marked.png"
            Image.new("RGB", (40, 40), "white").save(blank)
            image = Image.new("RGB", (40, 40), "white")
            ImageDraw.Draw(image).rectangle((10, 10, 29, 29), fill="black")
            image.save(marked)
            row = {
                "pair_id": "symbol",
                "change_type": "symbol",
                "image_old": str(blank),
                "image_new": str(marked),
                "bbox_old": [10, 10, 30, 30],
                "bbox_new": [10, 10, 30, 30],
            }

            passing, held, _ = filter_rows(root, [row])

            self.assertEqual(len(passing), 1)
            self.assertEqual(held, [])
            self.assertEqual(passing[0]["review_signal_filter"]["ink_asymmetry"], 1.0)

    def test_keeps_one_sided_addition_and_deletion_ink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blank = root / "blank.png"
            marked = root / "marked.png"
            Image.new("RGB", (40, 40), "white").save(blank)
            image = Image.new("RGB", (40, 40), "white")
            ImageDraw.Draw(image).rectangle((10, 10, 29, 29), fill="black")
            image.save(marked)
            rows = [
                {
                    "pair_id": "addition",
                    "change_type": "addition",
                    "image_old": str(blank),
                    "image_new": str(marked),
                    "bbox_old": [10, 10, 30, 30],
                    "bbox_new": [10, 10, 30, 30],
                },
                {
                    "pair_id": "deletion",
                    "change_type": "deletion",
                    "image_old": str(marked),
                    "image_new": str(blank),
                    "bbox_old": [10, 10, 30, 30],
                    "bbox_new": [10, 10, 30, 30],
                },
            ]

            passing, held, _ = filter_rows(root, rows)

            self.assertEqual([row["pair_id"] for row in passing], ["addition", "deletion"])
            self.assertEqual(held, [])
            self.assertTrue(
                all(
                    row["review_signal_filter"]["reason"]
                    == "one_sided_graphic_signal"
                    for row in passing
                )
            )

    def test_holds_two_sided_symbol_ink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "old.png"
            new = root / "new.png"
            for path in (old, new):
                image = Image.new("RGB", (40, 40), "white")
                ImageDraw.Draw(image).rectangle((10, 10, 29, 29), fill="black")
                image.save(path)
            row = {
                "pair_id": "symbol",
                "change_type": "symbol",
                "image_old": str(old),
                "image_new": str(new),
                "bbox_old": [10, 10, 30, 30],
                "bbox_new": [10, 10, 30, 30],
            }

            passing, held, _ = filter_rows(root, [row])

            self.assertEqual(passing, [])
            self.assertEqual(len(held), 1)

    def test_holds_missing_symbol_evidence(self) -> None:
        passing, held, _ = filter_rows(
            Path("."),
            [{"pair_id": "missing", "change_type": "symbol"}],
        )

        self.assertEqual(passing, [])
        self.assertIn("invalid_graphic_evidence", held[0]["review_signal_filter"]["reason"])


if __name__ == "__main__":
    unittest.main()
