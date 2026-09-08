import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="25.4mm" height="12.7mm"
viewBox="0 0 25.4 12.7">
<rect width="25.4" height="12.7" fill="white"/>
<text x="5" y="6" font-size="2.5">GPIO12</text>
</svg>
"""


class ExtractSvgTextlayerTests(unittest.TestCase):
    def test_extract_rows_uses_rendered_text_geometry_and_page_index(self):
        mod = load_module(
            "extract_svg_textlayer_geometry",
            ROOT / "tools" / "extract_svg_textlayer.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            svg = root / "page_003.svg"
            image = root / "page_003.png"
            svg.write_text(SVG, encoding="utf-8")
            Image.new("RGB", (300, 150), "white").save(image)

            rows = mod.extract_rows(svg, image, page_index=3)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["page"], 3)
            self.assertEqual(rows[0]["text"], "GPIO12")
            x0, y0, x1, y1 = rows[0]["bbox_px"]
            self.assertGreater(x0, 0)
            self.assertGreater(y0, 0)
            self.assertLessEqual(x1, 300)
            self.assertLessEqual(y1, 150)
            self.assertGreater(x1, x0)
            self.assertGreater(y1, y0)
            self.assertEqual(rows[0]["image_width_px"], 300)
            self.assertEqual(rows[0]["image_height_px"], 150)
            self.assertEqual(rows[0]["bbox_coordinate_space"], "rendered_image_px")

    def test_extract_directory_rows_matches_svg_pages_to_rendered_images(self):
        mod = load_module(
            "extract_svg_textlayer_directory",
            ROOT / "tools" / "extract_svg_textlayer.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            svg_dir = root / "svg"
            image_dir = root / "images"
            svg_dir.mkdir()
            image_dir.mkdir()
            for page_index in (0, 2):
                stem = f"page_{page_index:03d}"
                (svg_dir / f"{stem}.svg").write_text(
                    SVG.replace("GPIO12", f"GPIO{page_index}"),
                    encoding="utf-8",
                )
                Image.new("RGB", (300, 150), "white").save(image_dir / f"{stem}.png")

            rows = mod.extract_directory_rows(svg_dir, image_dir)

            self.assertEqual([row["page"] for row in rows], [0, 2])
            self.assertEqual([row["text"] for row in rows], ["GPIO0", "GPIO2"])


if __name__ == "__main__":
    unittest.main()
