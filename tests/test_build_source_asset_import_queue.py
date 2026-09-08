from __future__ import annotations

import csv
from pathlib import Path

from tools import build_source_asset_import_queue


def write_asset_links(path: Path) -> None:
    rows = [
        {
            "candidate_id": "ds_001",
            "rank": "6",
            "domain": "datasheet_spec",
            "task_card_type": "html_asset_selection",
            "source_url": "https://example.test/board",
            "page_url": "https://example.test/board",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://example.test/board-schematics.pdf",
            "text": "Board Schematics",
        },
        {
            "candidate_id": "ds_001",
            "rank": "6",
            "domain": "datasheet_spec",
            "task_card_type": "html_asset_selection",
            "source_url": "https://example.test/board",
            "page_url": "https://example.test/board",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://example.test/board-errata.pdf",
            "text": "Board Errata",
        },
        {
            "candidate_id": "ds_001",
            "rank": "6",
            "domain": "datasheet_spec",
            "task_card_type": "html_asset_selection",
            "source_url": "https://example.test/board",
            "page_url": "https://example.test/board",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "image",
            "score": "80",
            "url": "https://example.test/logo.png",
            "text": "Product Logo",
        },
        {
            "candidate_id": "pid_001",
            "rank": "2",
            "domain": "pid",
            "task_card_type": "commons_file_selection",
            "source_url": "https://commons.wikimedia.org/wiki/Category:Demo",
            "page_url": "https://commons.wikimedia.org/wiki/Category:Demo",
            "discovery_status": "links_found",
            "link_type": "commons_file_page",
            "asset_kind": "commons_file_page",
            "score": "70",
            "url": "https://commons.wikimedia.org/wiki/File:Pump_with_tank_pid_en.svg",
            "text": "",
        },
        {
            "candidate_id": "civil_001",
            "rank": "9",
            "domain": "civil",
            "task_card_type": "html_asset_selection",
            "source_url": "https://dot.example.test/standards",
            "page_url": "https://dot.example.test/standards",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://dot.example.test/bridge-standard.pdf",
            "text": "Bridge Standard Drawing",
        },
        {
            "candidate_id": "mech_001",
            "rank": "10",
            "domain": "mechanical_cad",
            "task_card_type": "html_asset_selection",
            "source_url": "https://cad.example.test",
            "page_url": "https://cad.example.test",
            "discovery_status": "no_links_found",
            "link_type": "",
            "asset_kind": "",
            "score": "",
            "url": "",
            "text": "",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_queue_prioritizes_importable_assets_and_caps_per_candidate(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    write_asset_links(input_csv)

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        date_label="2026-06-16",
        max_assets=10,
        max_per_candidate=1,
    )

    selected = report["selected_assets"]
    by_candidate = {row["candidate_id"]: row for row in selected}

    assert report["totals"]["input_rows"] == 6
    assert report["totals"]["eligible_rows"] == 4
    assert report["totals"]["selected_assets"] == 3
    assert by_candidate["ds_001"]["asset_title"] == "Board Schematics"
    assert by_candidate["ds_001"]["asset_kind"] == "pdf"
    assert by_candidate["ds_001"]["import_action"] == "download_hash_render_textlayer"
    assert by_candidate["pid_001"]["import_action"] == "resolve_commons_license_and_payload"
    assert by_candidate["civil_001"]["proposed_local_path"].endswith(".pdf")
    assert all("logo" not in row["asset_title"].lower() for row in selected)


def test_cli_writes_queue_reports(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    write_asset_links(input_csv)
    out_json = tmp_path / "queue.json"
    out_md = tmp_path / "queue.md"
    out_csv = tmp_path / "queue.csv"

    exit_code = build_source_asset_import_queue.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--asset-links-csv",
            str(input_csv),
            "--max-assets",
            "4",
            "--max-per-candidate",
            "2",
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
    assert "Source Asset Import Queue" in out_md.read_text(encoding="utf-8")


def test_queue_proposed_paths_are_unique_for_long_similar_titles(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    long_title = "Index Sheet with links to individual standard drawings and highlighting of changes"
    rows = []
    for idx, suffix in enumerate(["", " and e-notifications"], start=1):
        rows.append(
            {
                "candidate_id": "civil_001",
                "rank": "7",
                "domain": "civil",
                "task_card_type": "html_asset_selection",
                "source_url": "https://dot.example.test/standards",
                "page_url": "https://dot.example.test/standards",
                "discovery_status": "links_found",
                "link_type": "asset",
                "asset_kind": "pdf",
                "score": "100",
                "url": f"https://dot.example.test/standard-{idx}.pdf",
                "text": long_title + suffix,
            }
        )
    with input_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        date_label="2026-06-16",
        max_assets=2,
        max_per_candidate=2,
    )

    paths = [row["proposed_local_path"] for row in report["selected_assets"]]
    doc_ids = [row["proposed_doc_id"] for row in report["selected_assets"]]
    assert len(paths) == len(set(paths))
    assert len(doc_ids) == len(set(doc_ids))


def test_queue_uses_date_label_for_import_dir(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    write_asset_links(input_csv)

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        date_label="2026-07-01y",
        max_assets=3,
        max_per_candidate=1,
    )

    pdf_paths = [
        row["proposed_local_path"]
        for row in report["selected_assets"]
        if row["asset_kind"] != "commons_file_page"
    ]
    assert report["totals"]["import_dir"] == "microtext/docs/source_intake_2026_07_01y"
    assert pdf_paths
    assert all(path.startswith("microtext/docs/source_intake_2026_07_01y/") for path in pdf_paths)


def test_queue_deduplicates_same_asset_url_across_candidates(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    rows = [
        {
            "candidate_id": candidate_id,
            "rank": rank,
            "domain": "civil_architectural",
            "task_card_type": "loc_item_selection",
            "source_url": f"https://www.loc.gov/pictures/search/?q={query}&co=hh",
            "page_url": "https://www.loc.gov/pictures/item/md1862.sheet.00006a/",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "image",
            "score": "100",
            "url": "https://tile.loc.gov/storage-services/service/pnp/habshaer/md/md1800/md1862/sheet/00006r.jpg",
            "text": "Roof Plans and Framing Sections",
        }
        for candidate_id, rank, query in (
            ("arch_014", "14", "section"),
            ("arch_016", "16", "roof-plan"),
        )
    ]
    with input_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        max_assets=10,
        max_per_candidate=4,
    )

    assert report["totals"]["selected_assets"] == 1
    assert report["totals"]["excluded_in_run_duplicate_assets"] == 1
    assert report["totals"]["excluded_in_run_duplicates_by_reason"] == {"asset_url": 1}


def test_queue_ignores_generic_commons_link_text_for_doc_id(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    rows = [
        {
            "candidate_id": "pid_023",
            "rank": "2",
            "domain": "pid",
            "task_card_type": "commons_file_selection",
            "source_url": "https://commons.wikimedia.org/wiki/File:Sch%C3%A9ma_P%26ID.jpg",
            "page_url": "https://commons.wikimedia.org/wiki/File:Sch%C3%A9ma_P%26ID.jpg",
            "discovery_status": "links_found",
            "link_type": "commons_file_page",
            "asset_kind": "commons_file_page",
            "score": "70",
            "url": "https://commons.wikimedia.org/wiki/File:Sch%C3%A9ma_P%26ID.jpg",
            "text": "Jump to content",
        }
    ]
    with input_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        date_label="2026-07-01y",
    )

    selected = report["selected_assets"][0]
    assert selected["asset_title"] != "Jump to content"
    assert selected["proposed_doc_id"] != "pid_023_jump_to_content"
    assert selected["proposed_doc_id"].startswith("pid_023_sch")


def test_queue_can_exclude_existing_downloaded_and_inventory_assets(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    rows = [
        {
            "candidate_id": "ds_001",
            "rank": "1",
            "domain": "datasheet_spec",
            "task_card_type": "html_asset_selection",
            "source_url": "https://example.test/board",
            "page_url": "https://example.test/board",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://example.test/already-downloaded.pdf",
            "text": "Already Downloaded Schematic",
        },
        {
            "candidate_id": "civil_001",
            "rank": "2",
            "domain": "civil",
            "task_card_type": "html_asset_selection",
            "source_url": "https://dot.example.test/standards",
            "page_url": "https://dot.example.test/standards",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://dot.example.test/standard.pdf",
            "text": "Standard Drawing",
        },
        {
            "candidate_id": "mech_001",
            "rank": "3",
            "domain": "mechanical_cad",
            "task_card_type": "html_asset_selection",
            "source_url": "https://cad.example.test",
            "page_url": "https://cad.example.test",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://cad.example.test/new-drawing.pdf",
            "text": "New Drawing",
        },
    ]
    with input_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    receipts_dir = tmp_path / "derived" / "quality"
    receipts_dir.mkdir(parents=True)
    with (receipts_dir / "source_asset_download_receipts_2026-07-01.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=["download_url", "final_url", "doc_id", "local_path", "status"])
        writer.writeheader()
        writer.writerow(
            {
                "download_url": "https://example.test/already-downloaded.pdf",
                "final_url": "https://example.test/already-downloaded.pdf",
                "doc_id": "ds_001_already_downloaded_schematic",
                "local_path": "microtext/docs/source_intake_old/ds_001_already_downloaded_schematic.pdf",
                "status": "downloaded",
            }
        )

    with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["path", "task", "doc_id", "domain", "source_url"])
        writer.writeheader()
        writer.writerow(
            {
                "path": "microtext/docs/source_intake_2026_07_02ap/civil_001_standard_drawing.pdf",
                "task": "microtext",
                "doc_id": "civil_001_standard_drawing",
                "domain": "civil",
                "source_url": "https://dot.example.test/standard.pdf",
            }
        )

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        date_label="2026-07-02ap",
        max_assets=10,
        max_per_candidate=2,
        exclude_existing=True,
    )

    assert report["totals"]["eligible_rows"] == 3
    assert report["totals"]["excluded_existing_assets"] == 2
    assert report["totals"]["selected_assets"] == 1
    assert report["selected_assets"][0]["proposed_doc_id"] == "mech_001_new_drawing"


def test_url_only_existing_policy_allows_document_quality_upgrade(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    rows = [
        {
            "candidate_id": "arch_001",
            "rank": "1",
            "domain": "civil_architectural",
            "task_card_type": "loc_item_selection",
            "source_url": "https://www.loc.gov/pictures/search/?q=plan&co=hh",
            "page_url": "https://www.loc.gov/pictures/collection/hh/item/a.sheet.00001a/",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "image",
            "score": "100",
            "url": "https://tile.loc.gov/new-master-candidate.jpg",
            "text": "First Floor Plan",
        },
        {
            "candidate_id": "arch_002",
            "rank": "2",
            "domain": "civil_architectural",
            "task_card_type": "loc_item_selection",
            "source_url": "https://www.loc.gov/pictures/search/?q=plan&co=hh",
            "page_url": "https://www.loc.gov/pictures/collection/hh/item/b.sheet.00001a/",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "image",
            "score": "90",
            "url": "https://tile.loc.gov/already-processed.jpg",
            "text": "Second Floor Plan",
        },
    ]
    with input_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    receipts_dir = tmp_path / "derived" / "quality"
    receipts_dir.mkdir(parents=True)
    with (receipts_dir / "source_asset_download_receipts_wave.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=["download_url", "status"])
        writer.writeheader()
        writer.writerow(
            {
                "download_url": "https://tile.loc.gov/already-processed.jpg",
                "status": "downloaded",
            }
        )

    with (tmp_path / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["doc_id", "path"])
        writer.writeheader()
        writer.writerow(
            {
                "doc_id": "arch_001_first_floor_plan",
                "path": "microtext/docs/source_intake_old/arch_001_first_floor_plan.jpg",
            }
        )

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        max_assets=10,
        max_per_candidate=2,
        exclude_existing=True,
        existing_match_policy="url_only",
        download_receipts_glob="derived/quality/source_asset_download_receipts_*.csv",
    )

    assert report["totals"]["existing_match_policy"] == "url_only"
    assert report["totals"]["excluded_existing_by_reason"] == {"download_url": 1}
    assert [row["proposed_doc_id"] for row in report["selected_assets"]] == [
        "arch_001_first_floor_plan"
    ]


def test_queue_can_exclude_rights_hold_candidate_ids(tmp_path: Path) -> None:
    input_csv = tmp_path / "asset_links.csv"
    rows = [
        {
            "candidate_id": "civil_hold",
            "rank": "1",
            "domain": "civil",
            "task_card_type": "html_asset_selection",
            "source_url": "https://hold.example.test",
            "page_url": "https://hold.example.test",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "100",
            "url": "https://hold.example.test/standards.pdf",
            "text": "Bridge Standard Drawing",
        },
        {
            "candidate_id": "civil_ready",
            "rank": "2",
            "domain": "civil",
            "task_card_type": "html_asset_selection",
            "source_url": "https://ready.example.test",
            "page_url": "https://ready.example.test",
            "discovery_status": "links_found",
            "link_type": "asset",
            "asset_kind": "pdf",
            "score": "90",
            "url": "https://ready.example.test/standard.pdf",
            "text": "Road Standard Drawing",
        },
    ]
    with input_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    with (tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "candidate_id",
                "validation_date",
                "browser_result",
                "source_validity",
                "release_posture",
                "next_action",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "civil_hold",
                "validation_date": "2026-07-02",
                "browser_result": "checked",
                "source_validity": "rights_uncertain",
                "release_posture": "rights_hold",
                "next_action": "hold_rights_clearance",
                "notes": "No explicit redistribution license.",
            }
        )
        writer.writerow(
            {
                "candidate_id": "civil_ready",
                "validation_date": "2026-07-02",
                "browser_result": "checked",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "Usable public candidate.",
            }
        )

    report = build_source_asset_import_queue.build_report(
        tmp_path,
        asset_links_csv=input_csv,
        date_label="2026-07-02ap",
        max_assets=10,
        max_per_candidate=2,
        exclude_blocked_candidates=True,
    )

    assert report["totals"]["eligible_rows"] == 2
    assert report["totals"]["excluded_blocked_candidate_assets"] == 1
    assert report["totals"]["selected_assets"] == 1
    assert report["selected_assets"][0]["candidate_id"] == "civil_ready"
