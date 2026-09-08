from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import preflight_source_urls


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fake_fetcher(url: str, timeout: int) -> preflight_source_urls.FetchResult:
    if url.endswith("/doc.pdf"):
        return preflight_source_urls.FetchResult(
            ok=True,
            status_code=200,
            final_url=url,
            content_type="application/pdf",
            content_length=12345,
            error="",
        )
    if "github.com" in url:
        return preflight_source_urls.FetchResult(
            ok=True,
            status_code=200,
            final_url=url,
            content_type="text/html; charset=utf-8",
            content_length=777,
            error="",
        )
    if "commons.wikimedia.org" in url:
        return preflight_source_urls.FetchResult(
            ok=True,
            status_code=200,
            final_url=url,
            content_type="text/html",
            content_length=888,
            error="",
        )
    return preflight_source_urls.FetchResult(
        ok=False,
        status_code=0,
        final_url=url,
        content_type="",
        content_length=0,
        error="timeout",
    )


def test_preflight_classifies_selected_source_urls(tmp_path: Path) -> None:
    input_csv = tmp_path / "selected.csv"
    write_csv(
        input_csv,
        [
            {
                "rank": "1",
                "candidate_id": "pdf_001",
                "domain": "civil",
                "task_fit": "microtext",
                "source_url": "https://example.test/doc.pdf",
                "notes": "direct PDF",
            },
            {
                "rank": "2",
                "candidate_id": "repo_001",
                "domain": "mechanical_cad",
                "task_fit": "visualdiff,microtext",
                "source_url": "https://github.com/example/repo",
                "notes": "repository",
            },
            {
                "rank": "3",
                "candidate_id": "commons_001",
                "domain": "pid",
                "task_fit": "microtext",
                "source_url": "https://commons.wikimedia.org/wiki/Category:Piping_and_instrumentation_diagrams",
                "notes": "commons category",
            },
            {
                "rank": "4",
                "candidate_id": "bad_001",
                "domain": "pid",
                "task_fit": "microtext",
                "source_url": "https://offline.example.test/",
                "notes": "bad",
            },
        ],
    )

    report = preflight_source_urls.build_report(
        root=tmp_path,
        input_csv=input_csv,
        fetcher=fake_fetcher,
        timeout=5,
    )

    assert report["totals"]["rows"] == 4
    assert report["totals"]["reachable"] == 3
    assert report["totals"]["failed"] == 1
    rows = {row["candidate_id"]: row for row in report["rows"]}
    assert rows["pdf_001"]["import_strategy"] == "direct_pdf"
    assert rows["pdf_001"]["operator_next_step"] == "download_exact_url_and_hash"
    assert rows["repo_001"]["import_strategy"] == "git_repository_page"
    assert rows["repo_001"]["operator_next_step"] == "select_release_assets_or_repo_paths"
    assert rows["commons_001"]["import_strategy"] == "wikimedia_commons_category"
    assert rows["commons_001"]["operator_next_step"] == "select_files_and_capture_per_file_license"
    assert rows["bad_001"]["preflight_status"] == "failed"


def test_preflight_cli_writes_reports_without_network_when_disabled(tmp_path: Path) -> None:
    input_csv = tmp_path / "selected.csv"
    out_json = tmp_path / "preflight.json"
    out_md = tmp_path / "preflight.md"
    out_csv = tmp_path / "preflight.csv"
    write_csv(
        input_csv,
        [
            {
                "rank": "1",
                "candidate_id": "offline_001",
                "domain": "civil",
                "task_fit": "microtext",
                "source_url": "https://example.test/doc.pdf",
                "notes": "offline",
            }
        ],
    )

    exit_code = preflight_source_urls.main(
        [
            "--root",
            str(tmp_path),
            "--input-csv",
            str(input_csv),
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
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    markdown = out_md.read_text(encoding="utf-8")
    with out_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert payload["totals"]["rows"] == 1
    assert payload["rows"][0]["preflight_status"] == "not_checked"
    assert "Source URL Preflight" in markdown
    assert rows[0]["candidate_id"] == "offline_001"


def test_preflight_prefers_direct_asset_over_provenance_page(tmp_path: Path) -> None:
    input_csv = tmp_path / "selected.csv"
    write_csv(
        input_csv,
        [
            {
                "rank": "1",
                "candidate_id": "pdf_002",
                "domain": "civil",
                "task_fit": "microtext",
                "source_url": "https://example.test/index",
                "direct_asset_url": "https://example.test/doc.pdf",
            }
        ],
    )

    report = preflight_source_urls.build_report(
        root=tmp_path,
        input_csv=input_csv,
        fetcher=fake_fetcher,
        timeout=5,
    )

    row = report["rows"][0]
    assert row["source_url"] == "https://example.test/index"
    assert row["probe_url"] == "https://example.test/doc.pdf"
    assert row["preflight_status"] == "reachable"
    assert row["import_strategy"] == "direct_pdf"


def test_preflight_classifies_direct_archive() -> None:
    strategy, next_step = preflight_source_urls.classify_strategy(
        "https://example.test/drawings.zip",
        "application/zip",
    )

    assert strategy == "direct_archive"
    assert next_step == "download_hash_and_inspect_archive"
