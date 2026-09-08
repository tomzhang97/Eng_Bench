import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from enrich_visualdiff_review_text import enrich_rows


class EnrichVisualDiffReviewTextTest(unittest.TestCase):
    def test_adds_nearby_text_and_draft_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer = root / "derived" / "textlayer"
            textlayer.mkdir(parents=True)
            (textlayer / "old_doc.jsonl").write_text(
                json.dumps({"page": 0, "text": "R1 10k", "bbox_px": [100, 100, 180, 130]}) + "\n",
                encoding="utf-8",
            )
            (textlayer / "new_doc.jsonl").write_text(
                json.dumps({"page": 0, "text": "R1 22k", "bbox_px": [100, 100, 180, 130]}) + "\n",
                encoding="utf-8",
            )
            rows = [
                {
                    "pair_id": "pair",
                    "image_old": "derived/pages_300dpi/old_doc/page_000.png",
                    "image_new": "derived/pages_300dpi/new_doc/page_000.png",
                    "page_old": 0,
                    "page_new": 0,
                    "bbox_old": [110, 105, 150, 125],
                    "bbox_new": [110, 105, 150, 125],
                }
            ]
            enriched, report = enrich_rows(root, rows, margin=5, context_margin=20)
            self.assertTrue(report["valid"])
            self.assertEqual(enriched[0]["old_text"], "R1 10k")
            self.assertEqual(enriched[0]["new_text"], "R1 22k")
            self.assertEqual(enriched[0]["change_type"], "text")
            self.assertIn("R1 10k", enriched[0]["description"])
            self.assertFalse(enriched[0]["safe_to_merge_gold"])

    def test_missing_textlayer_is_reported_without_aborting_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "pair_id": "pair",
                    "image_old": "derived/pages_300dpi/old_doc/page_000.png",
                    "image_new": "derived/pages_300dpi/new_doc/page_000.png",
                    "page_old": 0,
                    "page_new": 0,
                    "bbox_old": [110, 105, 150, 125],
                    "bbox_new": [110, 105, 150, 125],
                }
            ]

            enriched, report = enrich_rows(root, rows, margin=5, context_margin=20)

            self.assertTrue(report["valid"])
            self.assertEqual(report["missing_textlayer_docs"], ["new_doc", "old_doc"])
            self.assertEqual(report["missing_textlayer_doc_count"], 2)
            self.assertEqual(enriched[0]["old_text"], "")
            self.assertEqual(enriched[0]["new_text"], "")
            self.assertEqual(enriched[0]["textlayer_status"], "missing_one_or_more")
            self.assertEqual(
                enriched[0]["description_source"],
                "machine_visual_candidate_missing_textlayer",
            )
            self.assertFalse(enriched[0]["safe_to_merge_gold"])


if __name__ == "__main__":
    unittest.main()
