import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import preview_reviewed_gold_promotion as preview
from tools.candidate_evidence_holds import CURRENT_HOLDS, digest, evidence_hold_ids
from tools.certify_microtext_bbox_repairs import activate_resolution, certify
from tools.ingest_auditor_return_batch import parser as audit_parser


class CertifyMicrotextBboxRepairsTests(unittest.TestCase):
    def fixture(self, root: Path):
        for name in audit_parser.ACTIVE_GOLD_PATHS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        page = root / "page.png"
        Image.new("RGB", (100, 100), "white").save(page)
        staging = root / "derived/quality/staging"
        staging.mkdir(parents=True)
        proposal = {
            "candidate_id": "c",
            "image_path": "page.png",
            "bbox": [5, 5, 20, 20],
            "proposed_text": "10 ft",
            "category": "dimension_value",
            "review_status": "needs_machine_bbox_reconciliation",
            "human_review_status": "needs_machine_bbox_reconciliation",
            "prior_human_review": {
                "review_status": "accepted",
                "human_review_status": "accepted",
            },
            "machine_bbox_repair": {"original_bbox": [6, 6, 19, 19]},
            "safe_to_merge_gold": False,
        }
        proposals = staging / "bbox_repair_proposals.jsonl"
        preview.write_jsonl(proposals, [proposal])
        preview.write_json(staging / "report.json", {
            "status": "PENDING_MACHINE_BBOX_RECONCILIATION",
            "active_gold_modified": False,
            "safe_to_merge_gold": False,
            "source_image_hashes": {"page.png": digest(page)},
            "output_hashes": {proposals.name: digest(proposals)},
        })
        hold_dir = root / "derived/quality/hold"
        hold_dir.mkdir(parents=True)
        hold_rows = hold_dir / "candidate_evidence_holds.jsonl"
        preview.write_jsonl(hold_rows, [{
            "candidate_id": "c",
            "hold_reason": "bbox",
            "safe_to_merge_gold": False,
        }])
        hold_report = hold_dir / "report.json"
        preview.write_json(hold_report, {
            "active_gold_modified": False,
            "safe_to_merge_gold": False,
            "hold_rows_artifact": hold_rows.name,
            "hold_count": 1,
            "output_hashes": {hold_rows.name: digest(hold_rows)},
        })
        (root / CURRENT_HOLDS).write_text(json.dumps({
            "schema_version": 2,
            "reports": [{
                "report_path": hold_report.relative_to(root).as_posix(),
                "report_sha256": digest(hold_report),
            }],
            "resolutions": [],
        }))
        contact = root / "contact.png"
        Image.new("RGB", (100, 100), "white").save(contact)
        verification = root / "verification.json"
        preview.write_json(verification, {
            "contact_sheet_sha256": digest(contact),
            "records": [{
                "candidate_id": "c",
                "bbox": proposal["bbox"],
                "status": "pass",
                "contact_sheet": contact.relative_to(root).as_posix(),
                "verification_note": "complete isolated target",
                "resolves_reports": [hold_report.relative_to(root).as_posix()],
            }],
        })
        return staging, verification, contact

    def test_certifies_geometry_and_resolves_only_hash_bound_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging, verification, contact = self.fixture(root)
            output = root / "derived/quality/certified"
            report = certify(root, staging, verification, contact, output)
            repaired = preview.read_jsonl(output / "bbox_repaired_reviewed.jsonl")[0]
            self.assertEqual("accepted", repaired["review_status"])
            self.assertEqual("pass", repaired["machine_bbox_repair"]["verification"]["status"])
            self.assertFalse(report["human_text_or_category_decisions_modified"])
            self.assertEqual({"c"}, evidence_hold_ids(root))
            before, after = activate_resolution(root, output / "report.json")
            self.assertEqual({"c"}, before)
            self.assertEqual(set(), after)

    def test_stale_bbox_verification_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging, verification, contact = self.fixture(root)
            config = json.loads(verification.read_text())
            config["records"][0]["bbox"] = [1, 1, 2, 2]
            preview.write_json(verification, config)
            with self.assertRaisesRegex(ValueError, "missing or stale"):
                certify(root, staging, verification, contact, root / "derived/quality/certified")


if __name__ == "__main__":
    unittest.main()
