import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.process_primary_intern_catchup_return import (
    build_assignment_capacity,
    build_replacement_coverage,
    engineering_note_valid,
    interpret_decision,
    load_additional_overlap_ids,
    normalize_overlap_decision,
    original_visual_type,
    replacement_alignment_issues,
    stage_row,
    validate_machine_sheet,
    validate_main_identity,
)


def micro_payload(*, engineering: bool = False) -> dict:
    return {
        "primary_index": 1,
        "record_id": "micro-1",
        "task": "microtext",
        "proposed_text": "P-101",
        "category": "equipment_tag",
        "engineering_required": engineering,
        "engineering_reason": "区分设备标签与普通文字" if engineering else "",
    }


def visual_payload(
    *,
    description: str = "Tag A changed to B.",
    engineering: bool = False,
    mandatory_rewrite: bool = False,
) -> dict:
    return {
        "primary_index": 229,
        "record_id": "visual-1",
        "task": "visualdiff",
        "change_type": "text_change_candidate",
        "change_description": description,
        "engineering_required": engineering,
        "engineering_reason": "确认文字是否改变工程含义" if engineering else "",
        "mandatory_description_rewrite": mandatory_rewrite,
    }


def micro_headers() -> dict[int, str]:
    return {
        0: "#",
        1: "证据图片",
        2: "机器内容 + 审核问题",
        3: "机器类别",
        4: "判断 1/2/3/4",
        5: "正确文字（仅 2）",
        6: "正确类别（仅 2）",
        7: "工程深审原因",
        8: "工程依据/说明",
        9: "完成状态",
    }


