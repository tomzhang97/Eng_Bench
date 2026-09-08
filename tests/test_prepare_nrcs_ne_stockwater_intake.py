from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import prepare_nrcs_ne_stockwater_intake as prepare
from tools.page_ranges import parse_page_selection


class PrepareNrcsNeStockwaterIntakeTests(unittest.TestCase):
    def test_builds_official_federal_pdf_queue(self) -> None:
        rows = prepare.build_rows("2026-08-22-wave529")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["candidate_id"], "civil_028")
        self.assertEqual(row["asset_kind"], "pdf")
        self.assertEqual(row["public_status"], "public_domain_us_federal_candidate")
        self.assertIn("CPSFile/29985", row["direct_asset_url"])
        self.assertEqual(json.loads(row["version_json"])["edition"], "2008-04")
        self.assertEqual(len(parse_page_selection(row["page_selection"], 250)), 89)

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
                    "2026-08-22-wave529",
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
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["valid"])
            self.assertFalse(payload["safe_to_merge_gold"])


if __name__ == "__main__":
    unittest.main()
