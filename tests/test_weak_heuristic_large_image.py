import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "weak_heuristic_baselines_large_image_test",
    ROOT / "baselines" / "weak_heuristic_baselines.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class WeakHeuristicLargeImageTests(unittest.TestCase):
    def test_trusted_local_image_size_ignores_and_restores_pillow_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "large.png"
            Image.new("RGB", (100, 100), "white").save(path)
            previous = Image.MAX_IMAGE_PIXELS
            try:
                Image.MAX_IMAGE_PIXELS = 100
                self.assertEqual(
                    MODULE.first_image_size(root, {"images": ["large.png"]}),
                    (100, 100),
                )
                self.assertEqual(Image.MAX_IMAGE_PIXELS, 100)
            finally:
                Image.MAX_IMAGE_PIXELS = previous


if __name__ == "__main__":
    unittest.main()