class PrimaryInternCatchupReturnTests(unittest.TestCase):
    def test_additional_overlap_ids_union_supported_identity_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.jsonl"
            second = Path(temporary) / "second.jsonl"
            first.write_text(
                '{"record_id":"row-a"}\n{"candidate_id":"row-b"}\n',
                encoding="utf-8",
            )
            second.write_text(
                '{"pair_id":"row-c"}\n{"record_id":"row-a"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                load_additional_overlap_ids([first, second]),
                {"row-a", "row-b", "row-c"},
            )

    def test_assignment_capacity_restores_physical_evidence(self) -> None:
        payload = micro_payload()
        payload.update(
            reserved_split="test",
            source_group="doc-a",
            capacity_cohort="fixture",
            auditor_overlap=True,
            retained_from_previous_primary=False,
        )
        source = {
            "candidate_id": "micro-1",
            "doc_id": "doc-a",
            "page_index": 3,
            "bbox": [10, 20, 30, 40],
            "review_status": "machine_held",
            "safe_to_merge_gold": True,
        }

        rows, issues = build_assignment_capacity(
            [payload],
            {"micro-1": source},
            "fixture",
        )

        self.assertEqual(issues, [])
        self.assertEqual(rows[0]["bbox"], [10, 20, 30, 40])
        self.assertEqual(rows[0]["reserved_split"], "test")
        self.assertEqual(rows[0]["review_status"], "needs_review")
        self.assertEqual(rows[0]["current_assignment_status"], "issued_primary_pending_return")
        self.assertFalse(rows[0]["safe_to_merge_gold"])

    def test_assignment_capacity_rejects_missing_geometry(self) -> None:
        payload = micro_payload()
        rows, issues = build_assignment_capacity(
            [payload],
            {"micro-1": {"candidate_id": "micro-1", "doc_id": "doc-a"}},
            "fixture",
        )
        self.assertEqual(rows, [])
        self.assertEqual(issues[0]["reason"], "assignment_capacity_evidence_missing")

    def test_microtext_accept_is_ready(self) -> None:
        result = interpret_decision(micro_payload(), {4: "1"})
        self.assertTrue(result["ready"])
        self.assertEqual(result["effective_text"], "P-101")
        self.assertEqual(result["effective_category"], "equipment_tag")

    def test_microtext_edit_can_change_only_category(self) -> None:
        result = interpret_decision(micro_payload(), {4: "2", 6: "instrument_tag"})
        self.assertTrue(result["ready"])
        self.assertEqual(result["effective_text"], "P-101")
        self.assertEqual(result["effective_category"], "instrument_tag")

    def test_unknown_microtext_must_be_resolved(self) -> None:
        payload = micro_payload()
        payload["category"] = "unknown_microtext"
        accepted = interpret_decision(payload, {4: "1"})
        self.assertFalse(accepted["ready"])
        self.assertIn("merge_candidate_unknown_category", accepted["blocking_reasons"])
        edited = interpret_decision(payload, {4: "2", 6: "equipment_tag"})
        self.assertTrue(edited["ready"])

    def test_engineering_basis_must_be_substantive(self) -> None:
        self.assertFalse(engineering_note_valid("正确"))
        self.assertTrue(engineering_note_valid("PT-101符合仪表位号格式"))
        result = interpret_decision(micro_payload(engineering=True), {4: "1", 8: "对"})
        self.assertFalse(result["ready"])
        self.assertIn("engineering_basis_missing_or_too_generic", result["blocking_reasons"])

    def test_visual_accept_requires_existing_description(self) -> None:
        result = interpret_decision(visual_payload(description=""), {4: "1"})
        self.assertFalse(result["ready"])
        self.assertIn("merge_candidate_missing_description", result["blocking_reasons"])

    def test_visual_edit_with_canonical_type_and_description_is_ready(self) -> None:
        result = interpret_decision(
            visual_payload(description=""),
            {4: "2", 5: "dimension_change", 6: "Dimension changed from 10 to 12 mm."},
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["effective_change_type"], "dimension_change")

    def test_mandatory_visual_rewrite_requires_code_type_and_description(self) -> None:
        payload = visual_payload(
            description="A localized graphic difference may be present.",
            engineering=True,
            mandatory_rewrite=True,
        )
        accepted = interpret_decision(payload, {4: "1", 8: "红框内符号需要具体工程解释"})
        self.assertFalse(accepted["ready"])
        self.assertIn(
            "mandatory_description_rewrite_requires_code_2",
            accepted["blocking_reasons"],
        )
        incomplete = interpret_decision(
            payload,
            {4: "2", 5: "symbol_component_change", 8: "红框内符号需要具体工程解释"},
        )
        self.assertFalse(incomplete["ready"])
        self.assertIn(
            "mandatory_description_rewrite_requires_description",
            incomplete["blocking_reasons"],
        )
        ready = interpret_decision(
            payload,
            {
                4: "2",
                5: "symbol_component_change",
                6: "The connector symbol gains a second mounting contact.",
                8: "新符号增加安装触点，改变连接器的机械接口表达",
            },
        )
        self.assertTrue(ready["ready"])

    def test_visual_machine_type_aliases_are_normalized(self) -> None:
        self.assertEqual(original_visual_type("addition+text"), "addition")
        self.assertEqual(original_visual_type("symbol"), "symbol_component_change")
        self.assertEqual(original_visual_type("geometry_or_dimension_change_candidate"), "")

    def test_main_identity_change_is_detected(self) -> None:
        payload = micro_payload()
        rows = {6: micro_headers(), 7: {0: "99", 3: "equipment_tag", 7: ""}}
        issues = validate_main_identity("主审_MicroText", rows, [payload])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["field"], "primary_index")
        self.assertEqual(issues[0]["record_id"], "micro-1")

    def test_main_header_change_is_global(self) -> None:
        payload = micro_payload()
        headers = micro_headers()
        headers[4] = "changed"
        rows = {6: headers, 7: {0: "1", 3: "equipment_tag", 7: ""}}
        issues = validate_main_identity("主审_MicroText", rows, [payload])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["reason"], "header_changed")
        self.assertNotIn("record_id", issues[0])

    def test_machine_identity_change_is_bound_to_record(self) -> None:
        payload = micro_payload()
        payload.update(
            reserved_split="test",
            source_group="source-a",
            capacity_cohort="release_safe",
            evidence_path="evidence/micro-1.png",
            engineering_index="",
            auditor_overlap=False,
            retained_from_previous_primary=False,
        )
        headers = {
            index: value
            for index, value in enumerate(
                [
                    "primary_index", "task", "record_id", "reserved_split", "source_group",
                    "capacity_cohort", "evidence_path", "engineering_required", "engineering_index",
                    "auditor_overlap", "retained_from_previous_primary", "safe_to_merge_gold",
                ]
            )
        }
        row = {
            0: "1", 1: "microtext", 2: "tampered", 3: "test", 4: "source-a",
            5: "release_safe", 6: "evidence/micro-1.png", 7: "false", 8: "",
            9: "false", 10: "false", 11: "false",
        }
        issues = validate_machine_sheet({1: headers, 2: row}, [payload])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["record_id"], "micro-1")
        self.assertEqual(issues[0]["field"], "record_id")

    def test_overlap_export_uses_auditor_compatible_semantics(self) -> None:
        micro = micro_payload()
        micro["auditor_overlap"] = True
        micro_decision = interpret_decision(micro, {4: "2", 6: "instrument_tag"})
        normalized_micro = normalize_overlap_decision(micro, micro_decision)
        self.assertEqual(normalized_micro["decision"], "pass")
        self.assertEqual(normalized_micro["decision_code"], "1")
        self.assertEqual(normalized_micro["primary_detailed_status"], "edited")

        visual = visual_payload()
        visual["auditor_overlap"] = True
        visual_decision = interpret_decision(visual, {4: "3"})
        normalized_visual = normalize_overlap_decision(visual, visual_decision)
        self.assertEqual(normalized_visual["decision"], "no_engineering_change")
        self.assertEqual(normalized_visual["decision_code"], "2")

    def test_provenance_replacement_alignment_and_coverage(self) -> None:
        source = {
            **micro_payload(),
            "reserved_split": "dev",
            "doc_id": "doc-a",
            "image_path": "pages/doc-a.png",
            "page_index": 3,
            "bbox": [1, 2, 3, 4],
        }
        replacement = {
            **source,
            "provenance_replacement_candidate": True,
            "replacement_for_task": "microtext",
            "replacement_for_split": "dev",
            "replacement_for_category": "equipment_tag",
            "replacement_rights_check": "release_safe_status",
            "replacement_evidence_fingerprint": "microtext:sha256:abc",
            "replacement_evidence_fingerprint_status": "pixel_crop_sha256",
            "replacement_source_unit": "doc-a",
        }
        self.assertEqual(replacement_alignment_issues(source, replacement), [])
        decision = interpret_decision(source, {4: "1"})
        coverage = build_replacement_coverage(
            [replacement],
            {"micro-1": {**source, "auditor_overlap": False}},
            {"micro-1": decision},
            {},
        )
        self.assertEqual(
            coverage[0]["coverage_status"],
            "primary_keep_pending_atomic_migration_gates",
        )
        self.assertEqual(coverage[0]["coverage_mode"], "direct_primary")
        replacement["replacement_for_split"] = "test"
        self.assertIn(
            "replacement_split_mismatch",
            replacement_alignment_issues(source, replacement),
        )

    def test_replacement_coverage_counts_specialist_and_exact_evidence_alias(self) -> None:
        replacement = {
            "candidate_id": "replacement-1",
            "replacement_for_task": "microtext",
            "replacement_for_split": "dev",
            "replacement_for_category": "equipment_tag",
            "replacement_source_unit": "doc-a",
        }
        owner = {**micro_payload(), "record_id": "owner-1"}
        owner_decision = interpret_decision(owner, {4: "1"})
        alias_coverage = build_replacement_coverage(
            [replacement],
            {"owner-1": owner},
            {"owner-1": owner_decision},
            {},
            {},
            {
                "replacement-1": {
                    "record_id": "replacement-1",
                    "review_via_record_id": "owner-1",
                    "coverage_basis": "exact_rendered_evidence_sha256",
                }
            },
        )
        self.assertEqual(alias_coverage[0]["coverage_mode"], "exact_evidence_alias")
        self.assertEqual(
            alias_coverage[0]["coverage_status"],
            "exact_evidence_alias_primary_keep_pending_atomic_migration_gates",
        )
        self.assertTrue(alias_coverage[0]["has_review_assignment"])

        specialist_coverage = build_replacement_coverage(
            [replacement],
            {},
            {},
            {},
            {"replacement-1": {"record_id": "replacement-1"}},
            {},
        )
        self.assertEqual(
            specialist_coverage[0]["coverage_mode"], "engineering_specialist"
        )
        self.assertEqual(
            specialist_coverage[0]["coverage_status"],
            "specialist_assignment_pending_separate_return_processing",
        )

    def test_staged_replacement_retains_migration_contract(self) -> None:
        payload = micro_payload()
        payload["auditor_overlap"] = False
        decision = interpret_decision(payload, {4: "1"})
        replacement = {
            "task": "microtext",
            "source_candidate_id": "source-row-1",
            "reserved_split": "dev",
            "split_reservation_plan": "replacement-plan.json",
            "split_reservation_id": "unit:doc-a",
            "provenance_replacement_candidate": True,
            "provenance_replacement_date_label": "fixture",
            "replacement_for_task": "microtext",
            "replacement_for_split": "dev",
            "replacement_for_category": "equipment_tag",
            "replacement_match_level": "task_split_category",
            "replacement_origin_phase": "future_capacity",
            "replacement_rights_check": "release_safe_status",
            "replacement_source_unit": "doc-a",
            "replacement_evidence_fingerprint": "microtext:sha256:abc",
            "replacement_evidence_fingerprint_status": "pixel_crop_sha256",
        }
        staged = stage_row(
            {
                "candidate_id": "micro-1",
                "safe_to_merge_gold": False,
                "split_reservation_plan": "stale-plan.json",
            },
            payload,
            decision,
            "fixture",
            "a" * 64,
            provenance_replacement=replacement,
        )
        self.assertTrue(staged["provenance_replacement_candidate"])
        self.assertEqual(staged["split_reservation_plan"], "replacement-plan.json")
        self.assertEqual(staged["split_reservation_id"], "unit:doc-a")
        self.assertEqual(
            staged["provenance_replacement_review_status"],
            "human_keep_pending_atomic_migration_gates",
        )
        self.assertFalse(staged["safe_to_merge_gold"])
        self.assertFalse(staged["provenance_replacement_safe_to_retire_active_row"])


if __name__ == "__main__":
    unittest.main()
