import unittest

from tools import merge_microtext_region_candidates as merge_tool


def candidate(candidate_id, bbox, *, doc_id="doc", page_index=0):
    return {
        "candidate_id": candidate_id,
        "doc_id": doc_id,
        "version_id": "v1",
        "page_index": page_index,
        "bbox": bbox,
        "image_path": f"derived/pages_300dpi/{doc_id}/page_000.png",
        "proposed_text": "",
        "category": "unknown_microtext",
    }


class MergeMicrotextRegionCandidatesTests(unittest.TestCase):
    def test_merges_union_and_preserves_review_gate(self):
        rows = [candidate("left", [10, 20, 40, 50]), candidate("right", [42, 18, 80, 52])]
        specs = [
            {
                "merged_candidate_id": "merged",
                "source_candidate_ids": "left|right",
                "proposed_text": "CONTROL ROOM",
                "category": "room_label",
                "notes": "complete adjacent label",
            }
        ]

        merged, report = merge_tool.merge_candidates(rows, specs)

        self.assertEqual(merged[0]["bbox"], [10, 18, 80, 52])
        self.assertEqual(merged[0]["proposed_text"], "CONTROL ROOM")
        self.assertEqual(
            merged[0]["question_text"],
            "What room label is shown in this region?",
        )
        self.assertEqual(merged[0]["review_status"], "needs_review")
        self.assertEqual(merged[0]["target_text"], "")
        self.assertEqual(report["gold_rows_added"], 0)

    def test_rejects_cross_page_merge(self):
        rows = [candidate("a", [0, 0, 10, 10]), candidate("b", [10, 0, 20, 10], page_index=1)]
        specs = [
            {
                "merged_candidate_id": "merged",
                "source_candidate_ids": "a|b",
                "proposed_text": "BAD MERGE",
                "category": "room_label",
            }
        ]

        with self.assertRaisesRegex(ValueError, "page_index"):
            merge_tool.merge_candidates(rows, specs)

    def test_rejects_source_reuse_across_specs(self):
        rows = [
            candidate("a", [0, 0, 10, 10]),
            candidate("b", [10, 0, 20, 10]),
            candidate("c", [20, 0, 30, 10]),
        ]
        specs = [
            {
                "merged_candidate_id": "one",
                "source_candidate_ids": "a|b",
                "proposed_text": "ONE",
                "category": "room_label",
            },
            {
                "merged_candidate_id": "two",
                "source_candidate_ids": "b|c",
                "proposed_text": "TWO",
                "category": "room_label",
            },
        ]

        with self.assertRaisesRegex(ValueError, "reused"):
            merge_tool.merge_candidates(rows, specs)


if __name__ == "__main__":
    unittest.main()
