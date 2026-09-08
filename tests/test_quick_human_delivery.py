import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import build_quick_human_delivery as quick


WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets>
    <sheet name="开始" sheetId="1"/>
    <sheet name="MicroText" sheetId="2"/>
    <sheet name="VisualDiff" sheetId="3"/>
  </sheets>
</workbook>
"""


def write_synthetic_workbook(path: Path, media_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("xl/workbook.xml", WORKBOOK_XML)
        for index in range(media_count):
            archive.writestr(f"xl/media/image{index + 1}.png", f"image-{index}".encode())


class QuickHumanDeliveryTests(unittest.TestCase):
    def test_builds_one_file_per_person_crc_clean_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            write_synthetic_workbook(
                source / "01_PRIMARY_REVIEWER" / "PRIMARY_REVIEW_498.xlsx",
                498,
            )
            for number in range(1, 11):
                write_synthetic_workbook(
                    source
                    / "02_INDEPENDENT_AUDITORS"
                    / f"auditor_{number:02d}"
                    / f"AUDITOR_{number:02d}_REVIEW_12.xlsx",
                    12,
                )

            delivery = root / "Eng_Bench_Quick_Human_Audit"
            archive = root / "delivery.zip"
            report = quick.build_delivery(source, delivery, archive, overwrite=False)

            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual(report["workbook_count"], 11)
            self.assertEqual(report["embedded_images"], 618)
            self.assertEqual(len(list(delivery.rglob("*.xlsx"))), 11)
            self.assertEqual(len(list(delivery.rglob("*.zip"))), 0)
            self.assertTrue((delivery / "00_START_HERE_ZH.txt").exists())
            self.assertFalse((delivery / "00_READ_ME_FIRST_ZH.txt").exists())
            with zipfile.ZipFile(archive, "r") as built:
                self.assertIsNone(built.testzip())
                self.assertFalse(any(name.lower().endswith(".zip") for name in built.namelist()))

    def test_unsafe_zip_paths_are_detected(self) -> None:
        self.assertTrue(quick.unsafe_zip_name("../escape.txt"))
        self.assertTrue(quick.unsafe_zip_name("/absolute.txt"))
        self.assertFalse(quick.unsafe_zip_name("root/folder/file.xlsx"))


if __name__ == "__main__":
    unittest.main()
