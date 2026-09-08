import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from tools.build_active_audit_evidence import extract_region
from tools import build_active_audit_evidence as module


class ActiveAuditEvidenceTests(unittest.TestCase):
    def test_default_limit_covers_largest_current_archival_scan(self):
        self.assertGreaterEqual(module.DEFAULT_MAX_IMAGE_PIXELS, 282_401_280)

    def test_exact_crop_unmodified_and_context_bounded(self):
        image = Image.new("RGB", (20, 20), "white")
        image.putpixel((2, 3), (0, 0, 0))
        native, context, bbox = extract_region(image, [1, 2, 7, 8], 100)
        self.assertEqual((6, 6), native.size)
        self.assertEqual((0, 0, 0), native.getpixel((1, 1)))
        self.assertEqual([0, 0, 20, 20], bbox)
        self.assertEqual((255, 255, 255), native.getpixel((0, 0)))
        self.assertEqual((255, 0, 0), context.getpixel((1, 2)))

    def test_invalid_bbox_is_not_silently_clamped(self):
        image = Image.new("RGB", (20, 20))
        for bbox in ([-1, 0, 4, 4], [0, 0, 30, 4], [3, 3, 3, 4], [0, 0, 4.1, 4]):
            with self.assertRaises(ValueError):
                extract_region(image, bbox, 10)

    def test_atlases_include_more_than_42_microtext_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "derived/quality/source/report.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("{}", encoding="utf-8")
            Image.new("RGB", (8, 8), "white").save(root / "page.png")
            holds, questions = [], []
            for number in range(43):
                identity = f"i{number}"
                annotation = {"item_id": identity, "split": "test", "bbox": [1, 1, 5, 5], "text_gt": "A"}
                holds.append({"active_gold_identity": identity, "active_row": annotation, "task_type": "microtext"})
                questions.append({"id": f"q{number}", "metadata": {"item_id": identity}, "images": ["page.png"],
                                  "evidence": [{"image_index": 0, "bbox": annotation["bbox"]}]})
            def read(path):
                return questions if path.name == "eng_bench.jsonl" else holds
            with patch.object(module, "release_constraint", return_value={"issues": [], "report_path": "derived/quality/source/report.json"}), \
                 patch.object(module.preview, "active_hashes", return_value={}), \
                 patch.object(module.preview, "read_jsonl", side_effect=read):
                report = module.build(root, root / "derived/quality/output", max_image_pixels=1000000, padding=1)
            self.assertEqual(43, report["rows"])
            self.assertIn("microtext_atlas_37_43.png", report["atlases"])
            self.assertTrue((root / "derived/quality/output/43_current_native.png").is_file())


if __name__ == "__main__":
    unittest.main()
