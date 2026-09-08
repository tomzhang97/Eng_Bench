from __future__ import annotations

import unittest

from tools.build_minimal_human_audit_delivery import (
    auditor_message_text,
    expected_names,
    owner_text,
    primary_message_text,
)


class BuildMinimalHumanAuditDeliveryTests(unittest.TestCase):
    def test_flat_delivery_has_three_guides_and_eleven_workbooks(self) -> None:
        names = expected_names()
        self.assertEqual(len(names), 14)
        self.assertIn("PRIMARY_REVIEW_498.xlsx", names)
        self.assertIn("AUDITOR_10_REVIEW_12.xlsx", names)
        self.assertFalse(any(name.lower().endswith(".zip") for name in names))

    def test_owner_instructions_are_copy_ready(self) -> None:
        text = owner_text(universal_primary=True)
        self.assertIn("你只做 3 步", text)
        self.assertIn("主审 498 条", text)
        self.assertIn("10 位复核员各 12 条", text)
        self.assertIn("Wave39 DOE 的 95 条", text)
        self.assertIn("不需要拆成 10 个 ZIP", text)
        self.assertIn("主审只有一个审核页", text)

    def test_primary_instructions_cover_fast_path_and_edge_cases(self) -> None:
        text = primary_message_text()
        self.assertIn("输入 1 个数字，再按 Enter", text)
        self.assertIn("MicroText 数字", text)
        self.assertIn("VisualDiff 数字", text)
        self.assertIn("截图缺字母，选 3", text)
        self.assertIn("删除线划掉：选 3", text)

    def test_universal_primary_instructions_use_one_four_key_scheme(self) -> None:
        text = primary_message_text(universal_primary=True)
        self.assertIn("两类任务永远使用同一套数字", text)
        self.assertIn("1 = 全对", text)
        self.assertIn("2 = 修改", text)
        self.assertIn("3 = 剔除", text)
        self.assertIn("4 = 看不清", text)
        self.assertIn("唯一的“审核498条”页", text)
        self.assertIn("黄色 E 列", text)
        self.assertIn("只有输入 2", text)
        self.assertIn("unknown", text)
        self.assertIn("普通引线、边框或下划线不算删除线", text)

    def test_auditor_instructions_require_one_independent_number(self) -> None:
        text = auditor_message_text()
        self.assertIn("独立复核 12 条", text)
        self.assertIn("1 = 是", text)
        self.assertIn("2 = 否", text)
        self.assertIn("3 = 看不清", text)
        self.assertIn("不要参考其他人的答案", text)
        self.assertIn("只回答 C 列", text)
        self.assertIn("不需要改文字", text)


if __name__ == "__main__":
    unittest.main()
