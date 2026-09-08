from __future__ import annotations

import csv
from pathlib import Path

from tools import preflight_source_asset_import_queue


def write_queue(path: Path) -> None:
    rows = [
        {
            "queue_rank": "1",
            "candidate_id": "civil_001",
            "domain": "civil",
            "asset_kind": "pdf",
            "link_type": "asset",
            "asset_title": "Bridge Standard",
            "import_action": "download_hash_render_textlayer",
            "source_url": "https://dot.example.test/standards",
            "page_url": "https://dot.example.test/standards",
            "direct_asset_url": "https://dot.example.test/bridge-standard.pdf",
            "proposed_doc_id": "civil_001_bridge_standard",
            "proposed_local_path": "microtext/docs/source_intake_2026_06_16/civil_001_bridge_standard.pdf",
            "rights_capture": "public_agency_source_url_terms_sha256",
            "review_gate": "review_packet_required_before_gold",
            "public_status": "public_domain_us_federal_candidate",
            "license_note": "Federal government work with source and hash preserved.",
            "rights_evidence_url": "https://example.test/rights",
            "rights_evidence_path": "docs/RIGHTS.md",
            "same_model_id": "civil_standards",
            "doc_type": "federal_standard_drawing_pdf",
            "version_json": "{\"revision\":\"2026\"}",
            "page_selection": "all",
            "full_page_count": "2",
            "notes": "Review only.",
        },
        {
            "queue_rank": "2",
            "candidate_id": "pid_001",
            "domain": "pid",
            "asset_kind": "commons_file_page",
            "link_type": "commons_file_page",
            "asset_title": "Pump P&ID",
            "import_action": "resolve_commons_license_and_payload",
            "source_url": "https://commons.wikimedia.org/wiki/File:Pump_with_tank_pid_en.svg",
            "page_url": "https://commons.wikimedia.org/wiki/Category:Demo",
            "direct_asset_url": "",
            "proposed_doc_id": "pid_001_pump_with_tank",
            "proposed_local_path": "",
            "rights_capture": "commons_api_license_author_payload_sha256",
            "review_gate": "review_packet_required_before_gold",
        },
        {
            "queue_rank": "3",
            "candidate_id": "ds_001",
            "domain": "datasheet_spec",
            "asset_kind": "pdf",
            "link_type": "asset",
            "asset_title": "Missing Direct URL",
            "import_action": "download_hash_render_textlayer",
            "source_url": "https://docs.example.test",
            "page_url": "https://docs.example.test",
            "direct_asset_url": "",
            "proposed_doc_id": "ds_001_missing",
            "proposed_local_path": "microtext/docs/source_intake_2026_06_16/ds_001_missing.pdf",
            "rights_capture": "vendor_docs_license_terms_sha256",
            "review_gate": "review_packet_required_before_gold",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_preflight_marks_direct_and_commons_ready(tmp_path: Path) -> None:
    queue_csv = tmp_path / "queue.csv"
    write_queue(queue_csv)

    def fake_probe(url: str, timeout: int) -> preflight_source_asset_import_queue.ProbeResult:
        del timeout
        return preflight_source_asset_import_queue.ProbeResult(
            ok=url.endswith(".pdf"),
            url=url,
            final_url=url,
            http_status=200 if url.endswith(".pdf") else 404,
            content_type="application/pdf" if url.endswith(".pdf") else "",
            content_length=12345 if url.endswith(".pdf") else 0,
            error="",
        )

    def fake_commons(file_page_url: str, timeout: int) -> preflight_source_asset_import_queue.CommonsResult:
        del file_page_url, timeout
        return preflight_source_asset_import_queue.CommonsResult(
            ok=True,
            file_page_url="https://commons.wikimedia.org/wiki/File:Pump_with_tank_pid_en.svg",
            direct_payload_url="https://upload.wikimedia.org/example/pump.svg",
            license_short_name="CC BY-SA 3.0",
            license_url="https://creativecommons.org/licenses/by-sa/3.0/",
            artist="Example author",
            commons_sha1="abc123",
            error="",
        )

    report = preflight_source_asset_import_queue.build_report(
        tmp_path,
        queue_csv=queue_csv,
        date_label="2026-06-16",
        probe=fake_probe,
        commons_resolver=fake_commons,
    )

    rows = {row["candidate_id"]: row for row in report["rows"]}
    assert report["totals"]["input_rows"] == 3
    assert report["totals"]["ready_for_intake"] == 2
    assert report["totals"]["blocked_rows"] == 1
    assert rows["civil_001"]["preflight_status"] == "ready_for_intake"
    assert rows["civil_001"]["public_status"] == "public_domain_us_federal_candidate"
    assert rows["civil_001"]["version_json"] == '{"revision":"2026"}'
    assert rows["pid_001"]["preflight_status"] == "ready_for_intake"
    assert rows["pid_001"]["resolved_direct_asset_url"] == "https://upload.wikimedia.org/example/pump.svg"
    assert rows["ds_001"]["preflight_status"] == "blocked_missing_direct_url"


def test_commons_disallowed_license_blocks_row(tmp_path: Path) -> None:
    queue_csv = tmp_path / "queue.csv"
    write_queue(queue_csv)

    def fake_commons(file_page_url: str, timeout: int) -> preflight_source_asset_import_queue.CommonsResult:
        del file_page_url, timeout
        return preflight_source_asset_import_queue.CommonsResult(
            ok=True,
            file_page_url="https://commons.wikimedia.org/wiki/File:Pump_with_tank_pid_en.svg",
            direct_payload_url="https://upload.wikimedia.org/example/pump.svg",
            license_short_name="All rights reserved",
            license_url="",
            artist="Example author",
            commons_sha1="abc123",
            error="",
        )

    report = preflight_source_asset_import_queue.build_report(
        tmp_path,
        queue_csv=queue_csv,
        date_label="2026-06-16",
        no_network=True,
        commons_resolver=fake_commons,
    )

    pid_row = next(row for row in report["rows"] if row["candidate_id"] == "pid_001")
    assert pid_row["preflight_status"] == "blocked_commons_license"


def test_pdf_url_returning_html_is_blocked(tmp_path: Path) -> None:
    queue_csv = tmp_path / "queue.csv"
    write_queue(queue_csv)

    def html_probe(url: str, timeout: int) -> preflight_source_asset_import_queue.ProbeResult:
        del timeout
        return preflight_source_asset_import_queue.ProbeResult(
            ok=bool(url),
            url=url,
            final_url=url,
            http_status=200,
            content_type="text/html; charset=UTF-8",
            content_length=1000,
            error="",
        )

    report = preflight_source_asset_import_queue.build_report(
        tmp_path,
        queue_csv=queue_csv,
        date_label="2026-06-16",
        probe=html_probe,
        no_network=False,
    )

    civil_row = next(row for row in report["rows"] if row["candidate_id"] == "civil_001")
    assert civil_row["preflight_status"] == "blocked_content_type_mismatch"
    assert civil_row["ready_for_intake"] is False


def test_probe_retries_range_get_after_html_head(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, status: int, headers: dict[str, str]) -> None:
            self.status = status
            self.headers = headers

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://docs.example.test/manual.pdf"

    def fake_urlopen(request, timeout: int) -> FakeResponse:
        del timeout
        method = request.get_method()
        calls.append(method)
        if method == "HEAD":
            return FakeResponse(
                200,
                {
                    "Content-Type": "text/html; charset=UTF-8",
                    "Content-Length": "1234",
                },
            )
        return FakeResponse(
            206,
            {
                "Content-Type": "application/pdf",
                "Content-Range": "bytes 0-0/1234",
            },
        )

    monkeypatch.setattr(preflight_source_asset_import_queue, "urlopen", fake_urlopen)

    result = preflight_source_asset_import_queue.probe_url(
        "https://docs.example.test/manual.pdf",
        timeout=5,
    )

    assert calls == ["HEAD", "GET"]
    assert result.ok is True
    assert result.http_status == 206
    assert result.content_type == "application/pdf"
    assert result.content_length == 1234


def test_cli_writes_reports_without_network(tmp_path: Path) -> None:
    queue_csv = tmp_path / "queue.csv"
    write_queue(queue_csv)
    out_json = tmp_path / "preflight.json"
    out_md = tmp_path / "preflight.md"
    out_csv = tmp_path / "preflight.csv"

    exit_code = preflight_source_asset_import_queue.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--queue-csv",
            str(queue_csv),
            "--no-network",
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert "Source Asset Intake Preflight" in out_md.read_text(encoding="utf-8")
