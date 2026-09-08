from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf
from PIL import Image

from tools.render_dwg import read_dwg_version, render_dxf_to_png, sha256_file


class RenderDwgTest(unittest.TestCase):
    def test_read_dwg_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.dwg"
            path.write_bytes(b"AC1018" + b"\x00" * 32)
            self.assertEqual(read_dwg_version(path), ("AC1018", "R2004"))

    def test_invalid_dwg_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.dwg"
            path.write_bytes(b"NOTDWG" + b"\x00" * 32)
            with self.assertRaisesRegex(ValueError, "invalid DWG header"):
                read_dwg_version(path)

    def test_render_dxf_to_png_is_nonblank_and_fixed_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dxf_path = tmp_path / "sample.dxf"
            png_path = tmp_path / "sample.png"
            doc = ezdxf.new("R2004")
            modelspace = doc.modelspace()
            modelspace.add_line((0, 0), (10, 10))
            modelspace.add_circle((5, 5), radius=2)
            modelspace.add_text("FT-101", dxfattribs={"height": 0.5})
            doc.saveas(dxf_path)

            result = render_dxf_to_png(
                dxf_path,
                png_path,
                dpi=100,
                paper_width_inches=4,
                paper_height_inches=3,
            )

            self.assertEqual((result["output_width"], result["output_height"]), (400, 300))
            self.assertGreater(result["nonwhite_pixels_below_250"], 0)
            self.assertEqual(result["modelspace_entity_counts"]["LINE"], 1)
            with Image.open(png_path) as image:
                self.assertEqual(image.size, (400, 300))
            self.assertEqual(len(sha256_file(png_path)), 64)


if __name__ == "__main__":
    unittest.main()
