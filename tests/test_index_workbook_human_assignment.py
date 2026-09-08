from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import index_workbook_human_assignment as indexer


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class WorkbookHumanAssignmentIndexTests(unittest.TestCase):
    def test_source_selection_does_not_resurrect_held_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_demo_holds.jsonl",
                [
                    {
                        "candidate_id": "candidate_1",
                        "doc_id": "demo_doc",
                        "review_status": "needs_review",
                        "reservoir_disposition": "held",
                        "reservoir_hold_reason": "target_cap",
                    }
                ],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_demo_human_ready.jsonl",
                [
                    {
                        "candidate_id": "candidate_1",
                        "doc_id": "demo_doc",
                        "version_id": "v1",
                        "review_status": "needs_review",
                        "image_path": "images/demo.png",
                        "bbox": [1, 2, 3, 4],
                        "category": "equipment_tag",
                        "target_text": "P-101",
                    }
                ],
            )

            rows, conflicts = indexer.collect_best_source_rows(
                root,
                {"microtext": {"candidate_1"}, "visualdiff": set()},
            )

            selected = rows[("microtext", "candidate_1")]
            self.assertEqual(selected["version_id"], "v1")
            self.assertFalse(indexer.source_readiness.review_exclusion_reason(selected))
            self.assertEqual(conflicts, {})

    def test_manifest_rows_are_review_only_and_preserve_primary_order(self) -> None:
        source_rows = {
            ("microtext", "m1"): {
                "candidate_id": "m1",
                "doc_id": "doc",
                "version_id": "v1",
                "category": "room_label",
                "target_text": "PUMP ROOM",
            },
            ("visualdiff", "v1"): {
                "pair_id": "v1",
                "project_id": "family",
                "change_type": "layout",
            },
        }

        rows, unresolved = indexer.build_manifest_rows(
            Path("PRIMARY_REVIEW_2.xlsx"),
            [{"candidate_id": "m1", "primary_index": "1"}],
            [{"pair_id": "v1", "primary_index": "2"}],
            source_rows,
        )

        self.assertEqual(unresolved, [])
        self.assertEqual([row["task"] for row in rows], ["microtext", "visualdiff"])
        self.assertEqual([row["primary_index"] for row in rows], ["1", "2"])
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in rows))
        self.assertTrue(all(row["review_status"] == "needs_review" for row in rows))


if __name__ == "__main__":
    unittest.main()
