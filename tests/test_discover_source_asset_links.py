from __future__ import annotations

import csv
from pathlib import Path

from tools import discover_source_asset_links


def write_task_cards(path: Path) -> None:
    rows = [
        {
            "rank": "1",
            "candidate_id": "civil_001",
            "domain": "civil",
            "task_card_type": "html_asset_selection",
            "source_url": "https://example.test/standards",
            "final_url": "https://example.test/standards/index.html",
        },
        {
            "rank": "2",
            "candidate_id": "pid_001",
            "domain": "pid",
            "task_card_type": "commons_file_selection",
            "source_url": "https://commons.wikimedia.org/wiki/Category:Demo",
            "final_url": "https://commons.wikimedia.org/wiki/Category:Demo",
        },
        {
            "rank": "3",
            "candidate_id": "mech_001",
            "domain": "mechanical_cad",
            "task_card_type": "repo_asset_selection",
            "source_url": "https://github.com/example/repo",
            "final_url": "https://github.com/example/repo",
        },
        {
            "rank": "4",
            "candidate_id": "arch_001",
            "domain": "architectural",
            "task_card_type": "browser_manual_fallback",
            "source_url": "https://blocked.example.test",
            "final_url": "https://blocked.example.test",
        },
        {
            "rank": "5",
            "candidate_id": "ds_019",
            "domain": "datasheet_spec",
            "task_card_type": "direct_asset_import",
            "source_url": "https://example.test/manual.pdf",
            "final_url": "https://example.test/manual.pdf",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_extract_asset_links_resolves_downloadable_links() -> None:
    html = """
    <html><body>
      <a href="/standard/bridge-plan.pdf">Bridge plan PDF</a>
      <a href="details/plate.dwg">DWG drawing</a>
      <img src="images/sign-support.png" alt="Sign support">
      <a href="notes.html">HTML notes</a>
    </body></html>
    """

    links = discover_source_asset_links.extract_asset_links(html, "https://example.test/root/page.html")
    by_url = {link["url"]: link for link in links}

    assert by_url["https://example.test/standard/bridge-plan.pdf"]["asset_kind"] == "pdf"
    assert by_url["https://example.test/root/details/plate.dwg"]["asset_kind"] == "cad"
    assert by_url["https://example.test/root/images/sign-support.png"]["asset_kind"] == "image"
    assert "https://example.test/root/notes.html" not in by_url


def test_build_report_checks_html_and_commons_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "task_cards.csv"
    write_task_cards(csv_path)

    def fake_fetch(url: str, timeout: int) -> discover_source_asset_links.FetchResult:
        del timeout
        if "commons" in url:
            body = """
            <img src="//upload.wikimedia.org/wikipedia/commons/e/ea/Instrument_proc%C3%A9d%C3%A9.png">
            <a href="/wiki/File:Pump_PID.png">Pump P&amp;ID</a>
            """
        else:
            body = """
            <a href="/downloads/standard.pdf">Standard PDF</a>
            <img src="/boards/orthoT_thumbnail.webp">
            """
        return discover_source_asset_links.FetchResult(
            ok=True,
            url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body=body,
            error="",
        )

    report = discover_source_asset_links.build_report(
        tmp_path,
        task_cards_csv=csv_path,
        date_label="2026-06-16",
        fetcher=fake_fetch,
    )

    assert report["totals"]["rows"] == 5
    assert report["totals"]["checked_rows"] == 3
    assert report["totals"]["skipped_rows"] == 2
    assert report["totals"]["asset_links"] == 2
    assert report["totals"]["commons_file_pages"] == 1
    assert report["checked_rows"][0]["candidate_id"] == "civil_001"
    assert report["checked_rows"][1]["candidate_id"] == "pid_001"
    assert {row["candidate_id"] for row in report["skipped_rows"]} == {"mech_001", "arch_001"}


def test_build_report_promotes_direct_asset_import_without_fetching(tmp_path: Path) -> None:
    csv_path = tmp_path / "task_cards.csv"
    rows = [
        {
            "rank": "5",
            "candidate_id": "ds_019",
            "domain": "datasheet_spec",
            "task_card_type": "direct_asset_import",
            "source_url": "https://example.test/manual.pdf",
            "final_url": "https://example.test/manual.pdf",
        }
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    def fail_if_called(url: str, timeout: int) -> discover_source_asset_links.FetchResult:
        raise AssertionError(f"direct asset import should not fetch HTML: {url} {timeout}")

    report = discover_source_asset_links.build_report(
        tmp_path,
        task_cards_csv=csv_path,
        date_label="2026-07-05",
        fetcher=fail_if_called,
    )

    direct_rows = [
        row
        for row in report["flat_link_rows"]
        if row["candidate_id"] == "ds_019" and row["link_type"] == "asset"
    ]
    assert len(direct_rows) == 1
    assert direct_rows[0]["asset_kind"] == "pdf"
    assert direct_rows[0]["url"] == "https://example.test/manual.pdf"
    assert report["totals"]["asset_links"] == 1


def test_build_report_retries_transient_fetch_failure(tmp_path: Path) -> None:
    csv_path = tmp_path / "task_cards.csv"
    write_task_cards(csv_path)
    attempts = {"count": 0}

    def flaky_fetch(url: str, timeout: int) -> discover_source_asset_links.FetchResult:
        del timeout
        attempts["count"] += 1
        if attempts["count"] == 1:
            return discover_source_asset_links.FetchResult(
                ok=False,
                url=url,
                final_url=url,
                http_status=0,
                content_type="",
                body="",
                error="TimeoutError",
            )
        return discover_source_asset_links.FetchResult(
            ok=True,
            url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body='<a href="/downloads/standard.pdf">Standard PDF</a>',
            error="",
        )

    report = discover_source_asset_links.build_report(
        tmp_path,
        task_cards_csv=csv_path,
        date_label="2026-06-16",
        fetcher=flaky_fetch,
        retries=1,
    )

    assert attempts["count"] >= 2
    assert report["checked_rows"][0]["discovery_status"] == "links_found"
    assert report["checked_rows"][0]["retry_count"] == 1


def test_extract_filters_site_chrome_and_commons_thumbnails() -> None:
    html = """
    <html><body>
      <a href="/static/images/footer/wikimedia.svg">Wikimedia Foundation</a>
      <img src="/w/resources/assets/poweredby_mediawiki.svg">
      <img src="//upload.wikimedia.org/wikipedia/commons/thumb/f/fc/P%26ID.JPG/120px-P%26ID.JPG">
      <a href="/wiki/File:P%26ID.JPG">P&amp;ID file page</a>
      <a href="/downloads/control-schematic.pdf">Control schematic</a>
      <img src="/images/site-logo.svg" alt="Site logo">
    </body></html>
    """

    links = discover_source_asset_links.extract_asset_links(
        html,
        "https://commons.wikimedia.org/wiki/Category:Demo",
    )
    urls = {link["url"] for link in links}

    assert "https://commons.wikimedia.org/wiki/File:P%26ID.JPG" in urls
    assert "https://commons.wikimedia.org/downloads/control-schematic.pdf" in urls
    assert "https://commons.wikimedia.org/static/images/footer/wikimedia.svg" not in urls
    assert "https://commons.wikimedia.org/w/resources/assets/poweredby_mediawiki.svg" not in urls
    assert "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fc/P%26ID.JPG/120px-P%26ID.JPG" not in urls
    assert "https://commons.wikimedia.org/images/site-logo.svg" not in urls


def test_cli_writes_reports_without_network(tmp_path: Path) -> None:
    csv_path = tmp_path / "task_cards.csv"
    write_task_cards(csv_path)
    out_json = tmp_path / "assets.json"
    out_md = tmp_path / "assets.md"
    out_csv = tmp_path / "assets.csv"

    exit_code = discover_source_asset_links.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--task-cards-csv",
            str(csv_path),
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
    assert "Source Asset Link Discovery" in out_md.read_text(encoding="utf-8")
