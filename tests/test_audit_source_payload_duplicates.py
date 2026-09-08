import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_source_payload_duplicates import build_report
from audit_v2_0_gate import unique_active_source_status, unique_release_safe_inventory_status


class SourcePayloadDuplicateAuditTest(unittest.TestCase):
    def test_duplicate_hashes_across_doc_ids_create_one_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"same engineering source"
            sha256 = hashlib.sha256(payload).hexdigest()
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(payload)
            second.write_bytes(payload)
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "doc_a",
                        "path": "first.bin",
                        "sha256": sha256,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            receipts = root / "derived" / "quality"
            receipts.mkdir(parents=True)
            (receipts / "source_asset_download_receipts_test.json").write_text(
                json.dumps(
                    {
                        "receipts": [
                            {
                                "doc_id": "doc_b",
                                "local_path": "second.bin",
                                "sha256": sha256,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(root)

            self.assertEqual(report["totals"]["duplicate_groups"], 1)
            self.assertEqual(report["alias_doc_ids"], ["doc_b"])
            self.assertEqual(report["totals"]["local_hash_mismatches"], 0)

    def test_gate_counts_unique_release_safe_documents(self) -> None:
        inventory = [
            {"doc_id": "doc_a", "task": "microtext", "public_status": "cc_by_4_0"},
            {"doc_id": "doc_b", "task": "microtext", "public_status": "cc_by_4_0"},
            {"doc_id": "doc_c", "task": "microtext", "public_status": "public_domain"},
        ]
        status = unique_release_safe_inventory_status(inventory, {"alias_doc_ids": ["doc_b"]})

        self.assertEqual(status["raw_current"], 3)
        self.assertEqual(status["current"], 2)
        self.assertEqual(status["duplicate_alias_docs"], 1)

    def test_gate_counts_unique_active_source_payloads(self) -> None:
        first_sha256 = hashlib.sha256(b"same active source").hexdigest()
        second_sha256 = hashlib.sha256(b"different active source").hexdigest()
        provenance = {
            "documents": [
                {"doc_id": "doc_a", "computed_sha256": first_sha256, "paper_ready": True},
                {"doc_id": "doc_b", "computed_sha256": first_sha256, "paper_ready": True},
                {"doc_id": "doc_c", "computed_sha256": second_sha256, "paper_ready": True},
                {"doc_id": "held_doc", "computed_sha256": "f" * 64, "paper_ready": False},
            ]
        }

        status = unique_active_source_status(provenance)

        self.assertEqual(status["raw_current"], 3)
        self.assertEqual(status["raw_active_docs"], 4)
        self.assertEqual(status["excluded_not_paper_ready_docs"], 1)
        self.assertEqual(status["excluded_not_paper_ready_doc_ids"], ["held_doc"])
        self.assertEqual(status["current"], 2)
        self.assertEqual(status["duplicate_alias_docs"], 1)
        self.assertEqual(status["duplicate_alias_doc_ids"], ["doc_b"])
        self.assertEqual(status["duplicate_payload_groups"][0]["canonical_doc_id"], "doc_a")


if __name__ == "__main__":
    unittest.main()
