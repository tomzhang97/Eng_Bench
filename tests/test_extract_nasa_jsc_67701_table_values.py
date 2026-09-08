from __future__ import annotations

import unittest

from tools import extract_nasa_jsc_67701_table_values as module


class ExtractNasaJsc67701TableValuesTests(unittest.TestCase):
    def test_normalize_and_classify_supported_engineering_values(self) -> None:
        self.assertEqual(module.classify_token(".003"), (".003", "dimension_value"))
        self.assertEqual(module.classify_token("1.00"), ("1.00", "dimension_value"))
        self.assertEqual(
            module.classify_token("� .015"), ("±.015", "tolerance_value")
        )
        self.assertEqual(
            module.classify_token(".003 - .005"), (".003-.005", "tolerance_value")
        )
        self.assertIsNone(module.classify_token("12"))
        self.assertIsNone(module.classify_token("Page 12 of 43"))

    def test_build_candidates_filters_headers_and_deduplicates(self) -> None:
        source_rows = [
            {"page": 23, "text": ".003", "bbox_px": [100, 600, 150, 630]},
            {"page": 23, "text": "� .015", "bbox_px": [200, 700, 260, 730]},
            {"page": 23, "text": "12", "bbox_px": [300, 800, 340, 830]},
            {"page": 23, "text": "1.00", "bbox_px": [100, 200, 160, 230]},
            {"page": 23, "text": ".003", "bbox_px": [100, 600, 150, 630]},
        ]
        rows, held = module.build_candidates(
            source_rows,
            {23: (2400, 3200)},
            version_id="test-version",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row["category"] for row in rows],
            ["dimension_value", "tolerance_value"],
        )
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in rows))
        self.assertEqual(held["duplicate_textlayer_span"], 1)
        self.assertEqual(held["outside_engineering_content_band"], 1)
        self.assertEqual(held["non_atomic_decimal_or_tolerance"], 1)
        self.assertNotEqual(rows[0]["candidate_id"], rows[1]["candidate_id"])


if __name__ == "__main__":
    unittest.main()
