from __future__ import annotations

import unittest

from tools import localize_auditor_supported_visualdiff as localizer


class LocalizeAuditorSupportedVisualDiffTests(unittest.TestCase):
    def row(self, **updates):
        row = {
            "pair_id": "pair-1",
            "human_review_status": "edit",
            "safe_to_merge_gold": False,
            "human_description": "红框内同一工程对象的文字标注发生变化。",
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
            "original_human_description": "红框内同一工程对象的文字标注发生变化。",
            "localized_english_description": "The label changed from GPIO2 to G2.",
            "evidence_anchor": "row-1",
        }
        correction.update(updates)
        return correction

    def test_localizes_human_semantics_with_pinned_support(self):
        row, reasons = localizer.localize_row(
            self.row(),
            self.correction(),
            set(),
            "d" * 64,
            {"path": "derived/quality/evidence.png", "sha256": "e" * 64},
        )
        self.assertEqual([], reasons)
        self.assertEqual(
            "The label changed from GPIO2 to G2.", row["localized_human_description"]
        )
        self.assertEqual(localizer.LOCALIZATION_METHOD, row["localization_method"])
        self.assertFalse(row["safe_to_merge_gold"])

    def test_holds_changed_original_or_missing_audit_provenance(self):
        row, reasons = localizer.localize_row(
            self.row(independent_audit_support={"decision_code": "1"}),
            self.correction(original_human_description="不同内容。"),
            set(),
            "d" * 64,
            {"path": "derived/quality/evidence.png", "sha256": "e" * 64},
        )
        self.assertIsNone(row)
        self.assertIn("original_human_description_mismatch", reasons)
        self.assertIn("hash_complete_independent_audit_support_missing", reasons)

    def test_holds_non_english_or_registered_evidence_issue(self):
        row, reasons = localizer.localize_row(
            self.row(),
            self.correction(localized_english_description="标签发生变化。"),
            {"pair-1"},
            "d" * 64,
            {"path": "derived/quality/evidence.png", "sha256": "e" * 64},
        )
        self.assertIsNone(row)
        self.assertIn("localized_description_not_english", reasons)
        self.assertIn("unresolved_machine_evidence_hold", reasons)


if __name__ == "__main__":
    unittest.main()
