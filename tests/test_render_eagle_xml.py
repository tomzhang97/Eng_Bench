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


class RenderEagleXmlTests(unittest.TestCase):
    def test_sheet_count_reads_schematic_and_board(self):
        mod = load_module("render_eagle_xml", ROOT / "tools" / "render_eagle_xml.py")

        schematic = """<?xml version="1.0"?>
<eagle><drawing><schematic><sheets><sheet/><sheet/></sheets></schematic></drawing></eagle>
"""
        board = """<?xml version="1.0"?>
<eagle><drawing><board/></drawing></eagle>
"""

        self.assertEqual(mod.sheet_count_from_xml(schematic), 2)
        self.assertEqual(mod.sheet_count_from_xml(board), 1)

    def test_svg_to_png_renders_at_requested_dpi(self):
        mod = load_module("render_eagle_xml_png", ROOT / "tools" / "render_eagle_xml.py")

        svg = """<svg xmlns="http://www.w3.org/2000/svg" width="25.4mm" height="12.7mm"
viewBox="0 0 25.4 12.7"><rect width="25.4" height="12.7" fill="white"/></svg>"""

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "page_000.png"
            width, height = mod.svg_to_png(svg, output, dpi=300, grayscale=True)

            self.assertTrue(output.exists())
            self.assertEqual((width, height), (300, 150))
            with Image.open(output) as image:
                self.assertEqual(image.mode, "L")

    def test_stabilize_svg_removes_extraction_timestamp(self):
        mod = load_module("render_eagle_xml_stable", ROOT / "tools" / "render_eagle_xml.py")

        svg = "<svg><text>2026-06-05 08:30:00</text><text>PX4FMUv2.4.6</text></svg>"
        stable = mod.stabilize_svg(svg, extraction_timestamp="2026-06-05 08:30:00")

        self.assertNotIn("2026-06-05 08:30:00", stable)
        self.assertIn("PX4FMUv2.4.6", stable)

    def test_stabilize_svg_escapes_raw_eagle_text(self):
        mod = load_module("render_eagle_xml_escape", ROOT / "tools" / "render_eagle_xml.py")

        svg = '<svg><text text-anchor="start">PD0/OSC<= & test</text></svg>'
        stable = mod.stabilize_svg(svg)

        self.assertIn("PD0/OSC&lt;= &amp; test", stable)

    def test_stabilize_svg_preserves_multiline_tspan_markup(self):
        mod = load_module("render_eagle_xml_tspan", ROOT / "tools" / "render_eagle_xml.py")

        svg = (
            '<svg><text text-anchor="middle">'
            '<tspan x="0" y="-1">VSOP383 & test</tspan>'
            '<tspan x="0" y="0">A<=B</tspan>'
            '</text></svg>'
        )
        stable = mod.stabilize_svg(svg)

        self.assertIn('<tspan x="0" y="-1">VSOP383 &amp; test</tspan>', stable)
        self.assertIn('<tspan x="0" y="0">A&lt;=B</tspan>', stable)
        self.assertNotIn("&lt;tspan", stable)

    def test_sanitize_eagle_xml_removes_only_empty_text_nodes(self):
        mod = load_module("render_eagle_xml_sanitize", ROOT / "tools" / "render_eagle_xml.py")

        xml = (
            '<eagle><text x="1">VISIBLE</text>'
            '<text x="2"></text><text x="3">  \n </text></eagle>'
        )
        sanitized, removed = mod.sanitize_eagle_xml_for_renderer(xml)

        self.assertEqual(removed, 2)
        self.assertIn('<text x="1">VISIBLE</text>', sanitized)
        self.assertNotIn('<text x="2">', sanitized)
        self.assertNotIn('<text x="3">', sanitized)

    def test_utf8_eagle_opener_is_independent_of_windows_code_page(self):
        mod = load_module("render_eagle_xml_utf8", ROOT / "tools" / "render_eagle_xml.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "utf8.sch"
            source.write_text("smart quote: \u201d\n", encoding="utf-8")
            with mod.open_eagle_xml(str(source)) as handle:
                self.assertEqual(handle.read(), "smart quote: \u201d\n")


if __name__ == "__main__":
    unittest.main()
