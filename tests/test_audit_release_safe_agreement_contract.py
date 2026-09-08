from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.audit_release_safe_agreement_contract import audit_contract
from tools.build_provenance_replacement_plan import candidate_evidence_fingerprint
from tools.build_release_safe_agreement_contract import file_sha256


class AgreementContractAuditTests(unittest.TestCase):
    def test_complete_machine_contract_passes_without_claiming_human_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "page.png"
            evidence_path = root / "evidence.png"
            source_path = root / "source.bin"
            Image.new("RGB", (100, 100), "white").save(image_path)
            Image.new("RGB", (20, 20), "white").save(evidence_path)
            source_path.write_bytes(b"source")
            base = {
                "candidate_id": "candidate-a",
                "record_id": "candidate-a",
                "task": "microtext",
                "reserved_split": "dev",
                "image_path": "page.png",
                "bbox": [10, 10, 30, 30],
                "agreement_channel": "completed_independent_screen",
                "agreement_source_rights_check": "release_safe_status",
                "agreement_source_documents": ["doc-a"],
                "agreement_source_payloads": {
                    "doc-a": {
                        "path": "source.bin",
                        "recorded_sha256": hashlib.sha256(b"source").hexdigest(),
                    }
                },
                "primary_evidence_path": str(evidence_path),
                "primary_evidence_sha256": file_sha256(evidence_path),
                "agreement_auditor_reviewer_id": "auditor-1",
                "agreement_auditor_decision_code": "1",
                "agreement_machine_contract_ready": True,
                "agreement_human_complete": False,
                "safe_to_merge_gold": False,
            }
            fingerprint, status = candidate_evidence_fingerprint(root, base)
            self.assertEqual("pixel_crop_sha256", status)
            base["agreement_evidence_fingerprint"] = fingerprint

            report = audit_contract(
                root=root,
                rows=[base],
                new_auditor_rows=[],
                gold_ids=set(),
                expected_rows=1,
                task_targets={"microtext": 1},
            )

            self.assertEqual("PASS", report["status"])
            self.assertFalse(report["human_review_complete"])
            self.assertFalse(report["safe_to_merge_gold"])

    def test_duplicate_identity_fails(self) -> None:
        report = audit_contract(
            root=Path("."),
            rows=[{"record_id": "same"}, {"record_id": "same"}],
            new_auditor_rows=[],
            gold_ids=set(),
            expected_rows=2,
            task_targets={"microtext": 2},
        )
        self.assertEqual("FAIL", report["status"])
        self.assertIn("duplicate_contract_identity", report["issues"])


if __name__ == "__main__":
    unittest.main()
