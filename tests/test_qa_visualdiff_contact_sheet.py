from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import qa_visualdiff_contact_sheet


class VisualDiffContactSheetTest(unittest.TestCase):
    def test_creates_missing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.png"
            new_path = root / "new.png"
            Image.new("RGB", (100, 100), "white").save(old_path)
            Image.new("RGB", (100, 100), "black").save(new_path)
            input_path = root / "review.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "pair_id": "vdiff__example__v1__to__v2__p0000__000",
                        "image_old": str(old_path),
                        "image_new": str(new_path),
                        "bbox_old": [20, 20, 40, 40],
                        "bbox_new": [20, 20, 40, 40],
                        "change_type": "text",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_path = root / "nested" / "contact.png"

            count = qa_visualdiff_contact_sheet.render_contact_sheet(
                input_path, output_path, row_count=1, pad=5, cell=80
            )

            self.assertEqual(count, 1)
            self.assertTrue(output_path.exists())
            with Image.open(output_path) as rendered:
                self.assertEqual(rendered.size, (190, 120))

    def test_reads_utf8_bom_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "review.jsonl"
            path.write_text(json.dumps({"pair_id": "example"}) + "\n", encoding="utf-8-sig")

            rows = qa_visualdiff_contact_sheet.read_rows(path)

            self.assertEqual(rows, [{"pair_id": "example"}])

    def test_renders_multiple_columns_without_changing_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.png"
            new_path = root / "new.png"
            Image.new("RGB", (100, 100), "white").save(old_path)
            Image.new("RGB", (100, 100), "black").save(new_path)
            row = {
                "pair_id": "vdiff__example__v1__to__v2__p0000__000",
                "image_old": str(old_path),
                "image_new": str(new_path),
                "bbox_old": [20, 20, 40, 40],
                "bbox_new": [20, 20, 40, 40],
                "change_type": "text",
            }
            input_path = root / "review.jsonl"
            input_path.write_text(
                "".join(json.dumps({**row, "pair_id": f"pair_{index}"}) + "\n" for index in range(5)),
                encoding="utf-8",
            )
            output_path = root / "contact.png"

            count = qa_visualdiff_contact_sheet.render_contact_sheet(
                input_path,
                output_path,
                row_count=5,
                pad=5,
                cell=80,
                columns=3,
            )

            self.assertEqual(count, 5)
            with Image.open(output_path) as rendered:
                self.assertEqual(rendered.size, (570, 240))


if __name__ == "__main__":
    unittest.main()
