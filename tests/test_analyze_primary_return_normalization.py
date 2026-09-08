import unittest

from tools.analyze_primary_return_normalization import normalize_visual_cells
from tools.process_primary_intern_catchup_return import interpret_decision

from tests.test_process_primary_intern_catchup_return import visual_payload


class AnalyzePrimaryReturnNormalizationTests(unittest.TestCase):
    def test_layout_only_feedback_becomes_no_change_reject(self) -> None:
        cells, action = normalize_visual_cells(
            {
                4: "2",
                5: "layout_only_no_change",
                6: "红框偏移，但工程内容一致。",
                8: "对比两图可见内容一致，仅红框位置不同。",
            }
        )
        self.assertIsNotNone(action)
        self.assertEqual(cells[4], "3")
        self.assertEqual(cells[5], "")
        self.assertEqual(cells[6], "")
        decision = interpret_decision(
            visual_payload(engineering=True, mandatory_rewrite=True), cells
        )
        self.assertTrue(decision["ready"])
        self.assertEqual(decision["status"], "rejected_no_change")

    def test_unclear_feedback_becomes_context_hold(self) -> None:
        cells, action = normalize_visual_cells(
            {4: "2", 5: "unclear", 8: "两张图看着不是同一个位置或对象。"}
        )
        self.assertIsNotNone(action)
        self.assertEqual(cells[4], "4")
        decision = interpret_decision(
            visual_payload(engineering=True, mandatory_rewrite=True), cells
        )
        self.assertTrue(decision["ready"])
        self.assertEqual(decision["status"], "needs_context")

    def test_unrecognized_type_is_not_machine_normalized(self) -> None:
        cells, action = normalize_visual_cells({4: "2", 5: "other"})
        self.assertIsNone(action)
        self.assertEqual(cells[4], "2")
        self.assertEqual(cells[5], "other")


if __name__ == "__main__":
    unittest.main()
