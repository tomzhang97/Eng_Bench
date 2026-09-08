from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import build_source_import_task_cards


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_task_cards_split_reachable_and_fallback_rows(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.csv"
    write_csv(
        preflight,
        [
            {
                "rank": "1",
                "candidate_id": "repo_001",
                "domain": "mechanical_cad",
                "task_fit": "visualdiff,microtext",
                "source_url": "https://github.com/example/repo",
                "preflight_status": "reachable",
                "http_status": "200",
                "final_url": "https://github.com/example/repo",
                "content_type": "text/html",
                "content_length": "100",
                "import_strategy": "git_repository_page",
                "operator_next_step": "select_release_assets_or_repo_paths",
                "error": "",
            },
            {
                "rank": "2",
                "candidate_id": "commons_001",
                "domain": "pid",
                "task_fit": "microtext",
                "source_url": "https://commons.wikimedia.org/wiki/Category:Process_flow_diagrams",
                "preflight_status": "reachable",
                "http_status": "200",
                "final_url": "https://commons.wikimedia.org/wiki/Category:Process_flow_diagrams",
                "content_type": "text/html",
                "content_length": "0",
                "import_strategy": "wikimedia_commons_category",
                "operator_next_step": "select_files_and_capture_per_file_license",
                "error": "",
            },
            {
                "rank": "3",
                "candidate_id": "bad_001",
                "domain": "civil",
                "task_fit": "visualdiff,microtext",
                "source_url": "https://offline.example.test/",
                "preflight_status": "failed",
                "http_status": "0",
                "final_url": "https://offline.example.test/",
                "content_type": "",
                "content_length": "0",
                "import_strategy": "html_index_or_product_page",
                "operator_next_step": "inspect_page_and_select_direct_assets",
                "error": "URLError",
            },
        ],
    )

    report = build_source_import_task_cards.build_report(
        root=tmp_path,
        preflight_csv=preflight,
    )

    assert report["totals"]["import_tasks"] == 2
    assert report["totals"]["fallback_tasks"] == 1
    cards = {row["candidate_id"]: row for row in report["import_tasks"]}
    assert cards["repo_001"]["task_card_type"] == "repo_asset_selection"
    assert "clone or download" in cards["repo_001"]["operator_checklist"][0]
    assert cards["commons_001"]["task_card_type"] == "commons_file_selection"
    assert "per-file license" in cards["commons_001"]["operator_checklist"][0]
    assert report["fallback_tasks"][0]["candidate_id"] == "bad_001"
    assert report["fallback_tasks"][0]["task_card_type"] == "browser_manual_fallback"


def test_task_cards_cli_writes_reports(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.csv"
    out_json = tmp_path / "cards.json"
    out_md = tmp_path / "cards.md"
    out_csv = tmp_path / "cards.csv"
    write_csv(
        preflight,
        [
            {
                "rank": "1",
                "candidate_id": "pdf_001",
                "domain": "civil",
                "task_fit": "microtext",
                "source_url": "https://example.test/doc.pdf",
                "preflight_status": "reachable",
                "http_status": "200",
                "final_url": "https://example.test/doc.pdf",
                "content_type": "application/pdf",
                "content_length": "100",
                "import_strategy": "direct_pdf",
                "operator_next_step": "download_exact_url_and_hash",
                "error": "",
            }
        ],
    )

    exit_code = build_source_import_task_cards.main(
        [
            "--root",
            str(tmp_path),
            "--preflight-csv",
            str(preflight),
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
    assert payload["import_tasks"][0]["candidate_id"] == "pdf_001"
    assert "Source Import Task Cards" in markdown
    assert "Do not merge unreviewed rows into gold" in markdown
    assert rows[0]["candidate_id"] == "pdf_001"
