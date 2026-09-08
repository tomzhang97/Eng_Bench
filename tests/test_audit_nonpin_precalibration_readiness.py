from __future__ import annotations

import unittest

from tools import audit_nonpin_precalibration_readiness as audit


class NonpinPrecalibrationReadinessTests(unittest.TestCase):
    def valid_row(self) -> dict:
        evidence = {
            "answer": "22uF",
            "bbox": [10, 20, 30, 40],
            "candidate_id": "candidate-a",
            "category": "component_value",
            "doc_id": "doc-a",
            "page_index": 0,
            "policy_version": "1.0",
            "reserved_split": "train",
            "version_id": "v1",
        }
        ocr = {
            "candidate_id": "candidate-a",
            "ocr_text": "22uF",
        }
        return {
            "task": "microtext",
            "candidate_id": "candidate-a",
            "record_id": "candidate-a",
            "category": "component_value",
            "doc_id": "doc-a",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [10, 20, 30, 40],
            "proposed_text": "22uF",
            "target_text": "22uF",
            "reserved_split": "train",
            "machine_certification_policy_version": "1.0",
            "machine_certification_tier": "auto_gold_train",
            "machine_certification_disposition": "auto_eligible_pending_calibration",
            "machine_certification_release_priority": "balance_closing_nonpin",
            "machine_certification_evidence": evidence,
            "machine_certification_evidence_sha256": audit.evidence_record_sha256(
                evidence
            ),
            "machine_certification_ocr_text": "22uF",
            "machine_certification_ocr_evidence": ocr,
            "machine_certification_ocr_evidence_sha256": audit.evidence_record_sha256(
                ocr
            ),
            "safe_to_merge_gold": False,
        }

    def test_valid_policy_and_canonical_evidence(self) -> None:
        row = self.valid_row()
        self.assertEqual(audit.policy_issues(row), [])
        self.assertEqual(audit.canonical_evidence_issues(row), [])

    def test_policy_infers_legacy_microtext_row_without_task(self) -> None:
        row = self.valid_row()
        row.pop("task")
        self.assertEqual(audit.policy_issues(row), [])

    def test_canonical_evidence_detects_text_and_hash_drift(self) -> None:
        row = self.valid_row()
        row["machine_certification_ocr_text"] = "2.2uF"
        row["machine_certification_evidence"]["answer"] = "47uF"
        reasons = audit.canonical_evidence_issues(row)
        self.assertIn("machine_certification_evidence_sha256_mismatch", reasons)
        self.assertIn("evidence_answer_mismatch", reasons)
        self.assertIn("ocr_text_mismatch", reasons)

    def test_canonical_evidence_uses_eligibility_nfkc_text_contract(self) -> None:
        row = self.valid_row()
        row["proposed_text"] = "10\u00b5F"
        row["target_text"] = "10\u00b5F"
        row["machine_certification_evidence"]["answer"] = "10\u03bcF"
        row["machine_certification_evidence_sha256"] = audit.evidence_record_sha256(
            row["machine_certification_evidence"]
        )
        row["machine_certification_ocr_text"] = "10\u03bcF"
        row["machine_certification_ocr_evidence"]["ocr_text"] = "10\u03bcF"
        row["machine_certification_ocr_evidence_sha256"] = audit.evidence_record_sha256(
            row["machine_certification_ocr_evidence"]
        )
        self.assertEqual(audit.canonical_evidence_issues(row), [])

    def test_near_region_partition_holds_active_and_candidate_overlap(self) -> None:
        active = [
            {
                "item_id": "active-a",
                "doc_id": "doc-a",
                "page_index": 0,
                "bbox": [10, 10, 50, 50],
            }
        ]
        candidates = [
            {
                "candidate_id": "near-active",
                "doc_id": "doc-a",
                "page_index": 0,
                "bbox": [11, 11, 49, 49],
            },
            {
                "candidate_id": "kept",
                "doc_id": "doc-a",
                "page_index": 0,
                "bbox": [100, 100, 140, 140],
            },
            {
                "candidate_id": "near-kept",
                "doc_id": "doc-a",
                "page_index": 0,
                "bbox": [101, 101, 139, 139],
            },
        ]
        kept, held = audit.partition_near_region_duplicates(active, candidates)
        self.assertEqual([row["candidate_id"] for row in kept], ["kept"])
        self.assertEqual(
            [row["candidate_id"] for row in held], ["near-active", "near-kept"]
        )
        self.assertTrue(
            all(
                row["precalibration_hold_reasons"] == ["near_duplicate_region"]
                for row in held
            )
        )

    def test_readiness_row_never_authorizes_promotion(self) -> None:
        row = audit.readiness_row(self.valid_row())
        self.assertFalse(row["safe_to_merge_gold"])
        self.assertTrue(row["calibration_required"])
        self.assertEqual(row["promotion_state"], "machine_calibration_pending")


if __name__ == "__main__":
    unittest.main()
