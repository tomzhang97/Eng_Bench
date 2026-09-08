import unittest
import tempfile
import zipfile
from pathlib import Path

from tools import process_partial_auditor_returns as processor


class ProcessPartialAuditorReturnsTests(unittest.TestCase):
    def test_reviewer_number_allows_round_suffixes(self):
        self.assertEqual(
            processor.reviewer_number(Path("AUDITOR_11_ROUND1_REVIEW_12.xlsx")),
            11,
        )

    def test_normalize_decision_is_task_specific(self):
        self.assertEqual(processor.normalize_decision("microtext", "2"), "issue")
        self.assertEqual(
            processor.normalize_decision("visualdiff", "2"),
            "no_engineering_change",
        )

    def test_normalize_decision_rejects_unknown_code(self):
        with self.assertRaises(ValueError):
            processor.normalize_decision("microtext", "4")

    def test_display_index_normalizes_excel_numeric_serialization(self):
        self.assertEqual(processor.normalize_display_index("1.0"), "1")
        self.assertEqual(processor.normalize_display_index(12.0), "12")
        self.assertEqual(processor.normalize_display_index("row-1"), "row-1")

    def test_decision_code_normalizes_excel_numeric_serialization(self):
        self.assertEqual(processor.normalize_decision_code("1.0"), "1")
        self.assertEqual(processor.normalize_decision_code(2.0), "2")
        self.assertEqual(processor.normalize_decision_code(""), "")

    def test_machine_suggestion_normalizes_only_surrounding_whitespace(self):
        self.assertEqual(
            processor.normalize_machine_suggestion("\r\n类别：unknown_microtext\r\n"),
            "类别：unknown_microtext",
        )

    def test_return_sheet_state_allows_hidden_or_very_hidden_machine_sheet(self):
        path = Path("AUDITOR_10_ROUND2_REVIEW_12.xlsx")
        for machine_state in ("hidden", "veryHidden"):
            with self.subTest(machine_state=machine_state):
                self.assertEqual(
                    processor.validate_return_sheet_states(
                        path,
                        {
                            processor.delivery.AUDIT_SHEET: "visible",
                            processor.delivery.MACHINE_SHEET: machine_state,
                        },
                    ),
                    machine_state,
                )

    def test_return_sheet_state_rejects_visible_machine_sheet(self):
        with self.assertRaisesRegex(ValueError, "must remain hidden"):
            processor.validate_return_sheet_states(
                Path("AUDITOR_06_ROUND2_REVIEW_12.xlsx"),
                {
                    processor.delivery.AUDIT_SHEET: "visible",
                    processor.delivery.MACHINE_SHEET: "visible",
                },
            )

    def test_return_sheet_state_accepts_payload_defined_24_row_sheet(self):
        self.assertEqual(
            processor.validate_return_sheet_states(
                Path("AUDITOR_01_CURRENT_REVIEW_24.xlsx"),
                {"审核24条": "visible", "机器数据_勿改": "veryHidden"},
                "审核24条",
                "机器数据_勿改",
            ),
            "veryHidden",
        )

    def test_zip_input_is_safely_flattened(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "returns.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("nested/AUDITOR_01_CURRENT_REVIEW_24.xlsx", b"xlsx-one")
                archive.writestr("__MACOSX/._ignored.xlsx", b"ignored")
                archive.writestr("nested/~$AUDITOR_02_CURRENT_REVIEW_24.xlsx", b"lock")

            workbooks, sources = processor.expand_return_inputs(
                [archive_path], root / "temporary"
            )

            self.assertEqual(["AUDITOR_01_CURRENT_REVIEW_24.xlsx"], [p.name for p in workbooks])
            self.assertEqual(b"xlsx-one", workbooks[0].read_bytes())
            self.assertEqual("zip", sources[0]["kind"])
            self.assertEqual(1, sources[0]["workbooks"])

    def test_zip_input_rejects_duplicate_workbook_basenames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "returns.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("first/AUDITOR_01_CURRENT_REVIEW_24.xlsx", b"one")
                archive.writestr("second/AUDITOR_01_CURRENT_REVIEW_24.xlsx", b"two")

            with self.assertRaisesRegex(ValueError, "duplicate workbook basenames"):
                processor.expand_return_inputs([archive_path], root / "temporary")


if __name__ == "__main__":
    unittest.main()
