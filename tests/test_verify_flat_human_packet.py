import csv
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.verify_flat_human_packet import (
    broken_html_links,
    csv_rows,
    workbook_valid,
    write_report,
)


class VerifyFlatHumanPacketTest(unittest.TestCase):
    def test_helpers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "rows.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["id"])
                writer.writeheader()
                writer.writerow({"id": "one"})
            self.assertEqual(csv_rows(csv_path), 1)

            workbook = root / "book.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("[Content_Types].xml", "ok")
            self.assertTrue(workbook_valid(workbook))

            (root / "image.png").write_bytes(b"png")
            (root / "index.html").write_text(
                '<img src="image.png"><a href="missing.png">x</a>',
                encoding="utf-8",
            )
            self.assertEqual(len(broken_html_links(root)), 1)

            report_path = root / "nested" / "packet_report.json"
            write_report(report_path, {"passed": True})
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
