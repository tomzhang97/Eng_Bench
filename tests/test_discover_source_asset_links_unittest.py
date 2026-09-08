from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from tools import discover_source_asset_links


class DiscoverSourceAssetLinksUnittest(unittest.TestCase):
    def test_direct_asset_import_task_card_becomes_asset_link_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
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
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
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

            self.assertEqual(report["totals"]["rows"], 1)
            self.assertEqual(report["totals"]["checked_rows"], 1)
            self.assertEqual(report["totals"]["skipped_rows"], 0)
            self.assertEqual(report["totals"]["asset_links"], 1)
            self.assertEqual(report["totals"]["commons_file_pages"], 0)
            direct_rows = [
                row
                for row in report["flat_link_rows"]
                if row["candidate_id"] == "ds_019" and row["link_type"] == "asset"
            ]
            self.assertEqual(len(direct_rows), 1)
            self.assertEqual(direct_rows[0]["asset_kind"], "pdf")
            self.assertEqual(direct_rows[0]["url"], "https://example.test/manual.pdf")


if __name__ == "__main__":
    unittest.main()
