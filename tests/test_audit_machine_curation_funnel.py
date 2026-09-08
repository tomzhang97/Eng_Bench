from __future__ import annotations

import unittest

from tools.audit_machine_curation_funnel import build_report


class AuditMachineCurationFunnelTests(unittest.TestCase):
    def test_valid_funnel_quantifies_machine_reduction(self) -> None:
        report = build_report(
            {"input_rows": 100, "selected_rows": 25, "held_rows": 75},
            {"totals": {"input_rows": 25, "kept_rows": 20, "held_rows": 5, "corrected_rows": 3}},
            {"files": [{"rows": 20, "issues": []}]},
            {"ready_records": 4},
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["machine_row_decisions_removed"], 80)
        self.assertEqual(report["human_review_rows"], 20)
        self.assertEqual(report["human_work_reduction_rate"], 0.8)
        self.assertEqual(report["registered_source_docs"], 4)

    def test_fails_when_stage_or_packet_counts_drift(self) -> None:
        report = build_report(
            {"input_rows": 10, "selected_rows": 3, "held_rows": 6},
            {"totals": {"input_rows": 4, "kept_rows": 3, "held_rows": 1}},
            {"files": [{"rows": 2, "issues": ["missing crop"]}]},
        )

        self.assertFalse(report["valid"])
        self.assertIn("pattern_filter_count_mismatch", report["issues"])
        self.assertIn("visual_input_does_not_match_pattern_output", report["issues"])
        self.assertIn("packet_rows_do_not_match_visual_output", report["issues"])
        self.assertIn("packet_has_structural_issues", report["issues"])

    def test_supports_standalone_visual_and_packet_verifier_schema(self) -> None:
        report = build_report(
            {"input_rows": 100, "selected_rows": 25, "held_rows": 75},
            {
                "input_rows": 25,
                "selected_rows": 22,
                "held_rows": 3,
                "corrected_rows": 4,
                "active_gold_modified": False,
            },
            {
                "valid": True,
                "counts": {"checklist_rows": 22},
                "issues": [],
            },
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["machine_row_decisions_removed"], 78)
        self.assertEqual(report["human_review_rows"], 22)
        self.assertEqual(report["machine_corrected_rows"], 4)

    def test_supports_evidence_audit_passing_rows_schema(self) -> None:
        report = build_report(
            {"input_rows": 100, "selected_rows": 25, "held_rows": 75},
            {
                "totals": {
                    "input_rows": 25,
                    "kept_rows": 20,
                    "held_rows": 5,
                    "corrected_rows": 3,
                }
            },
            {"input_rows": 20, "passing_rows": 20, "held_rows": 0},
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["packet_checklist_rows"], 20)
        self.assertEqual(report["human_review_rows"], 20)

    def test_standalone_invalid_packet_fails_closed(self) -> None:
        report = build_report(
            {"input_rows": 10, "selected_rows": 4, "held_rows": 6},
            {"input_rows": 4, "selected_rows": 4, "held_rows": 0},
            {"valid": False, "counts": {"checklist_rows": 4}, "issues": []},
        )

        self.assertFalse(report["valid"])
        self.assertIn("packet_has_structural_issues", report["issues"])

    def test_machine_certification_lane_reduces_human_packet(self) -> None:
        report = build_report(
            {"input_rows": 100, "selected_rows": 25, "held_rows": 75},
            {"input_rows": 25, "kept_rows": 20, "held_rows": 5},
            {"valid": True, "counts": {"checklist_rows": 17}, "issues": []},
            {"ready_records": 4},
            {
                "active_gold_modified": False,
                "counts": {
                    "input_rows": 20,
                    "auto_eligible_pending_calibration": 2,
                    "human_required": 17,
                    "reject_or_hold": 1,
                },
            },
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["human_review_rows"], 17)
        self.assertEqual(report["machine_certification_pending_calibration_rows"], 2)
        self.assertEqual(report["machine_certification_reject_or_hold_rows"], 1)
        self.assertEqual(report["machine_row_decisions_removed"], 83)
        self.assertEqual(report["human_work_reduction_rate"], 0.83)

    def test_machine_certification_lane_fails_closed_on_packet_drift(self) -> None:
        report = build_report(
            {"input_rows": 10, "selected_rows": 4, "held_rows": 6},
            {"input_rows": 4, "kept_rows": 4, "held_rows": 0},
            {"valid": True, "counts": {"checklist_rows": 1}, "issues": []},
            None,
            {
                "counts": {
                    "input_rows": 4,
                    "auto_eligible_pending_calibration": 1,
                    "human_required": 2,
                    "reject_or_hold": 1,
                }
            },
        )

        self.assertFalse(report["valid"])
        self.assertIn(
            "packet_rows_do_not_match_certification_human_output",
            report["issues"],
        )

    def test_supports_source_importer_report_schema(self) -> None:
        report = build_report(
            {"input_rows": 10, "selected_rows": 4, "held_rows": 6},
            {"input_rows": 4, "kept_rows": 4, "held_rows": 0},
            {"valid": True, "counts": {"checklist_rows": 4}, "issues": []},
            {"totals": {"sources": 5}, "active_gold_modified": False},
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["registered_source_docs"], 5)

    def test_supports_ocr_prefilter_candidate_rows_schema(self) -> None:
        report = build_report(
            {"candidate_rows": 71, "selected_rows": 24, "held_rows": 47},
            {
                "totals": {
                    "input_rows": 24,
                    "kept_rows": 24,
                    "held_rows": 0,
                    "corrected_rows": 4,
                }
            },
            {"valid": True, "counts": {"checklist_rows": 24}, "issues": []},
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["raw_candidate_rows"], 71)
        self.assertEqual(report["machine_row_decisions_removed"], 47)
        self.assertEqual(report["human_work_reduction_rate"], 0.661972)

    def test_supports_source_specific_filter_source_rows_schema(self) -> None:
        report = build_report(
            {"source_rows": 1694, "selected_rows": 159, "held_rows": 1535},
            {
                "totals": {
                    "input_rows": 159,
                    "kept_rows": 159,
                    "held_rows": 0,
                    "corrected_rows": 0,
                }
            },
            {"valid": True, "counts": {"checklist_rows": 159}, "issues": []},
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["raw_candidate_rows"], 1694)
        self.assertEqual(report["machine_row_decisions_removed"], 1535)
        self.assertEqual(report["human_work_reduction_rate"], 0.906139)


if __name__ == "__main__":
    unittest.main()
