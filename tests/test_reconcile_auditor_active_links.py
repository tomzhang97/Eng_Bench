import json
import tempfile
import unittest
from pathlib import Path

from tools.reconcile_auditor_active_links import (
    CURRENT_REPORT, collect_supported_candidates, compare_reviewed_content,
    release_constraint, registry_active_holds, p,
)


class ActiveAuditContentTests(unittest.TestCase):
    def release_fixture(self, root, holds=None):
        for name in p.ACTIVE_GOLD_PATHS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        registry = root / "derived/human_adjudication/processed_returns/receipt/auditor_decisions.jsonl"
        registry.parent.mkdir(parents=True)
        registry.write_text("")
        path = root / "derived/quality/reconciliation/report.json"
        path.parent.mkdir(parents=True)
        data = path.parent / "registry_active_audit_rechecks.jsonl"
        data.write_text("".join(json.dumps(row) + "\n" for row in (holds or [])))
        report = {
            "status": "PASS", "active_gold_modified": False,
            "active_gold_hashes_after": p.active_gold_hashes(root),
            "input_hashes": {str(registry): p.sha256_file(registry)},
            "output_hashes": {data.name: p.sha256_file(data)},
            "registry_active_audit_flags": len(holds or []),
            "registry_active_flags_by_task": {}, "registry_active_flags_by_split": {},
        }
        path.write_text(json.dumps(report))
        (root / CURRENT_REPORT).write_text(json.dumps({
            "report_path": path.relative_to(root).as_posix(), "report_sha256": p.sha256_file(path),
        }))
        return path, registry, data

    def test_fresh_zero_holds_pass_and_nonzero_holds_block(self):
        for holds in ([], [{"active_gold_identity": "m"}]):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.release_fixture(root, holds)
                result = release_constraint(root)
                self.assertEqual(not holds, result["passes"])
                self.assertEqual(len(holds), result["current"])
                self.assertEqual([], result["issues"])

    def test_stale_inputs_outputs_gold_or_registry_fail_closed(self):
        for changed in ("report", "input", "output", "gold", "registry"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                report, registry, data = self.release_fixture(root)
                path = {"report": report, "input": registry, "output": data,
                        "gold": root / p.ACTIVE_GOLD_PATHS[0],
                        "registry": registry.with_name("new_auditor_decisions.jsonl")}[changed]
                path.write_text("changed")
                self.assertFalse(release_constraint(root)["passes"])
                self.assertTrue(release_constraint(root)["issues"])

    def test_historical_negative_survives_later_positive_and_aliases_group(self):
        row = {"item_id": "m", "source_candidate_id": "c", "split": "test"}
        active = {"m": row, "c": row}
        history = [{"record_id": "c", "decision_code": "2"},
                   {"record_id": "m", "decision_code": "1"},
                   {"record_id": "m", "decision_code": "3"},
                   {"record_id": "staged", "decision_code": "2"}]
        holds = registry_active_holds(history, active)
        self.assertEqual(1, len(holds))
        self.assertEqual("m", holds[0]["active_gold_identity"])
        self.assertEqual(2, len(holds[0]["observations"]))
        self.assertFalse(holds[0]["safe_to_merge_gold"])

    def test_missing_or_untrusted_release_reference_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertFalse(release_constraint(root)["passes"])
            path = root / CURRENT_REPORT
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"report_path": "../outside.json", "report_sha256": "x"}))
            self.assertFalse(release_constraint(root)["passes"])
            self.assertIn("outside", release_constraint(root)["issues"][0])

    def test_matching_fields_do_not_certify_semantics(self):
        active = {"doc_id": "d", "page_index": 0, "bbox": [0, 0, 10, 10], "text_gt": "P1", "category": "equipment_tag"}
        primary = {**active, "candidate_id": "candidate", "proposed_text": "P1"}
        result = compare_reviewed_content(active, primary, "microtext")
        self.assertEqual("reviewed_fields_match", result["status"])
        self.assertFalse(result["semantic_certification"])

    def test_changed_crop_or_category_requires_reconciliation(self):
        active = {"doc_id": "d", "page_index": 0, "bbox": [0, 0, 10, 10], "text_gt": "P1", "category": "equipment_tag"}
        primary = {**active, "bbox": [0, 0, 20, 10], "category": "pin_label", "proposed_text": "P1"}
        result = compare_reviewed_content(active, primary, "microtext")
        self.assertEqual({"bbox", "category"}, set(result["changed_fields"]))

    def test_primary_edits_are_compared_instead_of_stale_proposals(self):
        active = {"doc_id": "d", "page_index": 0, "bbox": [0, 0, 10, 10], "text_gt": "P1", "category": "equipment_tag"}
        primary = {**active, "category": "pin_label", "proposed_text": "P", "review_status": "edited",
                   "corrected_text": "P1", "corrected_category": "equipment_tag"}
        self.assertEqual("reviewed_fields_match", compare_reviewed_content(active, primary, "microtext")["status"])

    def test_supported_candidates_are_deduplicated_and_aggregate_proof(self):
        primary = {
            "candidate": {
                "candidate_id": "candidate",
                "task": "microtext",
                "primary_reviewer_status": "accepted",
            }
        }
        base = {
            "record_id": "candidate",
            "task_type": "microtext",
            "action": "primary_and_auditor_support_pending_release_gates",
            "decision_code": "1",
            "decision_source": "returned_workbook",
            "evidence_sha256": "evidence",
            "source_workbook_sha256": "workbook",
            "assignment_payload_sha256": "payload",
        }
        actions = [
            {**base, "reviewer_id": "auditor_01"},
            {**base, "reviewer_id": "auditor_02"},
        ]
        prepared, conflicts, legacy = collect_supported_candidates(actions, primary, set())
        self.assertEqual(1, len(prepared["microtext"]))
        self.assertEqual(2, len(prepared["microtext"][0]["independent_audit_supports"]))
        self.assertEqual([], conflicts)
        self.assertEqual([], legacy)

    def test_incomplete_legacy_support_is_held_not_prepared(self):
        action = {
            "record_id": "candidate",
            "task_type": "visualdiff",
            "action": "primary_and_auditor_support_pending_release_gates",
            "decision_code": "1",
            "reviewer_id": "auditor_01",
        }
        prepared, conflicts, legacy = collect_supported_candidates(
            [action], {"candidate": {"pair_id": "candidate"}}, set()
        )
        self.assertEqual([], prepared["visualdiff"])
        self.assertEqual([], conflicts)
        self.assertEqual(1, len(legacy))
        self.assertEqual(
            "legacy_audit_provenance_incomplete", legacy[0]["hold_reason"]
        )

    def test_nonpass_history_overrides_positive_candidate_support(self):
        action = {
            "record_id": "candidate",
            "task_type": "microtext",
            "action": "primary_and_auditor_support_pending_release_gates",
            "decision_code": "1",
            "reviewer_id": "auditor_01",
            "evidence_sha256": "evidence",
            "source_workbook_sha256": "workbook",
            "assignment_payload_sha256": "payload",
        }
        prepared, conflicts, legacy = collect_supported_candidates(
            [action], {"candidate": {"candidate_id": "candidate"}}, {"candidate"}
        )
        self.assertEqual([], prepared["microtext"])
        self.assertEqual(1, len(conflicts))
        self.assertEqual([], legacy)


if __name__ == "__main__":
    unittest.main()
