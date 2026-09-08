from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.unify_dataset import process_visualdiff


class UnifyDatasetVisualDiffImageTests(unittest.TestCase):
    def test_explicit_review_image_paths_are_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            annotations = root / "visualdiff" / "annotations"
            old_image = root / "derived" / "pages_300dpi" / "old_doc" / "page_000.png"
            new_image = root / "derived" / "pages_300dpi" / "new_doc" / "page_000.png"
            annotations.mkdir(parents=True)
            old_image.parent.mkdir(parents=True)
            new_image.parent.mkdir(parents=True)
            old_image.write_bytes(b"old")
            new_image.write_bytes(b"new")
            (annotations / "visualdiff_pairs.jsonl").write_text(
                (
                    '{"pair_id":"pair","doc_id":"family","version_id_old":"old",'
                    '"version_id_new":"new","page_index_old":0,"page_index_new":0,'
                    '"image_old":"derived/pages_300dpi/old_doc/page_000.png",'
                    '"image_new":"derived/pages_300dpi/new_doc/page_000.png",'
                    '"bbox_old":[0,0,1,1],"bbox_new":[0,0,1,1],"split":"dev"}\n'
                ),
                encoding="utf-8",
            )
            (annotations / "visualdiff_questions.jsonl").write_text(
                '{"question_id":"q_pair","pair_id":"pair","query_text":"What changed?",'
                '"answer_text":"A label changed."}\n',
                encoding="utf-8",
            )

            rows = process_visualdiff(root)

            self.assertEqual(
                rows[0]["images"],
                [
                    "derived/pages_300dpi/old_doc/page_000.png",
                    "derived/pages_300dpi/new_doc/page_000.png",
                ],
            )


if __name__ == "__main__":
    unittest.main()
