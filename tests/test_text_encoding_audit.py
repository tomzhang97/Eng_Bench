import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_text_encoding import audit_paths
from tools.mine_microtext_candidates import repair_common_mojibake
from tools.text_encoding import mojibake_signatures
from tools.validate_engbench_v2 import validate_all


class TextEncodingAuditTest(unittest.TestCase):
    def test_detects_and_repairs_common_utf8_latin1_corruption(self) -> None:
        corrupted = "10\u00c2\u00b5F at 85\u00c2\u00b0C"
        self.assertEqual(
            mojibake_signatures(corrupted),
            ("latin1_utf8_lead_c2",),
        )
        self.assertEqual(repair_common_mojibake(corrupted), "10\u00b5F at 85\u00b0C")

    def test_leaves_legitimate_unicode_unchanged(self) -> None:
        text = "10\u00b5F at 85\u00b0C"
        self.assertEqual(mojibake_signatures(text), ())
        self.assertEqual(repair_common_mojibake(text), text)

    def test_queue_audit_separates_benchmark_and_provenance_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rows.jsonl"
            rows = [
                {
                    "candidate_id": "warning",
                    "target_text": "85\u00b0C",
                    "source_raw_text": "85\u00c2\u00b0C",
                },
                {
                    "candidate_id": "error",
                    "target_text": "10\u00c2\u00b5F",
                },
            ]
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            report = audit_paths([path])

        self.assertFalse(report["valid"])
        self.assertEqual(report["benchmark_errors"], 1)
        self.assertEqual(report["provenance_warnings"], 1)

    def test_strict_v2_validation_rejects_benchmark_mojibake(self) -> None:
        items = [
            {
                "id": "mt_bad",
                "question": "What text is shown?",
                "answer": "10\u00c2\u00b5F",
                "split": "train",
                "images": [],
            }
        ]

        report, bad_indices = validate_all(
            items,
            {},
            ".",
            strict=True,
            skip_textlayer=True,
        )

        self.assertIn(0, bad_indices)
        self.assertTrue(any("[Check 13]" in error for error in report.errors))

    def test_strict_v2_validation_ignores_forensic_raw_source_field(self) -> None:
        items = [
            {
                "id": "mt_clean",
                "question": "What text is shown?",
                "answer": "10\u00b5F",
                "source_raw_text": "10\u00c2\u00b5F",
                "split": "train",
                "images": [],
            }
        ]

        report, bad_indices = validate_all(
            items,
            {},
            ".",
            strict=True,
            skip_textlayer=True,
        )

        self.assertNotIn(0, bad_indices)
        self.assertFalse(any("[Check 13]" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
