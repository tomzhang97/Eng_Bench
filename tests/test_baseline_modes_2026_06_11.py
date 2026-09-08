import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SsimDiffModeTests(unittest.TestCase):
    def test_ssim_mode_localizes_structural_change_with_distinct_metadata(self):
        mod = load_module("simple_diff_baseline_ssim", ROOT / "baselines" / "simple_diff_baseline.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "images").mkdir()
            old_image = Image.new("L", (400, 300), 255)
            new_image = Image.new("L", (400, 300), 255)
            draw = ImageDraw.Draw(new_image)
            draw.rectangle([200, 120, 260, 170], fill=0)
            old_image.save(root / "images" / "old.png")
            new_image.save(root / "images" / "new.png")

            row = {
                "id": "q1",
                "task": "visualdiff",
                "split": "test",
                "images": ["images/old.png", "images/new.png"],
            }
            prediction, error = mod.predict_row(root, row, mode="ssim")

            self.assertIsNone(error)
            self.assertEqual(prediction["metadata"]["model"], "ssim_diff")
            bbox = prediction["evidence"][0]["bbox"]
            self.assertLessEqual(bbox[0], 200)
            self.assertGreaterEqual(bbox[2], 260)
            self.assertLessEqual(bbox[1], 120)
            self.assertGreaterEqual(bbox[3], 170)
            self.assertLess(
                (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
                400 * 300 * 0.5,
                "ssim box should localize, not cover the page",
            )


class PhaseDiffModeTests(unittest.TestCase):
    def test_phase_mode_localizes_change_despite_global_translation(self):
        mod = load_module("simple_diff_baseline_phase", ROOT / "baselines" / "simple_diff_baseline.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "images").mkdir()
            old_image = Image.new("L", (400, 300), 255)
            draw_old = ImageDraw.Draw(old_image)
            for x in range(40, 360, 40):
                draw_old.line([(x, 20), (x, 280)], fill=0, width=2)
            # New revision: same drawing shifted right by 6px plus one real change.
            new_image = Image.new("L", (400, 300), 255)
            new_image.paste(old_image.crop((0, 0, 394, 300)), (6, 0))
            draw_new = ImageDraw.Draw(new_image)
            draw_new.rectangle([200, 140, 240, 170], fill=0)
            old_image.save(root / "images" / "old.png")
            new_image.save(root / "images" / "new.png")

            row = {
                "id": "q1",
                "task": "visualdiff",
                "split": "test",
                "images": ["images/old.png", "images/new.png"],
            }
            absolute_prediction, _ = mod.predict_row(root, row, mode="absolute")
            phase_prediction, error = mod.predict_row(root, row, mode="phase")

            self.assertIsNone(error)
            self.assertEqual(phase_prediction["metadata"]["model"], "phase_diff")

            def area(bbox):
                return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])

            phase_bbox = phase_prediction["evidence"][0]["bbox"]
            absolute_bbox = absolute_prediction["evidence"][0]["bbox"]
            self.assertLess(
                area(phase_bbox),
                area(absolute_bbox),
                "translation compensation should shrink the predicted change region",
            )
            self.assertLessEqual(phase_bbox[0], 210)
            self.assertGreaterEqual(phase_bbox[2], 230)


class NewDiffModeTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module("simple_diff_baseline_new_modes", ROOT / "baselines" / "simple_diff_baseline.py")

    def make_pair(self, root: Path) -> dict:
        (root / "images").mkdir(exist_ok=True)
        old_image = Image.new("L", (480, 360), 255)
        draw_old = ImageDraw.Draw(old_image)
        for x in range(30, 460, 60):
            draw_old.rectangle([x, 30, x + 18, 60], outline=0, width=2)
        new_image = old_image.copy()
        draw_new = ImageDraw.Draw(new_image)
        draw_new.rectangle([250, 200, 310, 250], fill=0)
        old_image.save(root / "images" / "old.png")
        new_image.save(root / "images" / "new.png")
        return {
            "id": "q1",
            "task": "visualdiff",
            "split": "test",
            "images": ["images/old.png", "images/new.png"],
        }

    def assert_box_covers_change(self, bbox: list[int]):
        self.assertLessEqual(bbox[0], 252)
        self.assertGreaterEqual(bbox[2], 308)
        self.assertLessEqual(bbox[1], 202)
        self.assertGreaterEqual(bbox[3], 248)

    def test_largest_cc_localizes_single_changed_component(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            row = self.make_pair(Path(temp_dir))
            prediction, error = self.mod.predict_row(Path(temp_dir), row, mode="largest_cc")
            self.assertIsNone(error)
            self.assertEqual(prediction["metadata"]["model"], "largest_cc_diff")
            self.assert_box_covers_change(prediction["evidence"][0]["bbox"])

    def test_tile_zncc_localizes_lowest_correlation_tile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            row = self.make_pair(Path(temp_dir))
            prediction, error = self.mod.predict_row(Path(temp_dir), row, mode="tile_zncc")
            self.assertIsNone(error)
            self.assertEqual(prediction["metadata"]["model"], "tile_zncc_diff")
            bbox = prediction["evidence"][0]["bbox"]
            self.assertLess(bbox[0], 310)
            self.assertGreater(bbox[2], 250)
            self.assertLess(bbox[1], 250)
            self.assertGreater(bbox[3], 200)

    def test_orb_residual_returns_localized_cluster_box(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            row = self.make_pair(Path(temp_dir))
            prediction, error = self.mod.predict_row(Path(temp_dir), row, mode="orb_residual")
            self.assertIsNone(error)
            self.assertEqual(prediction["metadata"]["model"], "orb_residual_diff")
            bbox = prediction["evidence"][0]["bbox"]
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            self.assertLess(area, 480 * 360 * 0.5, "cluster box should localize, not cover the page")


class CenterAndFrequencyModeTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(
            "textlayer_microtext_baseline_new_modes", ROOT / "baselines" / "textlayer_microtext_baseline.py"
        )

    def write_doc(self, root: Path, spans: list[dict]):
        textlayer = root / "derived" / "textlayer"
        textlayer.mkdir(parents=True, exist_ok=True)
        with (textlayer / "demo_doc.jsonl").open("w", encoding="utf-8") as f:
            for span in spans:
                f.write(json.dumps(span) + "\n")

    def row(self) -> dict:
        return {
            "id": "q1",
            "task": "microtext",
            "split": "test",
            "images": ["images/demo_doc__v1/page_0000.png"],
            "metadata": {"doc_id": "demo_doc", "category": "pin_label"},
        }

    def test_center_span_picks_span_nearest_page_centroid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_doc(
                root,
                [
                    {"page": 0, "text": "CORNER", "bbox_px": [0, 0, 60, 20]},
                    {"page": 0, "text": "MIDDLE", "bbox_px": [470, 290, 530, 310]},
                    {"page": 0, "text": "EDGE", "bbox_px": [940, 580, 1000, 600]},
                ],
            )
            prediction, error = self.mod.predict_row(root, self.row(), mode="center_span")
            self.assertIsNone(error)
            self.assertEqual(prediction["answer"], "MIDDLE")
            self.assertEqual(prediction["metadata"]["model"], "textlayer_center_span")

    def test_page_frequency_picks_most_repeated_text_first_instance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_doc(
                root,
                [
                    {"page": 0, "text": "GND", "bbox_px": [500, 400, 530, 412]},
                    {"page": 0, "text": "UNIQUE", "bbox_px": [10, 10, 70, 22]},
                    {"page": 0, "text": "GND", "bbox_px": [100, 50, 130, 62]},
                    {"page": 0, "text": "GND", "bbox_px": [300, 200, 330, 212]},
                ],
            )
            prediction, error = self.mod.predict_row(root, self.row(), mode="page_frequency")
            self.assertIsNone(error)
            self.assertEqual(prediction["answer"], "GND")
            self.assertEqual(prediction["evidence"][0]["bbox"], [100, 50, 130, 62])
            self.assertEqual(prediction["metadata"]["model"], "textlayer_page_frequency")


class SmallestSpanModeTests(unittest.TestCase):
    def test_smallest_span_mode_picks_tiniest_readable_span(self):
        mod = load_module(
            "textlayer_microtext_baseline_smallest", ROOT / "baselines" / "textlayer_microtext_baseline.py"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer = root / "derived" / "textlayer"
            textlayer.mkdir(parents=True)
            spans = [
                {"page": 0, "text": "BIG TITLE", "bbox_px": [10, 10, 410, 80]},
                {"page": 0, "text": "R47", "bbox_px": [100, 100, 124, 112]},
                {"page": 0, "text": "-", "bbox_px": [200, 200, 203, 203]},
            ]
            with (textlayer / "demo_doc.jsonl").open("w", encoding="utf-8") as f:
                for span in spans:
                    f.write(json.dumps(span) + "\n")

            row = {
                "id": "q1",
                "task": "microtext",
                "split": "test",
                "images": ["images/demo_doc__v1/page_0000.png"],
                "metadata": {"doc_id": "demo_doc", "category": "pin_label"},
            }
            prediction, error = mod.predict_row(root, row, mode="smallest_span")

            self.assertIsNone(error)
            self.assertEqual(prediction["answer"], "R47")
            self.assertEqual(prediction["metadata"]["model"], "textlayer_smallest_span")
            self.assertEqual(prediction["evidence"][0]["bbox"], [100, 100, 124, 112])

    def test_default_mode_still_reports_category_heuristic(self):
        mod = load_module(
            "textlayer_microtext_baseline_default", ROOT / "baselines" / "textlayer_microtext_baseline.py"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer = root / "derived" / "textlayer"
            textlayer.mkdir(parents=True)
            with (textlayer / "demo_doc.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({"page": 0, "text": "TP4", "bbox_px": [5, 5, 30, 15]}) + "\n")
            row = {
                "id": "q1",
                "task": "microtext",
                "split": "test",
                "images": ["images/demo_doc__v1/page_0000.png"],
                "metadata": {"doc_id": "demo_doc", "category": "pin_label"},
            }
            prediction, error = mod.predict_row(root, row)
            self.assertIsNone(error)
            self.assertEqual(prediction["metadata"]["model"], "textlayer_heuristic")


class TextlayerDiffVisualdiffBaselineTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(
            "textlayer_diff_visualdiff_baseline", ROOT / "baselines" / "textlayer_diff_visualdiff_baseline.py"
        )

    def write_layer(self, root: Path, doc_id: str, spans: list[dict]):
        path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for span in spans:
                f.write(json.dumps(span) + "\n")

    def test_changed_text_drives_answer_and_side_specific_boxes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shared = [
                {"page": 0, "text": "R47", "bbox_px": [100, 100, 130, 112]},
                {"page": 0, "text": "VCC", "bbox_px": [300, 300, 330, 312]},
            ]
            self.write_layer(root, "demo_old", shared + [{"page": 0, "text": "10K", "bbox_px": [140, 100, 168, 112]}])
            self.write_layer(root, "demo_new", shared + [{"page": 0, "text": "22K", "bbox_px": [140, 100, 169, 112]}])

            row = {
                "id": "q1",
                "task": "visualdiff",
                "split": "test",
                "images": ["images/demo_old/page_0000.png", "images/demo_new/page_0000.png"],
            }
            prediction, error = self.mod.predict_row(root, row)

            self.assertIsNone(error)
            self.assertIn("removed '10K'", prediction["answer"])
            self.assertIn("added '22K'", prediction["answer"])
            self.assertEqual(prediction["evidence"][0]["bbox"], [140, 100, 168, 112])
            self.assertEqual(prediction["evidence"][1]["bbox"], [140, 100, 169, 112])

    def test_known_image_dir_alias_resolves_internal_textlayer_doc(self):
        self.assertEqual(
            self.mod.parse_image_ref("images/viola__pcbV1.1/page_0003.png"),
            ("toradex_viola_v1.1", 3),
        )

    def test_no_text_change_falls_back_to_graphical_answer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spans = [{"page": 0, "text": "SAME", "bbox_px": [10, 10, 50, 22]}]
            self.write_layer(root, "demo_old", spans)
            self.write_layer(root, "demo_new", spans)
            row = {
                "id": "q1",
                "task": "visualdiff",
                "split": "test",
                "images": ["images/demo_old/page_0000.png", "images/demo_new/page_0000.png"],
            }
            prediction, error = self.mod.predict_row(root, row)
            self.assertIsNone(error)
            self.assertEqual(prediction["answer"], self.mod.NO_TEXT_CHANGE_ANSWER)


if __name__ == "__main__":
    unittest.main()
