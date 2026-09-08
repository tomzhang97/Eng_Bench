from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import build_simple_multi_reviewer_package as build
from tools import verify_simple_multi_reviewer_handoff as verify


class SimpleMultiReviewerHandoffTest(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / "source"
        (source / "review_packs").mkdir(parents=True)
        (source / "review_packs" / "evidence.txt").write_text(
            "evidence", encoding="utf-8"
        )
        for name in build.ROOT_FILES:
            (source / name).write_text("placeholder\n", encoding="utf-8")

        control = source / "03_MACHINE_CONTROL"
        control.mkdir()
        for name in build.CONTROL_FILES:
            (control / name).write_text("placeholder\n", encoding="utf-8")

        primary = source / "01_PRIMARY_REVIEWER"
        primary.mkdir()
        for task in ("MICROTEXT", "VISUALDIFF"):
            (primary / f"PRIMARY_{task}_SOURCE.csv").write_text(
                "record_id\nexample\n", encoding="utf-8"
            )

        auditors = source / "02_INDEPENDENT_AUDITORS"
        for number in range(1, 11):
            folder = auditors / f"auditor_{number:02d}"
            folder.mkdir(parents=True)
            for task in ("MICROTEXT", "VISUALDIFF"):
                (folder / f"AUDITOR_{number:02d}_{task}_SOURCE.csv").write_text(
                    "record_id\nexample\n", encoding="utf-8"
                )
        return source

    def test_build_moves_controls_out_of_reviewer_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            output = root / "simple"

            report = build.build_package(source, output)

            self.assertTrue(report["valid"])
            self.assertEqual(report["primary_rows"], 498)
            self.assertTrue((output / "00_START_HERE_ZH.txt").exists())
            self.assertTrue((output / "01_PRIMARY_REVIEWER" / "OPEN_THIS_WORKBOOK_ZH.txt").exists())
            self.assertFalse(list((output / "01_PRIMARY_REVIEWER").rglob("*.csv")))
            self.assertFalse(list((output / "02_INDEPENDENT_AUDITORS").rglob("*.csv")))
            self.assertTrue(
                (
                    output
                    / "03_MACHINE_CONTROL"
                    / "source_tables"
                    / "auditor_10"
                    / "AUDITOR_10_VISUALDIFF_SOURCE.csv"
                ).exists()
            )
            instructions = (output / "00_START_HERE_ZH.txt").read_text(encoding="utf-8")
            self.assertIn("PRIMARY_REVIEW_498.xlsx", instructions)
            self.assertIn("只填写黄色列", instructions)
            self.assertIn("独立工作", instructions)

    def test_output_cannot_be_nested_in_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            with self.assertRaises(ValueError):
                build.safe_output(source, source / "nested")

    def test_zip_verifier_rejects_nested_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("handoff/README.txt", "review")
                archive.writestr("handoff/old_packet.zip", b"not another package")

            report = verify.verify_zip(path)

            self.assertFalse(report["valid"])
            self.assertTrue(any("nested ZIP entries" in issue for issue in report["issues"]))

    def test_zip_path_safety(self) -> None:
        self.assertTrue(verify.unsafe_zip_name("../escape.txt"))
        self.assertFalse(verify.unsafe_zip_name("handoff/review.xlsx"))

    def test_visualdiff_status_contract_has_no_unusable_valid_option(self) -> None:
        self.assertEqual(
            verify.VISUAL_STATUS_VALUES,
            {"edit", "reject_unclear", "needs_full_page"},
        )


if __name__ == "__main__":
    unittest.main()
