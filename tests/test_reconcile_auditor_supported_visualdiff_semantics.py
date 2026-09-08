from __future__ import annotations

import unittest

from tools import reconcile_auditor_supported_visualdiff_semantics as semantic


class ReconcileAuditorSupportedVisualDiffSemanticsTests(unittest.TestCase):
    def row(self, **updates):
        row = {
            "pair_id": "pair-1",
            "human_review_status": "edit",
            "safe_to_merge_gold": False,
            "change_type": "deletion",
            "human_description": "A localized graphic or schematic-symbol difference may be present.",
            "independent_audit_support": {
                "decision_code": "1",
                "assignment_payload_sha256": "a" * 64,
                "evidence_sha256": "b" * 64,
                "source_workbook_sha256": "c" * 64,
            },
        }
        row.update(updates)
        return row

    def correction(self, **updates):
        correction = {
            "pair_id": "pair-1",
            "original_human_description": "A localized graphic or schematic-symbol difference may be present.",
            "final_english_description": "The TP1 test-point connection was removed.",
            "expected_change_type": "deletion",
            "machine_visual_reconciliation": "TP1 is connected in old and absent in new.",
            "evidence_anchor": "row-1",
        }
        correction.update(updates)
        return correction

    def test_reconciles_vague_reviewed_semantics(self):
        row, reasons = semantic.reconcile_row(
            self.row(),
            self.correction(),
            set(),
            "d" * 64,
            {"path": "derived/quality/evidence.png", "sha256": "e" * 64},
        )
        self.assertEqual([], reasons)
        self.assertEqual(
            "The TP1 test-point connection was removed.",
            row["machine_reconciled_description"],
        )
        self.assertEqual(semantic.METHOD, row["semantic_reconciliation"]["method"])
        self.assertFalse(row["safe_to_merge_gold"])

    def test_holds_changed_type_or_missing_support(self):
        row, reasons = semantic.reconcile_row(
            self.row(independent_audit_support={"decision_code": "1"}),
            self.correction(expected_change_type="addition"),
            set(),
            "d" * 64,
            {"path": "derived/quality/evidence.png", "sha256": "e" * 64},
        )
        self.assertIsNone(row)
        self.assertIn("reviewed_change_type_mismatch", reasons)
        self.assertIn("hash_complete_independent_audit_support_missing", reasons)

    def test_holds_non_template_or_registered_evidence_issue(self):
        row, reasons = semantic.reconcile_row(
            self.row(human_description="A definite human description."),
            self.correction(original_human_description="A definite human description."),
            {"pair-1"},
            "d" * 64,
            {"path": "derived/quality/evidence.png", "sha256": "e" * 64},
        )
        self.assertIsNone(row)
        self.assertIn("original_description_is_not_graphic_template", reasons)
        self.assertIn("unresolved_machine_evidence_hold", reasons)


if __name__ == "__main__":
    unittest.main()
