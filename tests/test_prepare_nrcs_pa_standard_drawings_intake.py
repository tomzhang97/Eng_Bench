import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.prepare_nrcs_pa_standard_drawings_intake import build_rows, drawing_code


class PrepareNrcsPaStandardDrawingsIntakeTests(unittest.TestCase):
    def test_build_rows_excludes_vacant_and_uses_url_code(self) -> None:
        catalog = {
            "items": [
                {
                    "asset_title": "PA-002A-r2 Chain Link Safety Fence",
                    "direct_asset_url": "https://example.test/PA-002A-r2%20Chain%20Link.pdf",
                },
                {
                    "asset_title": "NO. PA-027A Title 6 foot wall with surcharge",
                    "direct_asset_url": "https://example.test/PA-027B%206%20Wall.pdf",
                },
                {
                    "asset_title": "NO. PA-060 Title Vacant",
                    "direct_asset_url": "https://example.test/PA-060%20-%20Vacant.pdf",
                },
            ]
        }
        rows, excluded = build_rows(catalog, "2026-08-11-wave117")
        self.assertEqual([row["proposed_doc_id"] for row in rows], ["nrcs_pa_pa_002a_r2", "nrcs_pa_pa_027b"])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["reason"], "vacant_catalog_slot")

    def test_drawing_code_rejects_non_pa_filename(self) -> None:
        with self.assertRaises(ValueError):
            drawing_code("https://example.test/not-a-drawing.pdf")

    def test_drawing_code_preserves_parenthesized_official_revision(self) -> None:
        self.assertEqual(
            drawing_code("https://example.test/PA-071%2807%29%20Deep-Well%20Pump.pdf"),
            "PA-071(07)",
        )

    def test_cli_writes_valid_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "asset_title": "PA-084.1-2 AHF Floor Details",
                                "direct_asset_url": "https://example.test/PA-084.1-2%20AHF.pdf",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            script = Path(__file__).parents[1] / "tools" / "prepare_nrcs_pa_standard_drawings_intake.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--root",
                    str(root),
                    "--date-label",
                    "2026-08-11-wave117",
                    "--catalog",
                    str(catalog),
                    "--output-csv",
                    "queue.csv",
                    "--output-json",
                    "report.json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["valid"])
            self.assertEqual(report["documents"], 1)


if __name__ == "__main__":
    unittest.main()
