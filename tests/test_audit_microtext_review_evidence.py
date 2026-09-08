import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools import audit_microtext_review_evidence as audit


class AuditMicrotextReviewEvidenceTest(unittest.TestCase):
    def test_materializes_authoritative_page_bbox_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            page = Image.new("RGB", (100, 80), "white")
            draw = ImageDraw.Draw(page)
            draw.line((34, 29, 56, 29), fill="black", width=3)
            draw.line((34, 29, 34, 42), fill="black", width=3)
            page.save(root / "page.png")
            row = {
                "candidate_id": "candidate",
                "doc_id": "doc",
                "category": "equipment_tag",
                "proposed_text": "P-101",
                "image_path": "page.png",
                "bbox": [30, 25, 61, 46],
            }

            passing, held, report = audit.audit_rows(
                root, [row], root / "crops", pad_x=4, pad_y=3
            )

            self.assertEqual(held, [])
            self.assertEqual(len(passing), 1)
            self.assertEqual(
                passing[0]["evidence_audit_status"],
                "pixel_informative_needs_visual_review",
            )
            self.assertEqual(passing[0]["evidence_audit_crop_bbox"], [26, 22, 65, 49])
            self.assertTrue((root / passing[0]["crop_path"]).is_file())
            self.assertNotIn("crop_path", row)
            self.assertEqual(report["passing_rows"], 1)

    def test_blank_authoritative_bbox_holds_even_when_stale_crop_has_ink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            Image.new("RGB", (100, 80), "white").save(root / "page.png")
            stale = Image.new("RGB", (20, 20), "white")
            ImageDraw.Draw(stale).line((0, 10, 19, 10), fill="black", width=2)
            stale.save(root / "stale.png")
            row = {
                "candidate_id": "candidate",
                "doc_id": "doc",
                "category": "pin_label",
                "proposed_text": "GND",
                "image_path": "page.png",
                "crop_path": "stale.png",
                "bbox": [30, 25, 61, 46],
            }

            passing, held, report = audit.audit_rows(root, [row], root / "crops")

            self.assertEqual(passing, [])
            self.assertEqual(len(held), 1)
            self.assertEqual(
                held[0]["machine_hold_reason"], "blank_or_low_information_page_bbox"
            )
            self.assertEqual(report["held_rows"], 1)

    def test_invalid_bbox_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            Image.new("RGB", (20, 20), "white").save(root / "page.png")
            row = {
                "candidate_id": "candidate",
                "doc_id": "doc",
                "category": "pin_label",
                "proposed_text": "GND",
                "image_path": "page.png",
                "bbox": [10, 10, 5, 5],
            }

            passing, held, _report = audit.audit_rows(root, [row], root / "crops")

            self.assertEqual(passing, [])
            self.assertEqual(held[0]["machine_hold_reason"], "invalid_bbox")

    def test_cli_writes_auditable_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            page = Image.new("RGB", (50, 50), "white")
            draw = ImageDraw.Draw(page)
            draw.line((14, 14, 26, 14), fill="black", width=2)
            draw.line((14, 14, 14, 26), fill="black", width=2)
            page.save(root / "page.png")
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "candidate",
                        "doc_id": "doc",
                        "category": "pin_label",
                        "proposed_text": "GND",
                        "image_path": "page.png",
                        "bbox": [10, 10, 31, 31],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            code = audit.main(
                [
                    "--root",
                    str(root),
                    "--input",
                    "input.jsonl",
                    "--passing-jsonl",
                    "passing.jsonl",
                    "--held-jsonl",
                    "held.jsonl",
                    "--report-json",
                    "report.json",
                    "--crop-dir",
                    "crops",
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue((root / "passing.jsonl").is_file())
            self.assertEqual((root / "held.jsonl").read_text(encoding="utf-8"), "")
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["passing_rows"], 1)
            self.assertEqual(len(report["passing_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
