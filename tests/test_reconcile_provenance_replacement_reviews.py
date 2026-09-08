from __future__ import annotations

import unittest

from tools import reconcile_provenance_replacement_reviews as reconciler


class ReconcileProvenanceReplacementReviewsTest(unittest.TestCase):
    def test_preserves_human_edit_and_rebinds_current_contract_metadata(self) -> None:
        candidate = {
            "candidate_id": "mt-1",
            "task": "microtext",
            "doc_id": "current-doc",
            "version_id": "v2",
            "page_index": 2,
            "image_path": "current/page.png",
            "bbox": [1, 2, 3, 4],
            "source_candidate_id": "source-current",
            "reserved_split": "dev",
            "split_reservation_plan": "current-plan.json",
            "split_reservation_id": "reservation-current",
            "provenance_replacement_candidate": True,
            "provenance_replacement_date_label": "current-contract",
            "replacement_for_task": "microtext",
            "replacement_for_split": "dev",
            "replacement_for_category": "dimension_value",
            "replacement_match_level": "exact_category",
            "replacement_origin_phase": "current_assignment",
            "replacement_rights_check": "release_safe_status",
            "replacement_source_unit": "current-doc",
            "replacement_evidence_fingerprint": "microtext:sha256:abc",
            "replacement_evidence_fingerprint_status": "pixel_crop_sha256",
            "review_status": "needs_review",
            "safe_to_merge_gold": False,
        }
        reviewed = {
            **candidate,
            "doc_id": "stale-doc",
            "provenance_replacement_date_label": "old-contract",
            "human_review_status": "edit",
            "review_status": "edit",
            "corrected_text": "10 mm",
            "reviewer_notes": "Corrected spacing.",
        }

        rows, report = reconciler.reconcile([candidate], [reviewed])

        self.assertTrue(report["valid"], report["issues"])
        self.assertEqual(report["promotable_review_rows"], 1)
        self.assertEqual(report["outstanding_review_rows"], 0)
        self.assertEqual(rows[0]["doc_id"], "current-doc")
        self.assertEqual(rows[0]["provenance_replacement_date_label"], "current-contract")
        self.assertEqual(rows[0]["review_status"], "edited")
        self.assertEqual(rows[0]["human_review_status"], "edited")
        self.assertEqual(rows[0]["corrected_text"], "10 mm")
        self.assertEqual(rows[0]["reviewer_notes"], "Corrected spacing.")
        self.assertFalse(rows[0]["safe_to_merge_gold"])

    def test_drops_old_contract_rows_and_reports_current_outstanding(self) -> None:
        candidates = [
            {"candidate_id": "current-a", "replacement_for_task": "microtext"},
            {"pair_id": "current-b", "replacement_for_task": "visualdiff"},
        ]
        reviewed = [
            {"candidate_id": "current-a", "human_review_status": "accepted"},
            {"candidate_id": "retired", "human_review_status": "accepted"},
        ]

        rows, report = reconciler.reconcile(candidates, reviewed)

        self.assertTrue(report["valid"], report["issues"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["source_review_rows_outside_current_contract"], 1)
        self.assertEqual(report["promotable_review_rows"], 1)
        self.assertEqual(report["outstanding_review_rows"], 1)
        self.assertFalse(report["ready_for_atomic_migration"])

    def test_rejected_current_row_remains_visible_and_blocks_readiness(self) -> None:
        candidates = [
            {"pair_id": "current", "replacement_for_task": "visualdiff"},
        ]
        reviewed = [
            {"pair_id": "current", "human_review_status": "rejected"},
        ]

        rows, report = reconciler.reconcile(candidates, reviewed)

        self.assertEqual(len(rows), 1)
        self.assertEqual(report["rejected_review_rows"], 1)
        self.assertEqual(report["outstanding_review_rows"], 1)
        self.assertFalse(report["ready_for_atomic_migration"])

        outstanding = reconciler.build_outstanding_rows(candidates, rows)
        self.assertEqual(len(outstanding), 1)
        self.assertEqual(
            outstanding[0]["replacement_completion_status"],
            "rejected_candidate_requires_replacement",
        )

    def test_outstanding_queue_excludes_promotable_reviews(self) -> None:
        candidates = [
            {"candidate_id": "accepted", "replacement_for_task": "microtext"},
            {"candidate_id": "pending", "replacement_for_task": "microtext"},
        ]
        reviewed = [
            {
                "candidate_id": "accepted",
                "replacement_for_task": "microtext",
                "human_review_status": "accepted",
            }
        ]
        reconciled, _ = reconciler.reconcile(candidates, reviewed)

        outstanding = reconciler.build_outstanding_rows(candidates, reconciled)

        self.assertEqual([row["candidate_id"] for row in outstanding], ["pending"])
        self.assertEqual(
            outstanding[0]["replacement_completion_status"], "human_review_required"
        )
        self.assertFalse(outstanding[0]["safe_to_merge_gold"])


if __name__ == "__main__":
    unittest.main()
