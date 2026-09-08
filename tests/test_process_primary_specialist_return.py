import unittest

from tools.process_primary_specialist_return import (
    interpret_micro,
    interpret_visual,
    stage_specialist_row,
    validate_machine_sheet,
    validate_specialist_identity,
)


def visual_row(*, type_required: bool = False) -> dict:
    return {
        "specialist_index": 1,
        "task": "visualdiff_english",
        "record_id": "visual-1",
        "reserved_split": "dev",
        "source_group": "family-a",
        "evidence_path": "evidence/visual.png",
        "evidence_sha256": "a" * 64,
        "chinese_description": "电阻阻值发生变化。",
        "proposed_english_description": "The resistor value changed from 10R to 220R.",
        "current_change_type": "text_change_candidate",
        "type_required": type_required,
        "old_text": "10R",
        "new_text": "220R",
        "safe_to_merge_gold": False,
    }


def micro_row() -> dict:
    return {
        "specialist_index": 1,
        "task": "microtext_balance",
        "record_id": "micro-1",
        "reserved_split": "test",
        "source_group": "doc-a",
        "evidence_path": "evidence/micro.png",
        "evidence_sha256": "b" * 64,
        "proposed_text": "PT-101",
        "category": "instrument_tag",
        "safe_to_merge_gold": False,
    }


class PrimarySpecialistReturnTests(unittest.TestCase):
    def test_visual_accept_uses_proposed_english(self) -> None:
        decision = interpret_visual(visual_row(), {7: "accepted"})
        self.assertTrue(decision["ready"])
        self.assertEqual(
            decision["effective_english"],
            "The resistor value changed from 10R to 220R.",
        )
        self.assertEqual(decision["effective_type"], "text_change")

    def test_visual_type_required_is_fail_closed(self) -> None:
        decision = interpret_visual(visual_row(type_required=True), {7: "accepted"})
        self.assertFalse(decision["ready"])
        self.assertIn("type_required_missing_corrected_type", decision["blocking_reasons"])
        fixed = interpret_visual(
            visual_row(type_required=True),
            {7: "accepted", 9: "dimension_change"},
        )
        self.assertTrue(fixed["ready"])

    def test_visual_edit_requires_full_english(self) -> None:
        decision = interpret_visual(visual_row(), {7: "edited"})
        self.assertFalse(decision["ready"])
        self.assertIn("edited_requires_corrected_english", decision["blocking_reasons"])

    def test_visual_hold_requires_notes(self) -> None:
        decision = interpret_visual(visual_row(), {7: "needs_full_page"})
        self.assertFalse(decision["ready"])
        fixed = interpret_visual(visual_row(), {7: "needs_full_page", 10: "裁剪无法确认连接关系"})
        self.assertTrue(fixed["ready"])

    def test_micro_accept_requires_substantive_basis(self) -> None:
        decision = interpret_micro(micro_row(), {4: "accepted", 7: "正确"})
        self.assertFalse(decision["ready"])
        fixed = interpret_micro(
            micro_row(),
            {4: "accepted", 7: "PT-101符合仪表位号格式且位于测压支路"},
        )
        self.assertTrue(fixed["ready"])

    def test_micro_edit_requires_a_correction(self) -> None:
        decision = interpret_micro(
            micro_row(),
            {4: "edited", 7: "该文本符合设备位号格式"},
        )
        self.assertFalse(decision["ready"])
        fixed = interpret_micro(
            micro_row(),
            {4: "edited", 6: "equipment_tag", 7: "该文本在图中对应设备位号"},
        )
        self.assertTrue(fixed["ready"])
        self.assertEqual(fixed["effective_category"], "equipment_tag")

    def test_machine_identity_is_immutable(self) -> None:
        row = visual_row()
        machine = {
            1: {index: value for index, value in enumerate([
                "specialist_index", "task", "record_id", "reserved_split", "source_group",
                "evidence_path", "evidence_sha256", "type_required", "safe_to_merge_gold",
            ])},
            2: {
                0: "1", 1: "visualdiff_english", 2: "tampered", 3: "dev",
                4: "family-a", 5: "evidence/visual.png", 6: "a" * 64,
                7: "false", 8: "false",
            },
        }
        issues = validate_machine_sheet(machine, [row])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["field"], "record_id")

    def test_specialist_identity_checks_visible_machine_values(self) -> None:
        row = micro_row()
        headers = [
            "#", "证据图片", "Proposed text", "Proposed category", "审核状态",
            "Corrected text", "Corrected category", "工程依据", "Notes", "完成状态",
        ]
        sheet = {6: {i: value for i, value in enumerate(headers)}, 7: {0: "1", 2: "PT-101", 3: "equipment_tag"}}
        issues = validate_specialist_identity("专项_MicroText3", sheet, [row])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["field"], "category")

    def test_staged_output_is_never_gold_safe(self) -> None:
        source = micro_row()
        decision = interpret_micro(
            source,
            {4: "accepted", 7: "PT-101符合仪表位号格式且位于测压支路"},
        )
        staged = stage_specialist_row(source, decision, "c" * 64, "fixture")
        self.assertFalse(staged["safe_to_merge_gold"])
        self.assertEqual(staged["promotion_state"], "human_reviewed_pending_release_gates")


if __name__ == "__main__":
    unittest.main()
