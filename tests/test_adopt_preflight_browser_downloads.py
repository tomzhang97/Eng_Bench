from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import adopt_preflight_browser_downloads as adopt


class AdoptPreflightBrowserDownloadsTests(unittest.TestCase):
    def test_adopts_only_size_and_signature_verified_pdf(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            payload = b"%PDF-1.7\nverified"
            (downloads / "W101-1.pdf").write_bytes(payload)
            preflight = root / "preflight.csv"
            row = {
                "queue_rank": "1",
                "candidate_id": "civil_018",
                "domain": "civil",
                "asset_kind": "pdf",
                "import_action": "download_hash_render_textlayer",
                "source_url": "https://example.test/index",
                "page_url": "https://example.test/index",
                "direct_asset_url": "https://example.test/W101-1.pdf",
                "resolved_direct_asset_url": "https://example.test/W101-1.pdf",
                "proposed_doc_id": "fhwa_w101_1",
                "proposed_local_path": "docs/fhwa_w101_1.pdf",
                "content_type": "application/pdf",
                "content_length": str(len(payload)),
                "public_status": "public_domain_us_federal_candidate",
                "rights_capture": "public_domain_us_federal_candidate",
                "license_note": "Federal work.",
                "review_gate": "review_packet_required_before_gold",
            }
            with preflight.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=row.keys())
                writer.writeheader()
                writer.writerow(row)

            report = adopt.build_report(root, preflight, downloads)

            self.assertTrue(report["valid"])
            self.assertEqual(report["totals"]["downloaded"], 1)
            self.assertEqual((root / "docs/fhwa_w101_1.pdf").read_bytes(), payload)
            self.assertEqual(
                report["receipts"][0]["public_status"],
                "public_domain_us_federal_candidate",
            )

    def test_rejects_html_saved_as_pdf(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            payload = b"<html>blocked</html>"
            (downloads / "bad.pdf").write_bytes(payload)
            row = {
                "queue_rank": "1",
                "candidate_id": "civil_018",
                "domain": "civil",
                "asset_kind": "pdf",
                "direct_asset_url": "https://example.test/bad.pdf",
                "resolved_direct_asset_url": "https://example.test/bad.pdf",
                "proposed_doc_id": "bad",
                "proposed_local_path": "docs/bad.pdf",
                "content_length": str(len(payload)),
            }
            preflight = root / "preflight.csv"
            with preflight.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=row.keys())
                writer.writeheader()
                writer.writerow(row)

            report = adopt.build_report(root, preflight, downloads)

            self.assertFalse(report["valid"])
            self.assertEqual(report["receipts"][0]["error"], "invalid_pdf_signature")
            self.assertFalse((root / "docs/bad.pdf").exists())

    def test_adopts_url_encoded_browser_filename(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            payload = b"%PDF-1.7\nencoded filename"
            (downloads / "PA-071%2807%29%20Deep-Well%20Pump.pdf").write_bytes(payload)
            row = {
                "queue_rank": "1",
                "candidate_id": "civil_019",
                "domain": "civil",
                "asset_kind": "pdf",
                "direct_asset_url": (
                    "https://example.test/PA-071%2807%29%20Deep-Well%20Pump.pdf"
                ),
                "proposed_doc_id": "nrcs_pa_pa_071_07",
                "proposed_local_path": "docs/nrcs_pa_pa_071_07.pdf",
            }
            preflight = root / "preflight.csv"
            with preflight.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=row.keys())
                writer.writeheader()
                writer.writerow(row)

            report = adopt.build_report(root, preflight, downloads)

            self.assertTrue(report["valid"])
            self.assertEqual(report["totals"]["downloaded"], 1)
            self.assertEqual((root / "docs/nrcs_pa_pa_071_07.pdf").read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
