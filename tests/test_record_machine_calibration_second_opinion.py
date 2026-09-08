from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import record_machine_calibration_second_opinion as recorder


class MachineCalibrationSecondOpinionTest(unittest.TestCase):
    def test_records_hash_bound_prefill_without_release_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pack = Path(temp_dir)
            checklist = pack / "machine_certification_calibration_checklist.csv"
            with checklist.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=("sample_index", "candidate_id", "category", "proposed_text"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "sample_index": 1,
                        "candidate_id": "candidate_1",
                        "category": "pin_label",
                        "proposed_text": "P1",
                    }
                )
            sheets = pack / "contact_sheets"
            sheets.mkdir()
            Image.new("RGB", (20, 20), "white").save(sheets / "contact_sheet_01.png")
            output = pack / "machine_second_opinion_prefill.csv"
            report_path = pack / "machine_second_opinion_attestation.json"

            report = recorder.record_second_opinion(
                pack_dir=pack,
                checklist=checklist,
                output_csv=output,
                report_json=report_path,
                date_label="fixture",
                reviewer="fixture machine",
                decision="correct",
                all_rows_visually_inspected=True,
            )

            self.assertEqual(1, report["row_count"])
            self.assertFalse(report["machine_release_authority"])
            self.assertTrue(report["human_calibration_still_required"])
            self.assertTrue(output.is_file())
            self.assertTrue(report_path.is_file())
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                output_row = next(csv.DictReader(handle))
            self.assertEqual("correct", output_row["machine_second_opinion"])
            self.assertEqual("P1", output_row["proposed_text"])

    def test_refuses_uninspected_default_decisions(self) -> None:
        with self.assertRaisesRegex(ValueError, "all-rows-visually-inspected"):
            recorder.record_second_opinion(
                pack_dir=Path("."),
                checklist=Path("missing.csv"),
                output_csv=Path("output.csv"),
                report_json=Path("report.json"),
                date_label="fixture",
                reviewer="fixture",
                decision="correct",
                all_rows_visually_inspected=False,
            )


if __name__ == "__main__":
    unittest.main()
