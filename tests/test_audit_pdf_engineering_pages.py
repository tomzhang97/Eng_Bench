from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz

from tools import audit_pdf_engineering_pages as audit


class AuditPdfEngineeringPagesTests(unittest.TestCase):
    def make_pdf(self, path: Path) -> None:
        document = fitz.open()
        text_page = document.new_page()
        text_page.insert_text((72, 72), "Narrative-only engineering introduction")

        drawing_page = document.new_page()
        for index in range(25):
            y = 40 + index * 8
            drawing_page.draw_line((40, y), (300, y))

        figure_page = document.new_page()
        figure_page.insert_text((72, 72), "Figure 3. Typical valve installation")
        figure_page.draw_rect((72, 100, 250, 200))
        document.save(path)
        document.close()

    def test_selects_graphic_engineering_pages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "sample.pdf"
            self.make_pdf(pdf_path)
            report = audit.analyze_pdf(pdf_path)
            self.assertEqual(report["page_count"], 3)
            self.assertEqual(report["selected_page_numbers"], [2, 3])
            self.assertFalse(report["safe_to_merge_gold"])

    def test_cli_writes_report_and_contact_sheet(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "sample.pdf"
            report_path = root / "report.json"
            contacts = root / "contacts"
            self.make_pdf(pdf_path)
            exit_code = audit.main(
                [
                    "--root",
                    str(root),
                    "--pdf",
                    str(pdf_path),
                    "--output-json",
                    str(report_path),
                    "--contact-sheet-dir",
                    str(contacts),
                ]
            )
            self.assertEqual(exit_code, 0)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["valid"])
            self.assertEqual(len(payload["contact_sheets"]), 1)
            self.assertTrue((contacts / "contact_sheet_01.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
