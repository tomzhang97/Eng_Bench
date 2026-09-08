from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import process_simple_multi_reviewer_returns as returns


def micro_control() -> dict[str, str]:
    return {
        "primary_index": "7",
        "audit_index": "2",
        "candidate_id": "micro_007",
        "pack_name": "pack_a",
        "category": "equipment_tag",
        "proposed_text": "P-101",
        "page_path": "../pages/p001.png",
        "crop_path": "../crops/micro_007.png",
    }


def visual_control() -> dict[str, str]:
    return {
        "primary_index": "11",
        "audit_index": "10",
        "pair_id": "visual_011",
        "pack_name": "pack_v",
        "change_type": "text_change_candidate",
        "old_text": "A",
        "new_text": "B",
        "old_page_path": "../old/p001.png",
        "new_page_path": "../new/p001.png",
        "panel_path": "../panels/visual_011.png",
    }


def ready_decision(
    record_id: str,
    task: str,
    reviewer_id: str,
    status: str,
    decision_class: str,
    *,
    effective_text: str = "",
    effective_category: str = "",
    description: str = "",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "task": task,
        "reviewer_id": reviewer_id,
        "status": status,
        "decision_class": decision_class,
        "effective_text": effective_text,
        "effective_category": effective_category,
        "description": description,
        "notes": "",
        "ready": True,
        "primary_index": "1",
        "evidence_path": "evidence.png",
        "page_path": "page.png",
        "old_page_path": "",
        "new_page_path": "",
    }


class SimpleMultiReviewerReturnTest(unittest.TestCase):
    def test_text_preserves_zero_page_indices(self) -> None:
        self.assertEqual(returns.text(0), "0")

    def test_microtext_edited_can_correct_only_category(self) -> None:
        row = {
            "你的结论": "edited",
            "正确文字（仅 edited）": "",
            "正确类别（仅 edited）": "instrument_tag",
            "原因/备注": "",
        }

        decision = returns.micro_human_decision(row, micro_control())

        self.assertTrue(decision["human_complete"])
        self.assertEqual(decision["effective_text"], "P-101")
        self.assertEqual(decision["effective_category"], "instrument_tag")

    def test_microtext_reject_and_visual_edit_require_explanatory_content(self) -> None:
        micro = returns.micro_human_decision({"你的结论": "rejected"}, micro_control())
        visual = returns.visual_human_decision({"你的结论": "edit"}, visual_control())

        self.assertIn("rejected_requires_reason", micro["human_reasons"])
        self.assertIn("edit_requires_description", visual["human_reasons"])

    def test_visual_valid_is_not_final_for_todo_cohort(self) -> None:
        decision = returns.visual_human_decision({"你的结论": "valid"}, visual_control())

        self.assertFalse(decision["human_complete"])
        self.assertIn("legacy_valid_not_allowed_for_todo_description", decision["human_reasons"])

    def test_immutable_fields_detect_machine_suggestion_tampering(self) -> None:
        control = micro_control()
        row = {
            "#": "7",
            "candidate_id": "micro_007",
            "primary_index": "7",
            "机器建议（不一定对）": "changed",
            "整页路径（不确定时复制打开）": "../pages/p001.png",
        }

        reasons = returns.immutable_reasons("microtext", row, control, primary=True)

        self.assertEqual(reasons, ["machine_suggestion_changed"])

    def test_microtext_accepted_and_equivalent_edit_agree(self) -> None:
        assignment = {
            "record_id": "micro_007",
            "task": "microtext",
            "pack_name": "pack_a",
            "primary_index": "7",
            "auditor_id": "auditor_01",
        }
        primary = ready_decision(
            "micro_007",
            "microtext",
            "primary_reviewer",
            "accepted",
            "keep",
            effective_text="P-101",
            effective_category="equipment_tag",
        )
        auditor = ready_decision(
            "micro_007",
            "microtext",
            "auditor_01",
            "edited",
            "keep",
            effective_text=" P-101 ",
            effective_category="equipment_tag",
        )

        comparison = returns.compare_pair(assignment, primary, auditor)

        self.assertTrue(comparison["pair_complete"])
        self.assertTrue(comparison["exact_outcome_agreement"])
        self.assertEqual(comparison["recommended_action"], "no_conflict")

    def test_visual_description_difference_requires_adjudication(self) -> None:
        assignment = {
            "record_id": "visual_011",
            "task": "visualdiff",
            "pack_name": "pack_v",
            "primary_index": "11",
            "auditor_id": "auditor_01",
        }
        primary = ready_decision(
            "visual_011",
            "visualdiff",
            "primary_reviewer",
            "edit",
            "keep",
            description="Tag A changed to B.",
        )
        auditor = ready_decision(
            "visual_011",
            "visualdiff",
            "auditor_01",
            "edit",
            "keep",
            description="The equipment label changed.",
        )

        comparison = returns.compare_pair(assignment, primary, auditor)

        self.assertTrue(comparison["class_agreement"])
        self.assertFalse(comparison["exact_outcome_agreement"])
        self.assertIn("description_disagreement", comparison["conflict_reasons"])
        self.assertEqual(comparison["recommended_action"], "human_adjudication")

    def test_staging_holds_audited_conflict_and_stages_single_review(self) -> None:
        single = ready_decision(
            "micro_single",
            "microtext",
            "primary_reviewer",
            "accepted",
            "keep",
            effective_text="A-1",
            effective_category="equipment_tag",
        )
        conflict = ready_decision(
            "micro_conflict",
            "microtext",
            "primary_reviewer",
            "accepted",
            "keep",
            effective_text="B-1",
            effective_category="equipment_tag",
        )
        comparison = {
            "record_id": "micro_conflict",
            "pair_complete": True,
            "exact_outcome_agreement": False,
            "auditor_id": "auditor_02",
        }
        frozen = {
            "microtext": {
                "micro_single": [
                    (Path("review_packs/pack_a/manifest.jsonl"), {"candidate_id": "micro_single"})
                ]
            },
            "visualdiff": {},
        }
        micro, visual, holds, resolutions = returns.build_staging(
            [single, conflict],
            [comparison],
            "2026-08-08",
            frozen,
            {"micro_single"},
        )

        self.assertEqual(len(micro), 1)
        self.assertFalse(visual)
        self.assertEqual(micro[0]["promotion_state"], "human_reviewed_pending_release_gates")
        self.assertFalse(micro[0]["safe_to_merge_gold"])
        self.assertEqual(holds[0]["hold_reasons"], "independent_review_conflict")
        self.assertEqual(len(resolutions), 1)

    def test_return_zip_rejects_unsafe_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "return.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.xlsx", b"not a workbook")

            files, report = returns.collect_return_workbooks(archive_path, root / "extract")

            self.assertFalse(files)
            self.assertTrue(report["issues"])


if __name__ == "__main__":
    unittest.main()
