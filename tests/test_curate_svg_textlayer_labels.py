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


class CurateSvgTextlayerLabelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(
            "curate_svg_textlayer_labels_test_module",
            ROOT / "tools" / "curate_svg_textlayer_labels.py",
        )

    def decisions(self):
        return {
            "doc_id": "diagram",
            "version_id": "v1",
            "source_candidate_id": "pid_1",
            "image_path": "pages/page_000.png",
            "default_decision": "hold",
            "selections": [
                {
                    "text": "AT",
                    "decision": "keep",
                    "category": "equipment_tag",
                    "notes": "readable equipment abbreviation",
                }
            ],
        }

    def test_curate_dispositions_every_source_span(self):
        rows = [
            {"page": 0, "text": "AT", "bbox_px": [10, 10, 50, 30]},
            {"page": 0, "text": "1", "bbox_px": [70, 10, 80, 30]},
        ]
        kept, held, report = self.mod.curate_rows(rows, self.decisions())

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["category"], "equipment_tag")
        self.assertFalse(kept[0]["safe_to_merge_gold"])
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["review_status"], "machine_held")
        self.assertTrue(report["all_source_spans_dispositioned"])
        self.assertFalse(report["active_gold_modified"])

    def test_curate_rejects_missing_explicit_text(self):
        decisions = self.decisions()
        decisions["selections"].append({"text": "MISSING", "decision": "hold"})
        with self.assertRaisesRegex(ValueError, "missing from textlayer"):
            self.mod.curate_rows(
                [{"page": 0, "text": "AT", "bbox_px": [10, 10, 50, 30]}],
                decisions,
            )

    def test_curate_rejects_ambiguous_duplicate_text(self):
        rows = [
            {"page": 0, "text": "AT", "bbox_px": [10, 10, 50, 30]},
            {"page": 0, "text": "AT", "bbox_px": [60, 10, 100, 30]},
        ]
        with self.assertRaisesRegex(ValueError, "ambiguous duplicate"):
            self.mod.curate_rows(rows, self.decisions())


if __name__ == "__main__":
    unittest.main()
