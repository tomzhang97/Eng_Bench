from __future__ import annotations

import unittest
from pathlib import Path

from tools.build_fast_human_audit_delivery import (
    assignments,
    auditor_message_text,
    guide_text,
    instruction_files,
    primary_message_text,
    unsafe_zip_name,
)


class BuildFastHumanAuditDeliveryTests(unittest.TestCase):
    def test_assignments_are_one_primary_plus_ten_auditors(self) -> None:
        rows = assignments(Path("source"), Path("books"))
        self.assertEqual(len(rows), 11)
        self.assertEqual(sum(int(row["rows"]) for row in rows), 618)
        self.assertEqual(rows[0]["name"], "PRIMARY_REVIEW_498.xlsx")
        self.assertEqual(rows[-1]["name"], "AUDITOR_10_REVIEW_12.xlsx")

    def test_role_guides_explain_the_one_step_workflow(self) -> None:
        owner = guide_text()
        primary = primary_message_text()
        auditor = auditor_message_text()
        self.assertIn("你只做 4 件事", owner)
        self.assertIn("内嵌全部 618 张", owner)
        self.assertIn("每一行都只做 3 步", primary)
        self.assertIn("D 列下拉", primary)
        self.assertIn("K 列即可查漏", primary)
        self.assertIn("needs_full_page", primary)
        self.assertIn("独立完成", auditor)
        self.assertIn("K 列即可查漏", auditor)
        self.assertIn("不要复制他人的答案", auditor)
        self.assertEqual(
            set(instruction_files()),
            {
                "00_OWNER_READ_FIRST_ZH.txt",
                "01_MESSAGE_TO_PRIMARY_ZH.txt",
                "02_MESSAGE_TO_AUDITOR_ZH.txt",
            },
        )

    def test_unsafe_zip_name(self) -> None:
        self.assertFalse(unsafe_zip_name("package/AUDITOR_01_REVIEW_12.xlsx"))
        self.assertTrue(unsafe_zip_name("../escape.xlsx"))
        self.assertTrue(unsafe_zip_name("/absolute.xlsx"))


if __name__ == "__main__":
    unittest.main()
