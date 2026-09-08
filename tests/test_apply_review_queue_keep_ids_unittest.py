from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import apply_review_queue_keep_ids


class ApplyReviewQueueKeepIdsTests(unittest.TestCase):
    def test_can_match_preserved_alias_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = root / "queue.jsonl"
            queue.write_text(
                "".join(
                    json.dumps(row) + "\n"
                    for row in (
                        {
                            "candidate_id": "padded-a",
                            "pre_padding_candidate_id": "original-a",
                            "doc_id": "doc-1",
                        },
                        {
                            "candidate_id": "padded-b",
                            "pre_padding_candidate_id": "original-b",
                            "doc_id": "doc-1",
                        },
                    )
                ),
                encoding="utf-8",
            )
            keep_ids = root / "keep.txt"
            keep_ids.write_text("original-b\n", encoding="utf-8")

            report = apply_review_queue_keep_ids.build_report(
                root,
                input_jsonl=queue,
                keep_ids_file=keep_ids,
                hold_reason="not_selected_before_padding",
                row_id_field="pre_padding_candidate_id",
            )

            self.assertEqual(
                [row["candidate_id"] for row in report["kept_rows"]],
                ["padded-b"],
            )
            self.assertEqual(report["row_id_field"], "pre_padding_candidate_id")


if __name__ == "__main__":
    unittest.main()
