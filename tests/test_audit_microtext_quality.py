from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from tools import audit_microtext_quality as quality


class AuditMicrotextQualityTest(unittest.TestCase):
    def test_png_dimensions_reads_large_ihdr_without_pixel_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.png"
            path.write_bytes(
                quality.PNG_SIGNATURE
                + struct.pack(">I", 13)
                + b"IHDR"
                + struct.pack(">II", 21630, 13056)
            )

            self.assertEqual(quality.png_dimensions(path), (21630, 13056))

    def test_png_dimensions_rejects_invalid_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.png"
            path.write_bytes(b"not a png")

            with self.assertRaisesRegex(ValueError, "invalid PNG"):
                quality.png_dimensions(path)


if __name__ == "__main__":
    unittest.main()
