from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import microtext_review


class MicrotextReviewOcrSchemaTests(unittest.TestCase):
    def test_preserves_ocr_proposed_text_when_target_text_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_dir = root / "derived" / "pages_300dpi" / "pid_doc"
            page_dir.mkdir(parents=True)
            Image.new("L", (100, 100), "white").save(page_dir / "page_001.png")

            candidate = {
                "candidate_id": "ocr_1",
                "doc_id": "pid_doc",
                "version_id": "v1",
                "page_index": 1,
                "bbox": [10, 10, 40, 40],
                "target_text": "",
                "proposed_text": "PI 8",
                "category": "instrument_tag",
                "review_status": "needs_review",
            }

            rows = microtext_review.build_review_batch(
                root=root,
                candidates=[candidate],
                existing_items=[],
                limit=10,
                max_per_category=10,
            )

            self.assertEqual(1, len(rows))
            self.assertEqual("PI 8", rows[0]["proposed_text"])
            self.assertEqual("PI 8", rows[0]["text_context"])

    def test_item_key_uses_ocr_proposed_text(self) -> None:
        row = {
            "doc_id": "pid_doc",
            "page_index": 1,
            "bbox": [1, 2, 3, 4],
            "target_text": "",
            "proposed_text": "PS 13",
        }

        self.assertEqual("PS 13", microtext_review.item_key(row)[-1])


if __name__ == "__main__":
    unittest.main()
