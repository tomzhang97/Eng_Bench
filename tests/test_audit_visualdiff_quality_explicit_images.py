from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.audit_visualdiff_quality import bbox_failure


class AuditVisualDiffQualityExplicitImageTests(unittest.TestCase):
    def test_bbox_audit_uses_explicit_review_image_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_path = root / "derived" / "pages_300dpi" / "old_doc" / "page_000.png"
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "white").save(image_path)
            row = {
                "pair_id": "pair",
                "doc_id": "family",
                "version_id_old": "old",
                "page_index_old": 0,
                "image_old": "derived/pages_300dpi/old_doc/page_000.png",
                "bbox_old": [10, 10, 20, 20],
            }

            failure = bbox_failure(
                root,
                row,
                "bbox_old",
                "version_id_old",
                "page_index_old",
            )

            self.assertIsNone(failure)


if __name__ == "__main__":
    unittest.main()
