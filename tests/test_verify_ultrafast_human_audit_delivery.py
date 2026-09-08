from __future__ import annotations

import unittest
from pathlib import Path

from tools import verify_ultrafast_human_audit_delivery as verify


class VerifyUltrafastHumanAuditDeliveryTests(unittest.TestCase):
    def test_resolve_keeps_absolute_and_anchors_relative_paths(self) -> None:
        root = Path("C:/example/root")
        self.assertEqual(verify.resolve(root, Path("packet.zip")), root / "packet.zip")
        absolute = Path("C:/example/packet.zip")
        self.assertEqual(verify.resolve(root, absolute), absolute)

    def test_markdown_reports_flat_contract(self) -> None:
        markdown = verify.render_markdown(
            {
                "goal": "Gold v2.0 Global",
                "valid": True,
                "primary_rows": 498,
                "workbook_count": 11,
                "embedded_images": 618,
                "unique_double_review_rows": 120,
                "gold_rows_modified": 0,
                "issues": [],
                "zip": {
                    "entry_count": 12,
                    "root_files_only": True,
                    "nested_zip_files": 0,
                    "sha256": "abc",
                },
            }
        )
        self.assertIn("Gold v2.0 Global", markdown)
        self.assertIn("Flat root files only: `true`", markdown)
        self.assertIn("Embedded images: `618`", markdown)


if __name__ == "__main__":
    unittest.main()
