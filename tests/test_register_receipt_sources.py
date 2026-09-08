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

from register_receipt_sources import apply_registration, build_registration, write_manifest_preview


class RegisterReceiptSourcesTest(unittest.TestCase):
    def test_registers_only_hash_and_render_verified_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"receipt-backed-pdf"
            source = root / "docs/source.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            page_dir = root / "derived/pages/doc"
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"png")
            download = root / "download.json"
            render = root / "render.json"
            download.write_text(
                json.dumps(
                    {
                        "receipts": [
                            {
                                "doc_id": "doc",
                                "candidate_id": "civil_1",
                                "domain": "civil",
                                "asset_kind": "pdf",
                                "doc_type": "federal_engineering_handbook_pdf",
                                "same_model_id": "handbook_series",
                                "status": "downloaded",
                                "local_path": "docs/source.pdf",
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "page_url": "https://example.test/index",
                                "official_direct_url": "https://example.test/official.pdf",
                                "retrieved_from_url": "https://mirror.test/source.pdf",
                                "full_page_count": 120,
                                "version": {"revision": "1993"},
                                "public_status": "public_domain_us_federal_candidate",
                                "rights_capture": "public_agency_source_url_terms_sha256",
                                "license_note": "Approved for public release; distribution is unlimited.",
                                "rights_evidence_url": "https://example.test/rights",
                                "rights_evidence_path": "docs/RIGHTS.md",
                                "notes": "Selected engineering-heavy pages only; no gold promotion.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            render.write_text(
                json.dumps(
                    {
                        "render_receipts": [
                            {
                                "doc_id": "doc",
                                "status": "rendered_pdf_textlayer",
                                "pages_dir": "derived/pages/doc",
                                "rendered_pages": 1,
                                "dpi": 300,
                                "colorspace": "gray",
                                "page_selection": "28-34",
                                "textlayer_dir": "derived/text/doc",
                                "textlayer_jsonl": "derived/text/doc.jsonl",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "doc_id",
                        "domain",
                        "task",
                        "public_status",
                        "source_path",
                        "rendered_pages",
                        "source_url",
                    ],
                )
                writer.writeheader()
                writer.writerow({"doc_id": "doc"})

            report = build_registration(root, download, render, date_label="demo")
            self.assertEqual(report["issues"], [])
            self.assertEqual(report["new_manifest_docs"], 1)
            preview = root / "preview.jsonl"
            write_manifest_preview(preview, report)
            preview_row = json.loads(preview.read_text(encoding="utf-8"))
            self.assertEqual(preview_row["doc_id"], "doc")
            apply_registration(root, report)
            manifest = json.loads((root / "manifest.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(manifest["doc_id"], "doc")
            self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(manifest["pages"], 120)
            self.assertEqual(manifest["same_model_id"], "handbook_series")
            self.assertEqual(manifest["direct_source_url"], "https://example.test/official.pdf")
            self.assertEqual(
                manifest["derived"]["retrieved_from_url"], "https://mirror.test/source.pdf"
            )
            self.assertEqual(manifest["derived"]["rendered_page_selection_1based"], "28-34")
            self.assertEqual(manifest["version"]["revision"], "1993")
            self.assertEqual(manifest["version"]["receipt_date"], "demo")
            self.assertEqual(manifest["render"]["colorspace"], "gray")
            self.assertEqual(
                manifest["license_note"],
                "Approved for public release; distribution is unlimited.",
            )
            self.assertEqual(manifest["rights_evidence_url"], "https://example.test/rights")
            self.assertEqual(manifest["rights_evidence_path"], "docs/RIGHTS.md")
            with (root / "SOURCE_INVENTORY.csv").open(encoding="utf-8", newline="") as handle:
                inventory = next(csv.DictReader(handle))
            self.assertEqual(inventory["source_path"], "docs/source.pdf")

    def test_commons_receipt_prefers_exact_file_page_and_maps_license(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"commons-image"
            source = root / "docs/source.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            page_dir = root / "derived/pages/doc"
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"png")
            download = root / "download.json"
            render = root / "render.json"
            download.write_text(
                json.dumps(
                    {
                        "receipts": [
                            {
                                "doc_id": "doc",
                                "candidate_id": "pid_1",
                                "domain": "pid",
                                "status": "downloaded",
                                "local_path": "docs/source.jpg",
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "source_url": "https://commons.wikimedia.org/wiki/File:Exact.jpg",
                                "page_url": "https://commons.wikimedia.org/wiki/Category:Diagrams",
                                "final_url": "https://upload.wikimedia.org/source.jpg",
                                "license_short_name": "CC BY-SA 3.0",
                                "artist": "Example Author",
                                "rights_capture": "commons_api_license_author_payload_sha256",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            render.write_text(
                json.dumps(
                    {
                        "render_receipts": [
                            {
                                "doc_id": "doc",
                                "status": "rendered_image",
                                "pages_dir": "derived/pages/doc",
                                "rendered_pages": 1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "domain", "task", "public_status", "source_path", "rendered_pages", "source_url"],
                )
                writer.writeheader()

            report = build_registration(root, download, render, date_label="demo")
            manifest = report["records"][0]["manifest_row"]
            self.assertEqual(manifest["source_url"], "https://commons.wikimedia.org/wiki/File:Exact.jpg")
            self.assertEqual(manifest["public_status"], "cc_by_sa_3_0_commons_candidate")
            self.assertEqual(manifest["rights_evidence_url"], manifest["source_url"])
            self.assertTrue(manifest["rights_evidence_path"].endswith("download.json"))
            self.assertIn("Example Author", manifest["license_note"])

    def test_nasa_ntrs_receipt_maps_government_public_use_determination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"nasa-ntrs-report"
            source = root / "docs/source.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            page_dir = root / "derived/pages/doc"
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"png")
            download = root / "download.json"
            render = root / "render.json"
            download.write_text(
                json.dumps(
                    {
                        "receipts": [
                            {
                                "doc_id": "doc",
                                "candidate_id": "pid_70",
                                "domain": "pid",
                                "status": "downloaded",
                                "local_path": "docs/source.pdf",
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "source_url": "https://ntrs.nasa.gov/citations/19920001990",
                                "final_url": "https://ntrs.nasa.gov/api/citations/19920001990/downloads/19920001990.pdf",
                                "license_short_name": "GOV_PUBLIC_USE_PERMITTED",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            render.write_text(
                json.dumps(
                    {
                        "render_receipts": [
                            {
                                "doc_id": "doc",
                                "status": "rendered_pdf_textlayer",
                                "pages_dir": "derived/pages/doc",
                                "rendered_pages": 1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "domain", "task", "public_status", "source_path", "rendered_pages", "source_url"],
                )
                writer.writeheader()

            report = build_registration(root, download, render, date_label="demo")
            manifest = report["records"][0]["manifest_row"]
            self.assertEqual(
                manifest["public_status"],
                "government_public_use_permitted_nasa_candidate",
            )
            self.assertEqual(manifest["rights_evidence_url"], manifest["source_url"])
            self.assertTrue(manifest["rights_evidence_path"].endswith("download.json"))
            self.assertIn("NASA NTRS metadata", manifest["license_note"])

    def test_refreshes_existing_hash_matched_manifest_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"receipt-backed-image"
            source = root / "docs/source.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            page_dir = root / "derived/pages/doc"
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"png")
            download = root / "download.json"
            render = root / "render.json"
            download.write_text(
                json.dumps(
                    {
                        "receipts": [
                            {
                                "doc_id": "doc",
                                "candidate_id": "pid_1",
                                "domain": "pid",
                                "status": "downloaded",
                                "local_path": "docs/source.jpg",
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "source_url": "https://commons.wikimedia.org/wiki/File:Exact.jpg",
                                "final_url": "https://upload.wikimedia.org/source.jpg",
                                "license_short_name": "CC BY 4.0",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            render.write_text(
                json.dumps(
                    {
                        "render_receipts": [
                            {
                                "doc_id": "doc",
                                "status": "rendered_image",
                                "pages_dir": "derived/pages/doc",
                                "rendered_pages": 1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stale = {
                "type": "doc",
                "doc_id": "doc",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "source_url": "https://example.test/stale",
            }
            (root / "manifest.jsonl").write_text(json.dumps(stale) + "\n", encoding="utf-8")
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "domain", "task", "public_status", "source_path", "rendered_pages", "source_url"],
                )
                writer.writeheader()
                writer.writerow({"doc_id": "doc"})

            report = build_registration(root, download, render, date_label="demo")
            apply_registration(root, report, refresh_existing=True)
            rows = [json.loads(line) for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(rows))
            self.assertEqual(rows[0]["source_url"], "https://commons.wikimedia.org/wiki/File:Exact.jpg")
            self.assertEqual(rows[0]["public_status"], "cc_by_4_0_commons_candidate")

    def test_parses_version_json_from_csv_preserved_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"receipt-backed-pdf"
            source = root / "docs/source.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            page_dir = root / "derived/pages/doc"
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"png")
            download = root / "download.json"
            render = root / "render.json"
            download.write_text(
                json.dumps(
                    {
                        "receipts": [
                            {
                                "doc_id": "doc",
                                "candidate_id": "civil_1",
                                "domain": "civil",
                                "asset_kind": "pdf",
                                "status": "downloaded",
                                "local_path": "docs/source.pdf",
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "page_url": "https://example.test/index",
                                "final_url": "https://example.test/source.pdf",
                                "public_status": "public_domain_us_federal_candidate",
                                "version_json": '{"sheet":"W101-1"}',
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            render.write_text(
                json.dumps(
                    {
                        "render_receipts": [
                            {
                                "doc_id": "doc",
                                "status": "rendered_pdf_textlayer",
                                "pages_dir": "derived/pages/doc",
                                "rendered_pages": 1,
                                "dpi": 300,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "doc_id",
                        "domain",
                        "task",
                        "public_status",
                        "source_path",
                        "rendered_pages",
                        "source_url",
                    ],
                )
                writer.writeheader()

            report = build_registration(root, download, render, date_label="demo")
            self.assertEqual(report["issues"], [])
            self.assertEqual(report["records"][0]["manifest_row"]["version"]["sheet"], "W101-1")


if __name__ == "__main__":
    unittest.main()
