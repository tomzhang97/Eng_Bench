from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import rebuild_review_batch_zip


class RebuildReviewBatchZipTest(unittest.TestCase):
    def test_builds_single_root_crc_valid_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "review_batch"
            (source / "nested").mkdir(parents=True)
            (source / "README.md").write_text("review\n", encoding="utf-8")
            (source / "nested" / "row.csv").write_text("id,status\n1,\n", encoding="utf-8")
            output = root / "review_batch.zip"

            report = rebuild_review_batch_zip.rebuild_zip(source, output)

            self.assertTrue(report["valid"])
            self.assertEqual(report["entries"], 2)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    sorted(archive.namelist()),
                    ["review_batch/README.md", "review_batch/nested/row.csv"],
                )

    def test_builds_flat_root_crc_valid_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "review_batch"
            (source / "nested").mkdir(parents=True)
            (source / "README.md").write_text("review\n", encoding="utf-8")
            (source / "nested" / "row.csv").write_text(
                "id,status\n1,\n", encoding="utf-8"
            )
            output = root / "review_batch_flat.zip"

            report = rebuild_review_batch_zip.rebuild_zip(
                source, output, flat_root=True
            )

            self.assertTrue(report["valid"])
            self.assertTrue(report["flat_root"])
            self.assertEqual(report["archive_layout"], "flat")
            self.assertIsNone(report["single_root"])
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    sorted(archive.namelist()),
                    ["README.md", "nested/row.csv"],
                )


if __name__ == "__main__":
    unittest.main()
