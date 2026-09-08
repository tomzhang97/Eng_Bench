from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import download_ready_source_assets


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "intake_rank",
        "queue_rank",
        "candidate_id",
        "domain",
        "asset_kind",
        "import_action",
        "doc_id",
        "local_path",
        "download_url",
        "source_url",
        "page_url",
        "content_type",
        "content_length",
        "license_short_name",
        "license_url",
        "artist",
        "commons_sha1",
        "rights_capture",
        "review_gate",
        "public_status",
        "license_note",
        "rights_evidence_url",
        "rights_evidence_path",
        "same_model_id",
        "doc_type",
        "version_json",
        "page_selection",
        "full_page_count",
        "preflight_status",
        "ready_for_intake",
        "intake_status",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def manifest_row(**overrides: str) -> dict[str, str]:
    row = {
        "intake_rank": "1",
        "queue_rank": "1",
        "candidate_id": "civil_001",
        "domain": "civil",
        "asset_kind": "pdf",
        "import_action": "download_hash_render_textlayer",
        "doc_id": "civil_001_standard",
        "local_path": "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf",
        "download_url": "https://example.test/standard.pdf",
        "source_url": "https://example.test/standards",
        "page_url": "https://example.test/standards",
        "content_type": "application/pdf",
        "content_length": "7",
        "license_short_name": "",
        "license_url": "",
        "artist": "",
        "commons_sha1": "",
        "rights_capture": "public_agency_source_url_terms_sha256",
        "review_gate": "review_packet_required_before_gold",
        "public_status": "public_domain_us_federal_candidate",
        "license_note": "Federal public-domain basis recorded.",
        "rights_evidence_url": "https://example.test/rights",
        "rights_evidence_path": "docs/RIGHTS.md",
        "same_model_id": "civil_standards",
        "doc_type": "federal_standard_drawing_pdf",
        "version_json": "{\"revision\":\"2026\"}",
        "page_selection": "all",
        "full_page_count": "2",
        "preflight_status": "ready_for_intake",
        "ready_for_intake": "True",
        "intake_status": "ready_download_hash_then_render",
        "notes": "",
    }
    row.update(overrides)
    return row


def test_downloader_writes_payload_and_sha256_receipt(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row()])
    payload = b"payload"

    def fake_downloader(
        url: str,
        output_path: Path,
        *,
        timeout: float,
        max_bytes: int | None,
    ) -> dict[str, str | int]:
        assert url == "https://example.test/standard.pdf"
        assert timeout == 10
        assert max_bytes == 100
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
        return {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "content_type": "application/pdf",
            "final_url": url,
        }

    report = download_ready_source_assets.build_report(
        tmp_path,
        manifest_csv=manifest,
        date_label="2026-06-16",
        max_bytes=100,
        timeout=10,
        downloader=fake_downloader,
    )

    receipt = report["receipts"][0]
    assert receipt["status"] == "downloaded"
    assert receipt["bytes"] == len(payload)
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["public_status"] == "public_domain_us_federal_candidate"
    assert receipt["version_json"] == '{"revision":"2026"}'
    assert (tmp_path / "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf").read_bytes() == payload


def test_downloader_skips_large_rows_before_fetch(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row(content_length="101")])

    def forbidden_downloader(
        url: str,
        output_path: Path,
        *,
        timeout: float,
        max_bytes: int | None,
    ) -> dict[str, str | int]:
        raise AssertionError("large row should be skipped before network fetch")

    report = download_ready_source_assets.build_report(
        tmp_path,
        manifest_csv=manifest,
        date_label="2026-06-16",
        max_bytes=100,
        downloader=forbidden_downloader,
    )

    receipt = report["receipts"][0]
    assert receipt["status"] == "skipped_max_bytes"
    assert receipt["bytes"] == ""
    assert not (tmp_path / "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf").exists()


def test_downloader_rejects_manifest_content_length_mismatch(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    row = manifest_row(content_length="10")
    write_manifest(manifest, [row])

    def short_downloader(
        url: str,
        output_path: Path,
        *,
        timeout: float,
        max_bytes: int | None,
    ) -> dict[str, str | int]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"short")
        return {
            "bytes": 5,
            "sha256": "a" * 64,
            "content_type": "application/pdf",
            "final_url": url,
        }

    report = download_ready_source_assets.build_report(
        tmp_path,
        manifest_csv=manifest,
        max_bytes=100,
        downloader=short_downloader,
    )

    receipt = report["receipts"][0]
    assert receipt["status"] == "failed_content_length_mismatch"
    assert receipt["error"] == "download_content_length_mismatch:5!=10"
    assert not (tmp_path / row["local_path"]).exists()


def test_downloader_rejects_existing_payload_with_wrong_size(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    row = manifest_row(content_length="10")
    write_manifest(manifest, [row])
    output_path = tmp_path / row["local_path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"short")

    report = download_ready_source_assets.build_report(
        tmp_path,
        manifest_csv=manifest,
        max_bytes=100,
    )

    receipt = report["receipts"][0]
    assert receipt["status"] == "failed_existing_size_mismatch"
    assert receipt["error"] == "existing_content_length_mismatch:5!=10"


def test_cli_dry_run_writes_receipts_without_payload(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    out_json = tmp_path / "receipts.json"
    out_md = tmp_path / "receipts.md"
    out_csv = tmp_path / "receipts.csv"
    write_manifest(manifest, [manifest_row()])

    exit_code = download_ready_source_assets.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--manifest-csv",
            str(manifest),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert json.loads(out_json.read_text(encoding="utf-8"))["totals"]["dry_run"] == 1
    with out_csv.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["status"] == "dry_run"
    assert not (tmp_path / "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf").exists()


class DownloadReadySourceAssetsUnittest(unittest.TestCase):
    def test_build_report_preserves_provenance_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "manifest.csv"
            write_manifest(manifest, [manifest_row()])

            def fake_downloader(
                url: str,
                output_path: Path,
                *,
                timeout: float,
                max_bytes: int | None,
            ) -> dict[str, str | int]:
                del timeout, max_bytes
                payload = b"payload"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(payload)
                return {
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "content_type": "application/pdf",
                    "final_url": url,
                }

            report = download_ready_source_assets.build_report(
                root,
                manifest_csv=manifest,
                downloader=fake_downloader,
            )

        receipt = report["receipts"][0]
        self.assertEqual(receipt["public_status"], "public_domain_us_federal_candidate")
        self.assertEqual(receipt["version_json"], '{"revision":"2026"}')

    def test_build_report_skips_preflight_blocked_rows_before_fetch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "manifest.csv"
            write_manifest(
                manifest,
                [
                    manifest_row(
                        preflight_status="blocked_content_type_mismatch",
                        ready_for_intake="False",
                        download_url="https://example.test/not-a-real-zip",
                    )
                ],
            )

            def forbidden_downloader(
                url: str,
                output_path: Path,
                *,
                timeout: float,
                max_bytes: int | None,
            ) -> dict[str, str | int]:
                raise AssertionError("blocked preflight row should be skipped before network fetch")

            report = download_ready_source_assets.build_report(
                root,
                manifest_csv=manifest,
                date_label="2026-07-04",
                downloader=forbidden_downloader,
            )

        receipt = report["receipts"][0]
        self.assertEqual(receipt["status"], "skipped_not_ready_for_intake")
        self.assertEqual(report["totals"]["skipped_not_ready_for_intake"], 1)
        self.assertIn("blocked_content_type_mismatch", receipt["error"])
