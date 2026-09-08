from __future__ import annotations

import csv
from pathlib import Path

from tools import build_ready_source_intake_manifest


def write_preflight(path: Path) -> None:
    rows = [
        {
            "queue_rank": "1",
            "candidate_id": "civil_001",
            "domain": "civil",
            "asset_kind": "pdf",
            "import_action": "download_hash_render_textlayer",
            "preflight_status": "ready_for_intake",
            "ready_for_intake": "True",
            "http_status": "200",
            "content_type": "application/pdf",
            "content_length": "12345",
            "error": "",
            "source_url": "https://dot.example.test/standards",
            "page_url": "https://dot.example.test/standards",
            "direct_asset_url": "https://dot.example.test/standard.pdf",
            "resolved_direct_asset_url": "https://dot.example.test/standard.pdf",
            "proposed_doc_id": "civil_001_standard",
            "proposed_local_path": "microtext/docs/source_intake_2026_06_16/civil_001_standard.pdf",
            "license_short_name": "",
            "license_url": "",
            "artist": "",
            "commons_sha1": "",
            "rights_capture": "public_agency_source_url_terms_sha256",
            "review_gate": "review_packet_required_before_gold",
        },
        {
            "queue_rank": "2",
            "candidate_id": "pid_001",
            "domain": "pid",
            "asset_kind": "commons_file_page",
            "import_action": "resolve_commons_license_and_payload",
            "preflight_status": "ready_for_intake",
            "ready_for_intake": "True",
            "http_status": "0",
            "content_type": "",
            "content_length": "0",
            "error": "",
            "source_url": "https://commons.wikimedia.org/wiki/File:Pump.svg",
            "page_url": "https://commons.wikimedia.org/wiki/Category:Demo",
            "direct_asset_url": "",
            "resolved_direct_asset_url": "https://upload.wikimedia.org/example/pump.svg",
            "proposed_doc_id": "pid_001_pump",
            "proposed_local_path": "",
            "license_short_name": "CC BY-SA 3.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
            "artist": "Example author",
            "commons_sha1": "abc123",
            "rights_capture": "commons_api_license_author_payload_sha256",
            "review_gate": "review_packet_required_before_gold",
        },
        {
            "queue_rank": "3",
            "candidate_id": "ds_001",
            "domain": "datasheet_spec",
            "asset_kind": "pdf",
            "import_action": "download_hash_render_textlayer",
            "preflight_status": "blocked_content_type_mismatch",
            "ready_for_intake": "False",
            "http_status": "200",
            "content_type": "text/html; charset=UTF-8",
            "content_length": "999",
            "error": "content_type_mismatch:text/html; charset=UTF-8",
            "source_url": "https://docs.example.test",
            "page_url": "https://docs.example.test",
            "direct_asset_url": "https://docs.example.test/file.pdf",
            "resolved_direct_asset_url": "https://docs.example.test/file.pdf",
            "proposed_doc_id": "ds_001_file",
            "proposed_local_path": "microtext/docs/source_intake_2026_06_16/ds_001_file.pdf",
            "license_short_name": "",
            "license_url": "",
            "artist": "",
            "commons_sha1": "",
            "rights_capture": "vendor_docs_license_terms_sha256",
            "review_gate": "review_packet_required_before_gold",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_manifest_splits_ready_and_blocked_rows(tmp_path: Path) -> None:
    input_csv = tmp_path / "preflight.csv"
    write_preflight(input_csv)

    report = build_ready_source_intake_manifest.build_report(
        tmp_path,
        preflight_csv=input_csv,
        date_label="2026-06-16",
    )

    assert report["totals"]["input_rows"] == 3
    assert report["totals"]["ready_rows"] == 2
    assert report["totals"]["repair_rows"] == 1
    ready = {row["candidate_id"]: row for row in report["ready_rows"]}
    repair = report["repair_rows"][0]

    assert ready["civil_001"]["download_url"] == "https://dot.example.test/standard.pdf"
    assert ready["civil_001"]["local_path"].endswith(".pdf")
    assert ready["pid_001"]["download_url"] == "https://upload.wikimedia.org/example/pump.svg"
    assert ready["pid_001"]["license_short_name"] == "CC BY-SA 3.0"
    assert ready["pid_001"]["local_path"].endswith(".svg")
    assert repair["repair_action"] == "replace_with_direct_payload_url"
    assert repair["candidate_id"] == "ds_001"


def test_cli_writes_manifest_and_repair_outputs(tmp_path: Path) -> None:
    input_csv = tmp_path / "preflight.csv"
    write_preflight(input_csv)
    out_json = tmp_path / "ready.json"
    out_md = tmp_path / "ready.md"
    out_ready = tmp_path / "ready.csv"
    out_repair = tmp_path / "repair.csv"

    exit_code = build_ready_source_intake_manifest.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--preflight-csv",
            str(input_csv),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--ready-csv",
            str(out_ready),
            "--repair-csv",
            str(out_repair),
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_ready.exists()
    assert out_repair.exists()
    assert "Ready Source Intake Manifest" in out_md.read_text(encoding="utf-8")


def test_manifest_rewrites_legacy_source_intake_dir_from_date_label(tmp_path: Path) -> None:
    input_csv = tmp_path / "preflight.csv"
    write_preflight(input_csv)

    report = build_ready_source_intake_manifest.build_report(
        tmp_path,
        preflight_csv=input_csv,
        date_label="2026-07-01y",
    )

    ready = {row["candidate_id"]: row for row in report["ready_rows"]}
    assert report["totals"]["import_dir"] == "microtext/docs/source_intake_2026_07_01y"
    assert ready["civil_001"]["local_path"] == (
        "microtext/docs/source_intake_2026_07_01y/civil_001_standard.pdf"
    )
    assert ready["pid_001"]["local_path"] == (
        "microtext/docs/source_intake_2026_07_01y/pid_001_pump.svg"
    )
