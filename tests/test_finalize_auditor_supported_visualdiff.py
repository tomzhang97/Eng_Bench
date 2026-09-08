from __future__ import annotations

import unittest

from tools import finalize_auditor_supported_visualdiff as finalizer


class FinalizeAuditorSupportedVisualDiffTests(unittest.TestCase):
    def row(self, **updates):
        row = {
            "pair_id": "pair-1",
            "human_review_status": "edit",
            "safe_to_merge_gold": False,
            "change_type": "text_change",
            "old_text": "SCLK",
            "new_text": "SCK",
            "human_description": "Localized text may have changed from 'SCLK' to 'SCK'.",
            "independent_audit_support": {
                "decision_code": "1",
                "assignment_payload_sha256": "a" * 64,
                "evidence_sha256": "b" * 64,
                "source_workbook_sha256": "c" * 64,
            },
        }
        row.update(updates)
        return row

    def test_finalizes_exact_reviewed_text_change(self):
        row, reasons = finalizer.finalize_row(
            self.row(), set(), ["derived/quality/evidence.png"], "d" * 64
        )
        self.assertEqual([], reasons)
        self.assertEqual(
            'The engineering text changed from "SCLK" to "SCK".',
            row["machine_final_description"],
        )
        self.assertEqual(
            "machine_epistemic_normalization",
            row["description_finalization"]["method"],
        )
        self.assertFalse(row["safe_to_merge_gold"])

    def test_finalizes_exact_addition_and_deletion(self):
        addition = self.row(
            change_type="addition",
            old_text="",
            new_text="+3V3",
            human_description="Localized text may have been added: '+3V3'.",
        )
        deletion = self.row(
            pair_id="pair-2",
            change_type="deletion",
            old_text=">DRGNO",
            new_text="",
            human_description="Localized text may have been removed: '>DRGNO'.",
        )
        added, add_reasons = finalizer.finalize_row(addition, set(), ["e.png"], "d" * 64)
        removed, remove_reasons = finalizer.finalize_row(deletion, set(), ["e.png"], "d" * 64)
        self.assertEqual([], add_reasons)
        self.assertEqual([], remove_reasons)
        self.assertEqual('The engineering text "+3V3" was added.', added["machine_final_description"])
        self.assertEqual('The engineering text ">DRGNO" was removed.', removed["machine_final_description"])

    def test_holds_text_correspondence_mismatch(self):
        row, reasons = finalizer.finalize_row(
            self.row(new_text="SCK1"), set(), ["e.png"], "d" * 64
        )
        self.assertIsNone(row)
        self.assertIn("template_text_does_not_match_old_new_text", reasons)

    def test_holds_graphic_template_and_missing_audit_hashes(self):
        support = {"decision_code": "1"}
        row, reasons = finalizer.finalize_row(
            self.row(
                old_text="",
                new_text="",
                change_type="symbol_component_change",
                human_description="A localized graphic or schematic-symbol difference may be present.",
                independent_audit_support=support,
            ),
            set(),
            ["e.png"],
            "d" * 64,
        )
        self.assertIsNone(row)
        self.assertIn("graphic_or_unknown_template_requires_semantic_review", reasons)
        self.assertIn("independent_audit_evidence_sha256_missing", reasons)

    def test_holds_registered_machine_evidence_issue(self):
        row, reasons = finalizer.finalize_row(
            self.row(), {"pair-1"}, ["e.png"], "d" * 64
        )
        self.assertIsNone(row)
        self.assertIn("unresolved_machine_evidence_hold", reasons)


if __name__ == "__main__":
    unittest.main()
