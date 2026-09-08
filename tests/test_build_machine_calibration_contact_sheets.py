from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_machine_calibration_contact_sheets as builder


class MachineCalibrationContactSheetsTest(unittest.TestCase):
    def test_builds_numbered_sheets_from_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pack = Path(temp_dir)
            crops = pack / "crops"
            crops.mkdir()
            checklist = pack / "machine_certification_calibration_checklist.csv"
            fields = ["sample_index", "category", "proposed_text", "ocr_text", "crop_path"]
            with checklist.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for index in range(1, 6):
                    crop = crops / f"{index:04d}.png"
                    Image.new("RGB", (80, 30), "white").save(crop)
                    writer.writerow(
                        {
                            "sample_index": index,
                            "category": "component_value",
                            "proposed_text": f"R{index}",
                            "ocr_text": f"R{index}",
                            "crop_path": crop.relative_to(pack).as_posix(),
                        }
                    )

            output = pack / "contact_sheets"
            paths = builder.build_contact_sheets(
                pack,
                checklist=checklist,
                output_dir=output,
                columns=2,
                rows_per_sheet=4,
            )

            self.assertEqual([path.name for path in paths], ["contact_sheet_01.png", "contact_sheet_02.png"])
            self.assertTrue(all(path.is_file() for path in paths))


if __name__ == "__main__":
    unittest.main()
