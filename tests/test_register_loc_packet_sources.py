import csv
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.register_loc_packet_sources import apply_registration, build_registration, render_attribution


class RegisterLocPacketSourcesTest(unittest.TestCase):
    def test_attribution_preserves_existing_sections(self) -> None:
        existing = "# LOC HABS/HAER/HALS Source Attribution\n\n## old_doc\n\n- Existing\n"
        records = [
            {
                "doc_id": "new_doc",
                "source_path": "sources/new_doc.tif",
                "sha256": "abc",
                "audit": {
                    "loc_item_id": "test.item",
                    "title": "New plan",
                    "call_number": "HABS TEST",
                    "item_url": "https://example.test/item",
                    "resolved_master_url": "https://example.test/master.tif",
                    "rights_information": "No known restrictions",
                },
            }
        ]
        rendered = render_attribution(records, existing)
        self.assertIn("## old_doc", rendered)
        self.assertIn("## new_doc", rendered)

    def test_accepts_explicit_doc_ids_without_a_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            fields = [
                "doc_id", "domain", "task", "public_status", "source_path",
                "rendered_pages", "next_step", "source_url",
            ]
            with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
            (root / "splits").mkdir()
            for split in ("train", "dev", "test"):
                (root / "splits" / f"microtext_{split}.txt").write_text("", encoding="utf-8")

            audit = root / "audit.csv"
            with audit.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "candidate_id", "proposed_doc_id", "domain", "loc_item_id", "item_url",
                        "resolved_master_url", "title", "call_number", "rights_information",
                        "unrestricted", "metadata_modified", "audit_status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "arch_019",
                        "proposed_doc_id": "explicit_doc",
                        "loc_item_id": "fl0001.sheet.00001a",
                        "item_url": "https://www.loc.gov/pictures/item/fl0001.sheet.00001a/",
                        "resolved_master_url": "https://cdn.loc.gov/master/test.tif",
                        "title": "Site plan",
                        "call_number": "HABS TEST",
                        "rights_information": "No known restrictions",
                        "unrestricted": "True",
                        "metadata_modified": "2026-01-01",
                        "audit_status": "ready_for_intake",
                    }
                )
            source_dir = root / "sources"
            source_dir.mkdir()
            Image.new("L", (20, 20), "white").save(source_dir / "explicit_doc.tif")
            page_dir = root / "derived" / "pages_300dpi" / "explicit_doc"
            page_dir.mkdir(parents=True)
            Image.new("L", (20, 20), "white").save(page_dir / "page_000.png")

            report = build_registration(
                root=root,
                packet_dirs=[],
                doc_ids=["explicit_doc"],
                audit_csvs=[audit],
                source_dirs=[source_dir],
                split="test",
                date_label="test",
            )
            self.assertEqual([], report["issues"])
            self.assertEqual(1, report["new_manifest_docs"])
            self.assertEqual("explicit_doc", report["records"][0]["doc_id"])

    def test_registers_only_audited_packet_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            fields = [
                "doc_id", "domain", "task", "public_status", "source_path",
                "rendered_pages", "textlayer_spans", "mineable_candidates", "review_rows",
                "open_review_rows", "packeted_open_review_rows", "unpacketed_open_review_rows",
                "fresh_open_review_rows", "rights_blocked_open_review_rows", "stale_open_review_rows",
                "mergeable_review_rows", "missing_review_evidence_rows", "gold_rows",
                "gold_microtext_rows", "gold_visualdiff_rows", "next_step", "priority_score", "source_url",
            ]
            with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
            (root / "splits").mkdir()
            for split in ("train", "dev", "test"):
                (root / "splits" / f"microtext_{split}.txt").write_text("", encoding="utf-8")

            packet = root / "packet" / "review_packs" / "loc"
            packet.mkdir(parents=True)
            (packet / "manifest.jsonl").write_text(
                json.dumps({"candidate_id": "c1", "doc_id": "loc_doc"}) + "\n",
                encoding="utf-8",
            )
            audit = root / "audit.csv"
            with audit.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "candidate_id", "proposed_doc_id", "domain", "loc_item_id", "item_url",
                        "resolved_master_url", "title", "call_number", "rights_information",
                        "unrestricted", "metadata_modified", "audit_status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "arch_012",
                        "proposed_doc_id": "loc_doc",
                        "domain": "mechanical",
                        "loc_item_id": "xx0001.sheet.00001a",
                        "item_url": "https://www.loc.gov/pictures/item/xx0001.sheet.00001a/",
                        "resolved_master_url": "https://cdn.loc.gov/master/test.tif",
                        "title": "Plan",
                        "call_number": "HABS TEST",
                        "rights_information": "No known restrictions",
                        "unrestricted": "True",
                        "metadata_modified": "2026-01-01",
                        "audit_status": "ready_for_intake",
                    }
                )
            source_dir = root / "sources"
            source_dir.mkdir()
            Image.new("L", (20, 20), "white").save(source_dir / "loc_doc.tif")
            page_dir = root / "derived" / "pages_300dpi" / "loc_doc"
            page_dir.mkdir(parents=True)
            Image.new("L", (20, 20), "white").save(page_dir / "page_000.png")

            report = build_registration(
                root=root,
                packet_dirs=[packet.parent.parent],
                audit_csvs=[audit],
                source_dirs=[source_dir],
                split="test",
                date_label="test",
            )
            self.assertEqual([], report["issues"])
            self.assertEqual(1, report["new_manifest_docs"])
            apply_registration(root, report)

            manifest = [json.loads(line) for line in (root / "manifest.jsonl").read_text().splitlines()]
            self.assertEqual("public_domain_us_federal_candidate", manifest[0]["public_status"])
            self.assertEqual("arch_012", manifest[0]["source_candidate_id"])
            self.assertEqual("mechanical", manifest[0]["domain"])
            with (root / "SOURCE_INVENTORY.csv").open(encoding="utf-8") as handle:
                inventory = list(csv.DictReader(handle))
            self.assertEqual("await_human_return", inventory[0]["next_step"])
            self.assertEqual("mechanical", inventory[0]["domain"])
            self.assertEqual("loc_doc\n", (root / "splits/microtext_test.txt").read_text())
            self.assertTrue((root / "microtext/docs/LOC_HABS_HAER_ATTRIBUTION.md").exists())


if __name__ == "__main__":
    unittest.main()
