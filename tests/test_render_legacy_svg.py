import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LEGACY_SVG = b'''<?xml version="1.0" encoding="iso-8859-1"?>
<svg xmlns="http://www.w3.org/2000/svg" width="25.4mm" height="12.7mm"
 viewBox="0 0 25400 12700">
 <defs>
  <font id="FontID0"><font-face font-family="Arial"/><glyph unicode="A"/></font>
  <style type="text/css"><![CDATA[
   @font-face { font-family:"Arial";src:url("#FontID0") format(svg) }
   .fil6 {fill:#1F1A17}
   .fnt0 {font-weight:normal;font-size:2500;font-family:Arial}
  ]]></style>
 </defs>
 <text x="2000" y="5000" class="fil6 fnt0">AT</text>
 <text x="14000" y="9000" class="fil6 fnt0">K\xfcW</text>
</svg>'''


class RenderLegacySvgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(
            "render_legacy_svg_test_module",
            ROOT / "tools" / "render_legacy_svg.py",
        )

    def test_sanitize_inlines_text_styles_and_removes_legacy_font(self):
        sanitized, report = self.mod.sanitize_svg(
            LEGACY_SVG,
            pixel_width=300,
            pixel_height=150,
        )
        text = sanitized.decode("utf-8")

        self.assertNotIn("@font-face", text)
        self.assertNotIn("<font ", text)
        self.assertNotIn('class="fil6 fnt0"', text)
        self.assertIn('font-size="2500"', text)
        self.assertIn('font-family="Arial"', text)
        self.assertIn('fill="#1F1A17"', text)
        self.assertIn("K\u00fcW", text)
        self.assertIn('width="300px"', text)
        self.assertEqual(report["text_nodes"], 2)
        self.assertEqual(report["text_nodes_with_inlined_styles"], 2)
        self.assertGreaterEqual(report["legacy_font_nodes_removed"], 1)

    def test_target_pixel_size_honors_physical_dimensions(self):
        self.assertEqual(self.mod.target_pixel_size(LEGACY_SVG, 300), (300, 150))

    def test_extract_text_rows_emits_nondegenerate_rendered_boxes(self):
        sanitized, _ = self.mod.sanitize_svg(
            LEGACY_SVG,
            pixel_width=300,
            pixel_height=150,
        )
        rows = self.mod.extract_text_rows(sanitized, 300, 150, page_index=4)

        self.assertEqual([row["text"] for row in rows], ["AT", "K\u00fcW"])
        self.assertTrue(all(row["page"] == 4 for row in rows))
        self.assertTrue(all(row["bbox_px"][2] > row["bbox_px"][0] for row in rows))
        self.assertTrue(all(row["bbox_px"][3] > row["bbox_px"][1] for row in rows))
        self.assertTrue(all(row["bbox_source"].startswith("legacy_svg") for row in rows))


if __name__ == "__main__":
    unittest.main()
