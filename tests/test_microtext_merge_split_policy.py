import unittest

from tools.microtext_merge import merge_review_rows


class MicrotextMergeSplitPolicyTest(unittest.TestCase):
    def test_assigns_document_split_from_frozen_policy(self) -> None:
        reviewed = [
            {
                "candidate_id": "mt_1",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "target_text": "P-101",
                "category": "equipment_tag",
                "review_status": "accepted",
            }
        ]

        items, questions, stats = merge_review_rows(
            [],
            [],
            reviewed,
            split="dev",
            reviewed_at="2026-07-10T00:00:00Z",
            split_by_doc={"doc_a": "test"},
            require_split_map=True,
        )

        self.assertEqual(1, stats["accepted"])
        self.assertEqual("test", items[0]["split"])
        self.assertEqual("test", questions[0]["split"])

    def test_holds_unmapped_document_when_policy_is_required(self) -> None:
        reviewed = [
            {
                "candidate_id": "mt_1",
                "doc_id": "doc_a",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "target_text": "P-101",
                "category": "equipment_tag",
                "review_status": "accepted",
            }
        ]

        items, questions, stats = merge_review_rows(
            [],
            [],
            reviewed,
            split="dev",
            reviewed_at="2026-07-10T00:00:00Z",
            split_by_doc={},
            require_split_map=True,
        )

        self.assertFalse(items)
        self.assertFalse(questions)
        self.assertEqual(1, stats["missing_split_mapping"])


if __name__ == "__main__":
    unittest.main()
