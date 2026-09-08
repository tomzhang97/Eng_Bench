from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import export_review_packs


class ExportReviewPacksLargeRasterTests(unittest.TestCase):
    def test_opt_in_pixel_ceiling_is_restored_after_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "sheet.png"
            Image.new("RGB", (64, 32), "white").save(image_path)
            default_ceiling = Image.MAX_IMAGE_PIXELS

            crop = export_review_packs.padded_crop(
                image_path,
                (8, 4, 32, 20),
                pad_px=2,
                max_image_pixels=300_000_000,
            )

            self.assertIsNotNone(crop)
            self.assertEqual(crop.size, (28, 20))
            self.assertEqual(Image.MAX_IMAGE_PIXELS, default_ceiling)


if __name__ == "__main__":
    unittest.main()
