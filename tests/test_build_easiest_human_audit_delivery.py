from __future__ import annotations

import unittest
from pathlib import Path

from tools import build_easiest_human_audit_delivery as delivery


class EasiestHumanAuditDeliveryTests(unittest.TestCase):
    def test_assignments_cover_primary_and_ten_auditors(self) -> None:
        rows = delivery.assignments(Path("source"), Path("workbooks"))
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[0]["name"], "PRIMARY_REVIEW_498.xlsx")
        self.assertEqual(rows[0]["rows"], 498)
        self.assertEqual(sum(row["rows"] for row in rows[1:]), 120)
        self.assertEqual(sum(row["expected_media"] for row in rows), 618)

    def test_guides_make_required_work_minimal_and_explicit(self) -> None:
        self.assertIn("只做 4 步", delivery.owner_guide())
        self.assertIn("大多数行选完就直接做下一行", delivery.primary_guide())
        self.assertIn("48 条文字型 VisualDiff", delivery.primary_guide())
        self.assertIn("每行只做一次选择", delivery.auditor_guide())
        self.assertIn("不要求你改文字或写描述", delivery.auditor_guide())

    def test_zip_path_safety(self) -> None:
        self.assertFalse(delivery.unsafe_zip_name("packet/file.xlsx"))
        self.assertTrue(delivery.unsafe_zip_name("../file.xlsx"))
        self.assertTrue(delivery.unsafe_zip_name("/absolute/file.xlsx"))


if __name__ == "__main__":
    unittest.main()
