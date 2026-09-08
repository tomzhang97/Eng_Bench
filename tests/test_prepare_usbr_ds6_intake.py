from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import prepare_usbr_ds6_intake as prepare


class PrepareUsbrDs6IntakeTests(unittest.TestCase):
    def test_builds_complete_unique_release_safe_queue(self) -> None:
        rows = prepare.build_rows("2026-08-11-wave116")
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["proposed_doc_id"] for row in rows}), 6)
        self.assertEqual(len({row["direct_asset_url"] for row in rows}), 6)
        self.assertEqual(sum(int(row["full_page_count"]) for row in rows), 168)
        self.assertTrue(
            all(row["public_status"] == "public_domain_us_federal_candidate" for row in rows)
        )
        self.assertTrue(
            all(row["review_gate"] == "review_packet_required_before_gold" for row in rows)
        )
        self.assertTrue(all(json.loads(row["version_json"])["edition"] for row in rows))

    def test_cli_writes_queue_and_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = root / "queue.csv"
            report = root / "report.json"
            exit_code = prepare.main(
                [
                    "--root",
                    str(root),
                    "--date-label",
                    "2026-08-11-wave116",
                    "--output-csv",
                    str(queue),
                    "--output-json",
                    str(report),
                ]
            )
            self.assertEqual(exit_code, 0)
            with queue.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 6)
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["valid"])


if __name__ == "__main__":
    unittest.main()
