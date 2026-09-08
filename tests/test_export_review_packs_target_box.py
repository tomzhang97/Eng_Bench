import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import export_review_packs


class ExportReviewPacksTargetBoxTest(unittest.TestCase):
    def test_padded_crop_can_draw_exact_target_box(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            page = Path(temp_dir) / "page.png"
            Image.new("RGB", (100, 100), color="white").save(page)

            crop = export_review_packs.padded_crop(
                page,
                (20, 30, 40, 50),
                pad_px=10,
                draw_target_box=True,
            )

            self.assertIsNotNone(crop)
            assert crop is not None
            self.assertEqual(crop.size, (40, 40))
            self.assertEqual(crop.getpixel((10, 10)), (220, 38, 38))
            self.assertEqual(crop.getpixel((20, 20)), (255, 255, 255))

    def test_microtext_export_records_target_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            page = root / "pages" / "doc" / "page.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), color="white").save(page)
            rows = [
                {
                    "candidate_id": "target_1",
                    "doc_id": "doc",
                    "page_index": 0,
                    "bbox": [20, 30, 40, 50],
                    "image_path": "pages/doc/page.png",
                    "category": "instrument_tag",
                }
            ]

            stats = export_review_packs.export_microtext_pack(
                root,
                rows,
                Path("pack"),
                pad_px=10,
                draw_target_box=True,
            )
            manifest = export_review_packs.load_jsonl(root / "pack" / "manifest.jsonl")

            self.assertEqual(stats["target_boxes"], 1)
            self.assertIs(manifest[0]["target_box_drawn"], True)
            with Image.open(root / manifest[0]["crop_path"]) as crop:
                self.assertEqual(crop.getpixel((10, 10)), (220, 38, 38))


if __name__ == "__main__":
    unittest.main()
