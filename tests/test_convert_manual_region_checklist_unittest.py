from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import convert_manual_region_checklist


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConvertManualRegionChecklistUnittest(unittest.TestCase):
    def build_fixture(self, root: Path) -> Path:
        source_dir = root / "sources"
        source_dir.mkdir(parents=True)
        good_source = source_dir / "good.pdf"
        good_source.write_bytes(b"release-safe-source")
        vendor_source = source_dir / "vendor.pdf"
        vendor_source.write_bytes(b"vendor-source")

        for doc_id, color in (
            ("active_doc", "white"),
            ("good_doc", "gray"),
            ("duplicate_doc", "white"),
            ("vendor_doc", "black"),
        ):
            page = root / "derived" / "pages_300dpi" / doc_id / "page_000.png"
            page.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 30), color).save(page)

        manifests = [
            {
                "type": "doc",
                "doc_id": "good_doc",
                "version": {"revision": "v1"},
                "path": "sources/good.pdf",
                "sha256": sha256(good_source),
                "source_candidate_id": "good",
                "source_url": "https://example.test/good",
                "public_status": "gpl_3_0_open_source_candidate",
                "license_note": "GPL source",
            },
            {
                "type": "doc",
                "doc_id": "duplicate_doc",
                "version": {"revision": "v1"},
                "path": "sources/good.pdf",
                "sha256": sha256(good_source),
                "source_candidate_id": "duplicate",
                "source_url": "https://example.test/duplicate",
                "public_status": "cc_by_sa_3_0_candidate",
                "license_note": "CC BY-SA source",
            },
            {
                "type": "doc",
                "doc_id": "vendor_doc",
                "version": {"revision": "v1"},
                "path": "sources/vendor.pdf",
                "sha256": sha256(vendor_source),
                "source_candidate_id": "vendor",
                "source_url": "https://example.test/vendor",
                "public_status": "public_vendor_docs_candidate",
                "license_note": "No redistribution license",
            },
        ]
        write_jsonl(root / "manifest.jsonl", manifests)
        write_jsonl(
            root / "microtext/annotations/microtext_items.jsonl",
            [{"item_id": "active", "doc_id": "active_doc", "page_index": 0}],
        )
        splits = root / "splits"
        splits.mkdir()
        (splits / "microtext_train.txt").write_text(
            "good_doc\nduplicate_doc\nvendor_doc\n", encoding="utf-8"
        )
        (splits / "microtext_dev.txt").write_text("", encoding="utf-8")
        (splits / "microtext_test.txt").write_text("", encoding="utf-8")

        packet = root / "packet"
        pages = packet / "pages"
        pages.mkdir(parents=True)
        for doc_id in ("good_doc", "duplicate_doc", "vendor_doc"):
            source = root / "derived/pages_300dpi" / doc_id / "page_000.png"
            (pages / f"{doc_id}.png").write_bytes(source.read_bytes())
        checklist = packet / "manual_region_mining_checklist.csv"
        fields = [
            "row_id",
            "doc_id",
            "domain",
            "page_index",
            "region_slot",
            "page_image",
            "source_path",
            "category",
            "transcribed_text",
            "bbox_xyxy",
            "status",
            "notes",
        ]
        with checklist.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                [
                    {"row_id": "good", "doc_id": "good_doc", "page_index": "0", "page_image": "pages/good_doc.png", "category": "dimension_value", "transcribed_text": "12.0", "bbox_xyxy": "1,2,20,15", "status": "accepted"},
                    {"row_id": "dup", "doc_id": "duplicate_doc", "page_index": "0", "page_image": "pages/duplicate_doc.png", "category": "equipment_tag", "transcribed_text": "P-1", "bbox_xyxy": "1,2,20,15", "status": "accepted"},
                    {"row_id": "vendor", "doc_id": "vendor_doc", "page_index": "0", "page_image": "pages/vendor_doc.png", "category": "pin_label", "transcribed_text": "J1", "bbox_xyxy": "1,2,20,15", "status": "accepted"},
                    {"row_id": "skip", "doc_id": "good_doc", "page_index": "0", "page_image": "pages/good_doc.png", "category": "dimension_value", "transcribed_text": "", "bbox_xyxy": "", "status": "skipped"},
                ]
            )
        return checklist

    def test_stages_only_release_safe_nonduplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checklist = self.build_fixture(root)
            staged, held, report = convert_manual_region_checklist.convert_checklist(root, checklist)

            self.assertEqual(["good"], [row["candidate_id"] for row in staged])
            self.assertEqual({"dup", "vendor"}, {row["row_id"] for row in held})
            reasons = {row["row_id"]: row["hold_reasons"] for row in held}
            self.assertTrue(any(reason.startswith("duplicate_page_active_gold") for reason in reasons["dup"]))
            self.assertIn("release_license_not_explicit", reasons["vendor"])
            self.assertEqual(1, report["staged_rows"])
            self.assertEqual(2, report["held_accepted_rows"])
            self.assertEqual(1, report["nonaccepted_human_rows"])

    def test_cli_writes_review_hold_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checklist = self.build_fixture(root)
            result = convert_manual_region_checklist.main(
                [
                    "--root",
                    str(root),
                    "--checklist",
                    str(checklist),
                    "--output-reviewed",
                    "out/reviewed.jsonl",
                    "--output-held",
                    "out/held.jsonl",
                    "--output-report-json",
                    "out/report.json",
                    "--output-report-md",
                    "out/report.md",
                ]
            )
            self.assertEqual(0, result)
            self.assertEqual(1, len(convert_manual_region_checklist.read_jsonl(root / "out/reviewed.jsonl")))
            self.assertEqual(2, len(convert_manual_region_checklist.read_jsonl(root / "out/held.jsonl")))
            self.assertTrue((root / "out/report.json").is_file())
            self.assertTrue((root / "out/report.md").is_file())

    def test_applies_auditable_bbox_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checklist = self.build_fixture(root)
            staged, held, report = convert_manual_region_checklist.convert_checklist(
                root,
                checklist,
                {
                    "good": {
                        "corrected_bbox": [3, 4, 21, 16],
                        "reason": "visual QA isolated the intended label",
                    }
                },
            )
            self.assertEqual([3, 4, 21, 16], staged[0]["bbox"])
            self.assertEqual("1,2,20,15", staged[0]["human_return_bbox_xyxy"])
            self.assertIn("visual QA", staged[0]["machine_correction_reason"])
            self.assertEqual(1, report["counters"]["machine_corrected_bbox"])
            self.assertEqual(2, len(held))


if __name__ == "__main__":
    unittest.main()
