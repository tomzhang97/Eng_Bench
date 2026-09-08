import importlib.util
import json
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


def write_page(root: Path, dpi_dir: str, doc_id: str, page: int, size: tuple[int, int]) -> None:
    path = root / "derived" / dpi_dir / doc_id / f"page_{page:03d}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)


class RepairReviewQueueImagePathsTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(
            "repair_review_queue_image_paths",
            ROOT / "tools" / "repair_review_queue_image_paths.py",
        )

    def test_repairs_missing_path_from_rendered_page_and_enriches_review_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "pages_300dpi", "doc_a", 0, (1000, 800))
            rows = [
                {
                    "candidate_id": "cand_a",
                    "doc_id": "doc_a",
                    "page_index": 0,
                    "bbox": [10, 20, 50, 60],
                    "category": "instrument_tag",
                    "target_text": "TIC-101",
                    "raw_text": "near TIC-101 label",
                    "review_status": "candidate",
                }
            ]

            repaired, stats = self.mod.repair_rows(root, rows, "unit")

            self.assertEqual(stats["output_rows"], 1)
            self.assertEqual(stats["repaired_image_path"], 1)
            self.assertEqual(repaired[0]["image_path"], "derived/pages_300dpi/doc_a/page_000.png")
            self.assertEqual(repaired[0]["proposed_text"], "TIC-101")
            self.assertEqual(repaired[0]["text_context"], "near TIC-101 label")
            self.assertEqual(repaired[0]["question_text"], "What instrument tag is shown in this region?")
            self.assertEqual(repaired[0]["review_status"], "needs_review")
            self.assertEqual(repaired[0]["image_path_repair_label"], "unit")

    def test_falls_back_to_200dpi_when_300dpi_page_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "pages_200dpi", "doc_b", 2, (500, 400))
            rows = [
                {
                    "candidate_id": "cand_b",
                    "doc_id": "doc_b",
                    "page_index": 2,
                    "bbox": [100, 100, 150, 130],
                    "category": "room_label",
                    "target_text": "ROOM 12",
                }
            ]

            repaired, stats = self.mod.repair_rows(root, rows, "unit")

            self.assertEqual(stats["output_rows"], 1)
            self.assertEqual(repaired[0]["image_path"], "derived/pages_200dpi/doc_b/page_002.png")
            self.assertEqual(repaired[0]["question_text"], "What room label is shown in this region?")

    def test_skips_rows_whose_bbox_does_not_fit_any_candidate_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "pages_300dpi", "doc_c", 0, (100, 100))
            rows = [
                {
                    "candidate_id": "cand_c",
                    "doc_id": "doc_c",
                    "page_index": 0,
                    "bbox": [90, 90, 150, 160],
                    "category": "pin_label",
                    "target_text": "A1",
                }
            ]

            repaired, stats = self.mod.repair_rows(root, rows, "unit")

            self.assertEqual(repaired, [])
            self.assertEqual(stats["bbox_out_of_frame"], 1)
            self.assertEqual(stats["unrepaired_rows"], 1)


if __name__ == "__main__":
    unittest.main()
