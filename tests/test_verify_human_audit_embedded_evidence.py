import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import verify_human_audit_embedded_evidence as verifier


class VerifyHumanAuditEmbeddedEvidenceTests(unittest.TestCase):
    def test_resolve_relative_package_part(self) -> None:
        self.assertEqual(
            verifier.resolve_part(
                "xl/drawings/drawing1.xml", "../media/image1.png"
            ),
            "xl/media/image1.png",
        )

    def test_relationship_part(self) -> None:
        self.assertEqual(
            verifier.relationship_part("xl/worksheets/sheet1.xml"),
            "xl/worksheets/_rels/sheet1.xml.rels",
        )

    def test_rejects_unsafe_package_part(self) -> None:
        with self.assertRaises(ValueError):
            verifier.resolve_part("xl/workbook.xml", "../../../outside.xml")

    def test_skips_explicitly_unissued_primary_workbook(self) -> None:
        payload = {
            "primary": {
                "issued": False,
                "workbook": "PRIMARY_NOT_ISSUED.xlsx",
                "rows": [],
            },
            "auditors": [
                {
                    "workbook": "AUDITOR_01_REVIEW_12.xlsx",
                    "rows": [{"evidence_path": "evidence.png"}],
                }
            ],
        }
        auditor_report = {
            "workbook": "AUDITOR_01_REVIEW_12.xlsx",
            "sheet": "\u5ba1\u683812\u6761",
            "expected_rows": 1,
            "picture_rows": 1,
            "exact_image_matches": 1,
            "unique_embedded_images": 1,
            "xlsx_sha256": "abc",
            "issues": [],
            "valid": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            payload_path = temp_path / "payload.json"
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(
                verifier,
                "verify_workbook",
                return_value=(auditor_report, []),
            ) as verify_workbook:
                report = verifier.verify_delivery(temp_path, payload_path)

        self.assertTrue(report["valid"])
        self.assertFalse(report["primary_issued"])
        self.assertEqual(report["workbooks"], 1)
        self.assertEqual(report["assigned_image_rows"], 1)
        self.assertEqual(verify_workbook.call_count, 1)
        self.assertEqual(
            verify_workbook.call_args.args[0].name,
            "AUDITOR_01_REVIEW_12.xlsx",
        )


if __name__ == "__main__":
    unittest.main()
