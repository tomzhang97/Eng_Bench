from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import prepare_nrcs_nd_stockwater_intake as prepare


class PrepareNrcsNdStockwaterIntakeTests(unittest.TestCase):
    def test_builds_official_federal_archive_queue(self) -> None:
        rows = prepare.build_rows("2026-08-22-wave500")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["candidate_id"], "civil_027")
        self.assertEqual(row["asset_kind"], "archive")
        self.assertEqual(row["public_status"], "public_domain_us_federal_candidate")
        self.assertTrue(row["direct_asset_url"].endswith("4-30-2025-2.zip"))
        self.assertEqual(json.loads(row["version_json"])["archive_date"], "2025-04-30")

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
                    "2026-08-22-wave500",
                    "--output-csv",
                    str(queue),
                    "--output-json",
                    str(report),
                ]
            )
            self.assertEqual(exit_code, 0)
            with queue.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["valid"])


if __name__ == "__main__":
    unittest.main()
