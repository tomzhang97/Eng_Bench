import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_active_rights_resolution_coverage import audit_coverage


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ActiveRightsResolutionCoverageTests(unittest.TestCase):
    def make_root(self, *, include_decision: bool = True) -> tuple[Path, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        source = root / "microtext" / "docs" / "blocked.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"blocked source")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        write_jsonl(
            root / "manifest.jsonl",
            [{
                "type": "doc",
                "doc_id": "blocked_doc",
                "task": "microtext",
                "path": "microtext/docs/blocked.pdf",
                "source_url": "https://example.test/source.pdf",
                "public_status": "public_candidate",
                "sha256": digest,
            }],
        )
        with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["doc_id", "task", "path", "source_url", "public_status"],
            )
            writer.writeheader()
            writer.writerow({
                "doc_id": "blocked_doc",
                "task": "microtext",
                "path": "microtext/docs/blocked.pdf",
                "source_url": "https://example.test/source.pdf",
                "public_status": "public_candidate",
            })
        write_jsonl(
            root / "microtext" / "annotations" / "microtext_items.jsonl",
            [{"item_id": "m1", "doc_id": "blocked_doc"}],
        )
        write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
        evidence = root / "evidence.md"
        evidence.write_text("reviewed terms", encoding="utf-8")
        fields = [
            "doc_id", "decision", "reason", "terms_url", "evidence_path",
            "expected_sha256", "target_public_status", "license_note", "reviewed_date",
        ]
        with (root / "rights.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            if include_decision:
                writer.writerow({
                    "doc_id": "blocked_doc",
                    "decision": "hold",
                    "reason": "No redistribution permission.",
                    "terms_url": "https://example.test/terms",
                    "evidence_path": "evidence.md",
                    "expected_sha256": digest,
                    "reviewed_date": "2026-09-09",
                })
        return root, temp

    def test_complete_hash_bound_hold_coverage_passes(self) -> None:
        root, temp = self.make_root()
        self.addCleanup(temp.cleanup)
        report = audit_coverage(root, root / "rights.csv", "test")
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["coverage_complete"])
        self.assertFalse(report["provenance_release_ready"])
        self.assertEqual(report["totals"]["covered_blocked_docs"], 1)
        self.assertEqual(report["totals"]["active_row_references_covered"], 1)

    def test_missing_blocked_decision_fails(self) -> None:
        root, temp = self.make_root(include_decision=False)
        self.addCleanup(temp.cleanup)
        report = audit_coverage(root, root / "rights.csv", "test")
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["missing_doc_ids"], ["blocked_doc"])
        self.assertIn("missing blocked-source decision: blocked_doc", report["issues"])


if __name__ == "__main__":
    unittest.main()
