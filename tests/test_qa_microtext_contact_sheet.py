import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "qa_microtext_contact_sheet.py"
SPEC = importlib.util.spec_from_file_location("qa_microtext_contact_sheet", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class QaMicrotextContactSheetTests(unittest.TestCase):
    def test_authoritative_bbox_ignores_padded_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (100, 80), "black").save(root / "page.png")
            Image.new("RGB", (70, 60), "white").save(root / "cached.png")
            row = {"crop_path": "cached.png", "image_path": "page.png", "bbox": [10, 10, 30, 25]}
            self.assertEqual((70, 60), MODULE.review_crop(root, row).size)
            crop = MODULE.review_crop(root, row, authoritative_bbox=True)
            self.assertEqual((20, 15), crop.size)
            self.assertEqual((0, 0, 0), crop.getpixel((0, 0)))
            self.assertEqual("cached.png", row["crop_path"])
            row["image_path"] = "missing.png"
            self.assertIsNone(MODULE.review_crop(root, row, authoritative_bbox=True))

    def test_renders_bbox_directly_from_source_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "page.png"
            Image.new("RGB", (100, 80), "white").save(source)
            output = root / "contact.png"
            rows = [
                {
                    "candidate_id": "candidate-a",
                    "doc_id": "doc-a",
                    "category": "equipment_tag",
                    "proposed_text": "P-101",
                    "image_path": "page.png",
                    "bbox": [10, 10, 50, 40],
                }
            ]

            stats = MODULE.render_contact_sheet(
                root,
                rows,
                output,
                start=0,
                limit=30,
                columns=5,
                cell_width=240,
                cell_height=160,
            )

            self.assertEqual(stats["rows"], 1)
            self.assertEqual(stats["missing_crops"], 0)
            self.assertTrue(output.is_file())

    def test_missing_source_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "contact.png"
            stats = MODULE.render_contact_sheet(
                root,
                [{"candidate_id": "candidate-a", "bbox": [1, 1, 2, 2]}],
                output,
                start=0,
                limit=30,
                columns=5,
                cell_width=240,
                cell_height=160,
            )

            self.assertEqual(stats["missing_crops"], 1)

    def test_opt_in_large_image_limit_is_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "page.png"
            Image.new("RGB", (100, 80), "white").save(source)
            prior_limit = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = 10
            try:
                crop = MODULE.review_crop(
                    root,
                    {"image_path": "page.png", "bbox": [10, 10, 50, 40]},
                    max_image_pixels=10_000,
                )
                self.assertIsNotNone(crop)
                self.assertEqual(crop.size, (40, 30))
                self.assertEqual(Image.MAX_IMAGE_PIXELS, 10)
            finally:
                Image.MAX_IMAGE_PIXELS = prior_limit

    def test_renders_crop_with_red_box_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "page.png"
            Image.new("RGB", (120, 100), "white").save(source)
            output = root / "context.png"
            rows = [
                {
                    "candidate_id": "candidate-a",
                    "doc_id": "doc-a",
                    "category": "dimension_value",
                    "proposed_text": '3"',
                    "image_path": "page.png",
                    "bbox": [45, 35, 65, 55],
                }
            ]

            context = MODULE.review_context(root, rows[0], 20)
            self.assertIsNotNone(context)
            self.assertEqual(context.size, (60, 60))
            self.assertEqual(context.getpixel((20, 20)), (208, 0, 0))

            stats = MODULE.render_contact_sheet(
                root,
                rows,
                output,
                start=0,
                limit=30,
                columns=1,
                cell_width=360,
                cell_height=220,
                context_padding=20,
            )

            self.assertEqual(stats["rows"], 1)
            self.assertEqual(stats["missing_crops"], 0)
            self.assertEqual(stats["missing_contexts"], 0)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
