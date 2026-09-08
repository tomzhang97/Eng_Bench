from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from baselines import textlayer_diff_visualdiff_baseline as visualdiff_baseline
from baselines import textlayer_microtext_baseline as microtext_baseline


class TextlayerBaselineAbstentionTests(unittest.TestCase):
    def test_microtext_missing_layer_emits_explicit_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                {
                    "id": "q_micro",
                    "task": "microtext",
                    "images": ["images/doc__v1/page_0000.png"],
                    "metadata": {"doc_id": "doc", "category": "pin_label"},
                }
            ]

            predictions, stats = microtext_baseline.predict_rows(Path(tmp), rows)

            self.assertEqual(len(predictions), 1)
            self.assertEqual(predictions[0]["id"], "q_micro")
            self.assertTrue(predictions[0]["metadata"]["abstained"])
            self.assertEqual(predictions[0]["metadata"]["abstention_reason"], "missing_textlayer")
            self.assertEqual(stats["abstentions"], 1)

    def test_visualdiff_unparseable_image_refs_emit_explicit_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                {
                    "id": "q_visual",
                    "task": "visualdiff",
                    "images": [
                        "derived/pages_300dpi/old/page_000.png",
                        "derived/pages_300dpi/new/page_000.png",
                    ],
                }
            ]

            predictions, stats = visualdiff_baseline.predict_rows(Path(tmp), rows)

            self.assertEqual(len(predictions), 1)
            self.assertEqual(predictions[0]["id"], "q_visual")
            self.assertTrue(predictions[0]["metadata"]["abstained"])
            self.assertEqual(predictions[0]["metadata"]["abstention_reason"], "missing_schema")
            self.assertEqual(stats["abstentions"], 1)


if __name__ == "__main__":
    unittest.main()
