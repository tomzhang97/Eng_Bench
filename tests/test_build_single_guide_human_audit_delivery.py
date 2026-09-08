import unittest

from tools import build_single_guide_human_audit_delivery as delivery


class SingleGuideHumanAuditDeliveryTests(unittest.TestCase):
    def test_expected_names_are_one_guide_and_eleven_workbooks(self) -> None:
        names = delivery.expected_names()
        self.assertEqual(len(names), 12)
        self.assertIn("00_READ_ME_FIRST_CN.txt", names)
        self.assertIn("PRIMARY_REVIEW_498.xlsx", names)
        self.assertIn("AUDITOR_10_REVIEW_12.xlsx", names)

    def test_guide_contains_distribution_and_keyboard_contract(self) -> None:
        text = delivery.guide_text()
        for term in (
            "30 秒最短版",
            "每个人只发送一个",
            "只填一个黄色答案列",
            "498 条",
            "120 条",
            "Wave39",
            "尺寸值：11.8°",
            "设备标签：MOTOR",
            "引脚/端子/元件标签：L1",
            "管线标签：DRAIN",
            "房间/区域标签：DECK",
            "DECK 是房间/区域标签",
            "VisualDiff 文字变化规则",
            "1=全对",
            "1=是，2=否，3=看不清",
            "不能直接写入 gold",
        ):
            self.assertIn(term, text)

    def test_lean_guide_moves_primary_decision_next_to_machine_content(self) -> None:
        text = delivery.guide_text(lean_primary=True)
        self.assertIn("B 列图片和 C 列机器内容", text)
        self.assertIn("黄色 D 列", text)
        self.assertNotIn("B 列图片和 D 列机器内容", text)


if __name__ == "__main__":
    unittest.main()
