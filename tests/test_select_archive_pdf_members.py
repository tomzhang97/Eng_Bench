from __future__ import annotations

import unittest

from tools import select_archive_pdf_members as selector


class SelectArchivePdfMembersTests(unittest.TestCase):
    def test_selects_numbered_drawings_and_holds_context_documents(self) -> None:
        rows = [
            {
                "member_path": "ND-100 Above Ground Enclosed Storage.pdf",
                "member_suffix": ".pdf",
                "selected_for_next_step": "true",
            },
            {
                "member_path": "ND-OM-516 - Livestock Pipeline.pdf",
                "member_suffix": ".pdf",
                "selected_for_next_step": "true",
            },
            {
                "member_path": "unsafe.pdf",
                "member_suffix": ".pdf",
                "selected_for_next_step": "false",
            },
        ]

        selected, reasons = selector.select_rows(
            rows,
            include_patterns=[r"(?i)^ND-\d{3}\b.*\.pdf$"],
            exclude_patterns=[],
        )

        self.assertEqual([row["selected_for_next_step"] for row in selected], ["true", "false", "false"])
        self.assertEqual(reasons["selected_drawing_pdf"], 1)
        self.assertEqual(reasons["include_pattern_miss"], 1)
        self.assertEqual(reasons["not_selected_by_archive_inventory"], 1)


if __name__ == "__main__":
    unittest.main()
