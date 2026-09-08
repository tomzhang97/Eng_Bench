from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import resolve_loc_habs_assets


def write_task_cards(path: Path) -> None:
    rows = [
        {
            "rank": "1",
            "candidate_id": "loc_boiler",
            "domain": "mechanical_process",
            "task_card_type": "loc_item_selection",
            "source_url": "https://www.loc.gov/pictures/search/?q=boiler&co=hh",
            "final_url": "https://www.loc.gov/pictures/search/?q=boiler&co=hh",
        }
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def item_payload(*, sheet: bool) -> str:
    if sheet:
        item = {
            "title": "Boiler House Equipment Plan and Section",
            "pk": "pa0123.sheet.00002a",
            "call_number": "HAER PA-123",
            "medium": "24 x 36 in.",
            "collection": ["hh"],
            "links": {
                "item": "https://www.loc.gov/pictures/collection/hh/item/pa0123.sheet.00002a/"
            },
            "image": {
                "full": "https://tile.loc.gov/storage-services/service/pnp/habshaer/pa/pa0100/pa0123/sheet/00002r.jpg"
            },
        }
    else:
        item = {
            "title": "General exterior view",
            "pk": "pa0123.photos.12345p",
            "collection": ["hh"],
            "links": {
                "item": "https://www.loc.gov/pictures/collection/hh/item/pa0123.photos.12345p/"
            },
            "image": {
                "full": "https://tile.loc.gov/storage-services/service/pnp/habshaer/pa/pa0100/pa0123/photos/12345pr.jpg"
            },
        }
    return json.dumps({"results": [item]})


def generic_item_payload() -> str:
    item = json.loads(item_payload(sheet=True))["results"][0]
    item["pk"] = "pa0123"
    item["links"]["item"] = "https://www.loc.gov/pictures/collection/hh/item/pa0123/"
    item["title"] = "Boiler House"
    return json.dumps({"results": [item]})


class ResolveLocHabsAssetsUnittest(unittest.TestCase):
    def test_search_page_url_preserves_query_and_sets_page(self) -> None:
        url = resolve_loc_habs_assets.loc_search_page_url(
            "https://www.loc.gov/pictures/search/?q=boiler&co=hh",
            3,
        )

        self.assertIn("q=boiler", url)
        self.assertIn("co=hh", url)
        self.assertIn("sp=3", url)

    def test_report_finds_and_deduplicates_sheet_on_later_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cards = root / "task_cards.csv"
            write_task_cards(cards)

            def fetcher(url: str, timeout: int) -> str:
                del timeout
                if "sp=1" in url:
                    return item_payload(sheet=False)
                if "sp=2" in url:
                    return generic_item_payload()
                return item_payload(sheet=True)

            report = resolve_loc_habs_assets.build_report(
                root,
                task_cards_csv=cards,
                date_label="2026-08-11",
                fetcher=fetcher,
                max_per_source=5,
                pages_per_source=3,
            )

        self.assertEqual(report["totals"]["asset_rows"], 1)
        self.assertEqual(report["totals"]["pages_fetched"], 3)
        self.assertEqual(report["totals"]["duplicate_assets_skipped"], 1)
        self.assertEqual(report["asset_rows"][0]["search_page"], 3)
        self.assertIn(".sheet.", report["asset_rows"][0]["page_url"])


if __name__ == "__main__":
    unittest.main()
