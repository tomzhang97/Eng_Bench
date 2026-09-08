from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.compact_primary_workbook_evidence import MAX_HEIGHT, MAX_WIDTH, compact_png


class CompactPrimaryEvidenceTests(unittest.TestCase):
    def test_compact_png_preserves_aspect_and_limits_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            output = root / "output.png"
            Image.new("RGB", (1_100, 360), "white").save(source)
            original_width, original_height, width, height = compact_png(source, output)
            self.assertEqual((original_width, original_height), (1_100, 360))
            self.assertLessEqual(width, MAX_WIDTH)
            self.assertLessEqual(height, MAX_HEIGHT)
            self.assertTrue(output.is_file())
            with Image.open(output) as compact:
                self.assertEqual(compact.size, (width, height))


if __name__ == "__main__":
    unittest.main()
