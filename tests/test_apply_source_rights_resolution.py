from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import apply_source_rights_resolution


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class SourceRightsResolutionTests(unittest.TestCase):
    def make_fixture(self, root: Path, target_status: str = "public_domain_official_terms") -> Path:
        source = root / "microtext" / "docs" / "sheet.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"official-source")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        (root / "manifest.jsonl").write_text(
            json.dumps(
                {
                    "type": "doc",
                    "doc_id": "sheet",
                    "path": "microtext/docs/sheet.pdf",
                    "sha256": digest,
                    "public_status": "public_vendor_candidate",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        write_csv(
            root / "SOURCE_INVENTORY.csv",
            [{"doc_id": "sheet", "public_status": "public_vendor_candidate", "notes": ""}],
        )
        evidence = root / "RIGHTS_EVIDENCE.md"
        evidence.write_text("Official terms evidence.\n", encoding="utf-8")
        evidence_csv = root / "rights.csv"
        write_csv(
            evidence_csv,
            [
                {
                    "doc_id": "sheet",
                    "decision": "release_safe",
                    "target_public_status": target_status,
                    "terms_url": "https://example.test/terms",
                    "evidence_path": "RIGHTS_EVIDENCE.md",
                    "expected_sha256": digest,
                    "reviewed_date": "2026-08-09",
                    "license_note": "Official terms permit redistribution.",
                    "reason": "Official terms and exact hash verified.",
                }
            ],
        )
        return evidence_csv

    def test_applies_release_safe_resolution_with_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence_csv = self.make_fixture(root)
            snapshot = root / "snapshot"

            report = apply_source_rights_resolution.apply_resolution(
                root,
                evidence_csv,
                snapshot_dir=snapshot,
                apply=True,
            )

            self.assertTrue(report["applied"])
            self.assertEqual(report["validated_release_updates"], 1)
            self.assertTrue((snapshot / "manifest.jsonl").is_file())
            manifest = json.loads((root / "manifest.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(manifest["public_status"], "public_domain_official_terms")
            self.assertEqual(manifest["rights_evidence_path"], "RIGHTS_EVIDENCE.md")
            with (root / "SOURCE_INVENTORY.csv").open(encoding="utf-8", newline="") as stream:
                inventory = list(csv.DictReader(stream))
            self.assertEqual(inventory[0]["public_status"], "public_domain_official_terms")

    def test_rejects_status_without_affirmative_release_basis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence_csv = self.make_fixture(root, target_status="public_vendor_candidate")

            report = apply_source_rights_resolution.apply_resolution(root, evidence_csv)

            self.assertFalse(report["applied"])
            self.assertTrue(any("not release-safe" in issue for issue in report["issues"]))

    def test_hold_decision_requires_no_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence_csv = self.make_fixture(root)
            write_csv(
                evidence_csv,
                [
                    {
                        "doc_id": "sheet",
                        "decision": "hold",
                        "target_public_status": "",
                        "terms_url": "https://example.test/terms",
                        "evidence_path": "RIGHTS_EVIDENCE.md",
                        "expected_sha256": "",
                        "reviewed_date": "2026-08-09",
                        "license_note": "",
                        "reason": "No redistribution grant.",
                    }
                ],
            )

            report = apply_source_rights_resolution.apply_resolution(root, evidence_csv)

            self.assertEqual(report["hold_decisions"], 1)
            self.assertEqual(report["issues"], [])
            self.assertFalse(report["applied"])

    def test_hold_decision_validates_a_supplied_payload_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence_csv = self.make_fixture(root)
            write_csv(
                evidence_csv,
                [
                    {
                        "doc_id": "sheet",
                        "decision": "hold",
                        "target_public_status": "",
                        "terms_url": "https://example.test/terms",
                        "evidence_path": "RIGHTS_EVIDENCE.md",
                        "expected_sha256": "0" * 64,
                        "reviewed_date": "2026-08-09",
                        "license_note": "",
                        "reason": "No redistribution grant.",
                    }
                ],
            )

            report = apply_source_rights_resolution.apply_resolution(root, evidence_csv)

            self.assertTrue(any("SHA-256 does not match" in issue for issue in report["issues"]))

    def test_hold_decision_validates_supplied_evidence_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence_csv = self.make_fixture(root)
            write_csv(
                evidence_csv,
                [
                    {
                        "doc_id": "sheet",
                        "decision": "hold",
                        "target_public_status": "",
                        "terms_url": "https://example.test/terms",
                        "evidence_path": "missing-evidence.md",
                        "expected_sha256": "",
                        "reviewed_date": "2026-08-30",
                        "license_note": "",
                        "reason": "No redistribution grant.",
                    }
                ],
            )

            report = apply_source_rights_resolution.apply_resolution(root, evidence_csv)

            self.assertTrue(any("evidence_path not found" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
