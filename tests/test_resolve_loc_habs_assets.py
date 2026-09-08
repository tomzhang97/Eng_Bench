from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import resolve_loc_habs_assets


def write_task_cards(path: Path) -> None:
    rows = [
        {
            "rank": "1",
            "candidate_id": "arch_019",
            "domain": "civil_architectural",
            "task_card_type": "loc_item_selection",
            "source_url": "https://www.loc.gov/pictures/search/?q=site%20plan&co=hh",
            "final_url": "https://www.loc.gov/pictures/search/?q=site%20plan&co=hh",
        },
        {
            "rank": "2",
            "candidate_id": "pid_001",
            "domain": "pid",
            "task_card_type": "commons_file_selection",
            "source_url": "https://commons.wikimedia.org/wiki/File:Pump.svg",
            "final_url": "https://commons.wikimedia.org/wiki/File:Pump.svg",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def loc_payload() -> str:
    return json.dumps(
        {
            "results": [
                {
                    "title": "1. Site Plan in 1870-1890 - Dudley Farm",
                    "pk": "fl0707.sheet.00001a",
                    "call_number": "HABS FL-391",
                    "medium": "19 x 24 in. (B size)",
                    "collection": ["diof", "hh"],
                    "links": {
                        "item": "https://www.loc.gov/pictures/collection/hh/item/fl0707.sheet.00001a/",
                        "resource": "https://www.loc.gov/pictures/collection/hh/item/fl0707.sheet.00001a/resource/",
                    },
                    "image": {
                        "full": "https://tile.loc.gov/storage-services/service/pnp/habshaer/fl/fl0700/fl0707/sheet/00001r.jpg",
                        "thumb": "https://tile.loc.gov/storage-services/service/pnp/habshaer/fl/fl0700/fl0707/sheet/00001_150px.jpg",
                    },
                },
                {
                    "title": "General view of observation bunker",
                    "pk": "ca2580.photos.382473p",
                    "call_number": "HAER CAL,15-BORON.V,4D--1",
                    "medium": "4 x 5 in.",
                    "collection": ["diof", "hh"],
                    "links": {
                        "item": "https://www.loc.gov/pictures/collection/hh/item/ca2580.photos.382473p/",
                    },
                    "image": {
                        "full": "https://tile.loc.gov/storage-services/service/pnp/habshaer/ca/ca2500/ca2580/photos/382473pr.jpg",
                    },
                },
            ]
        }
    )


def test_build_report_keeps_loc_sheet_assets_and_skips_photos(tmp_path: Path) -> None:
    cards = tmp_path / "task_cards.csv"
    write_task_cards(cards)

    report = resolve_loc_habs_assets.build_report(
        tmp_path,
        task_cards_csv=cards,
        date_label="2026-07-02",
        fetcher=lambda url, timeout: loc_payload(),
        max_per_source=5,
    )

    assert report["totals"]["rows"] == 2
    assert report["totals"]["loc_rows"] == 1
    assert report["totals"]["asset_rows"] == 1
    assert report["totals"]["skipped_non_sheet_items"] == 1
    asset = report["asset_rows"][0]
    assert asset["candidate_id"] == "arch_019"
    assert asset["asset_kind"] == "image"
    assert asset["link_type"] == "asset"
    assert asset["discovery_status"] == "links_found"
    assert asset["url"].endswith("/sheet/00001r.jpg")
    assert asset["page_url"].endswith("/fl0707.sheet.00001a/")
    assert asset["loc_call_number"] == "HABS FL-391"


def test_loc_search_page_url_preserves_query_and_sets_page() -> None:
    url = resolve_loc_habs_assets.loc_search_page_url(
        "https://www.loc.gov/pictures/search/?q=boiler&co=hh",
        3,
    )

    assert "q=boiler" in url
    assert "co=hh" in url
    assert "sp=3" in url


def test_build_report_finds_sheet_assets_on_later_search_pages(tmp_path: Path) -> None:
    cards = tmp_path / "task_cards.csv"
    write_task_cards(cards)
    photo_payload = json.dumps({"results": [json.loads(loc_payload())["results"][1]]})
    sheet_payload = json.dumps({"results": [json.loads(loc_payload())["results"][0]]})

    def fetcher(url: str, timeout: int) -> str:
        del timeout
        return sheet_payload if "sp=2" in url else photo_payload

    report = resolve_loc_habs_assets.build_report(
        tmp_path,
        task_cards_csv=cards,
        date_label="2026-08-11",
        fetcher=fetcher,
        max_per_source=5,
        pages_per_source=2,
    )

    assert report["totals"]["asset_rows"] == 1
    assert report["totals"]["pages_fetched"] == 2
    assert report["asset_rows"][0]["search_page"] == 2


def test_cli_writes_loc_asset_reports_without_network(tmp_path: Path) -> None:
    cards = tmp_path / "task_cards.csv"
    fixture = tmp_path / "loc_fixture.json"
    out_json = tmp_path / "loc_assets.json"
    out_md = tmp_path / "loc_assets.md"
    out_csv = tmp_path / "loc_assets.csv"
    write_task_cards(cards)
    fixture.write_text(loc_payload(), encoding="utf-8")

    exit_code = resolve_loc_habs_assets.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-07-02",
            "--task-cards-csv",
            str(cards),
            "--fixture-json",
            str(fixture),
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
    assert "LOC HABS/HAER/HALS Asset Resolution" in out_md.read_text(encoding="utf-8")
    with out_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "arch_019"
