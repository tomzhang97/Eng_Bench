from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageStat

from tools.render_stl import load_triangles, mesh_stats, render_stl


class RenderStlTests(unittest.TestCase):
    def test_loads_ascii_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "triangle.stl"
            path.write_text(
                """solid sample
facet normal 0 0 1
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 1 0
  endloop
endfacet
endsolid sample
""",
                encoding="ascii",
            )
            triangles = load_triangles(path)
            self.assertEqual((1, 3, 3), triangles.shape)
            self.assertEqual([1.0, 1.0, 0.0], mesh_stats(triangles)["extent"])

    def test_loads_binary_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "triangle.stl"
            payload = bytearray(b"binary".ljust(80, b"\0"))
            payload.extend(struct.pack("<I", 1))
            payload.extend(
                struct.pack(
                    "<12fH",
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                )
            )
            path.write_bytes(payload)
            self.assertEqual((1, 3, 3), load_triangles(path).shape)

    def test_renders_non_blank_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "tetrahedron.stl"
            output = root / "tetrahedron.png"
            source.write_text(
                """solid tetrahedron
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 0 1 0
vertex 1 0 0
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 0 1
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 0 0
vertex 0 0 1
vertex 0 1 0
endloop
endfacet
facet normal 1 1 1
outer loop
vertex 1 0 0
vertex 0 1 0
vertex 0 0 1
endloop
endfacet
endsolid tetrahedron
""",
                encoding="ascii",
            )
            report = render_stl(source, output, width=400, height=400)
            self.assertEqual(4, report["triangles"])
            with Image.open(output) as image:
                self.assertEqual((400, 400), image.size)
                self.assertGreater(ImageStat.Stat(image.convert("L")).stddev[0], 5)


if __name__ == "__main__":
    unittest.main()
