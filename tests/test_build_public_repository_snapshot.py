import unittest

from tools.build_public_repository_snapshot import (
    LABEL_FIELDS,
    assert_test_is_label_free,
    partition_public_rows,
)


class PublicRepositorySnapshotTests(unittest.TestCase):
    def test_partition_filters_blocked_docs_and_strips_test_labels(self):
        rows = [
        {
            "id": "train-ok",
            "task": "microtext",
            "split": "train",
            "answer": "A1",
            "evidence": [{"bbox": [0, 0, 1, 1]}],
            "images": ["images/train.png"],
            "metadata": {"doc_id": "ready"},
        },
        {
            "id": "test-ok",
            "task": "visualdiff",
            "split": "test",
            "answer": "changed",
            "evidence": [{"bbox": [0, 0, 1, 1]}],
            "images": ["images/old.png", "images/new.png"],
            "metadata": {"pair_id": "pair-ready", "change_desc_gt": "changed"},
        },
        {
            "id": "blocked",
            "task": "microtext",
            "split": "dev",
            "answer": "B2",
            "images": ["images/blocked.png"],
            "metadata": {"doc_id": "blocked"},
        },
    ]
        outputs, excluded = partition_public_rows(
            rows,
            {"ready", "old-ready", "new-ready"},
            {"pair-ready": (["old-ready", "new-ready"], "")},
        )

        self.assertEqual(outputs["train"][0]["answer"], "A1")
        self.assertTrue(LABEL_FIELDS.isdisjoint(outputs["test"][0]))
        self.assertNotIn("change_desc_gt", outputs["test"][0]["metadata"])
        self.assertEqual(
            excluded,
            [
                {
                    "id": "blocked",
                    "reason": "rights_or_provenance_block",
                    "blocked_doc_ids": ["blocked"],
                }
            ],
        )
        assert_test_is_label_free(outputs["test"])


if __name__ == "__main__":
    unittest.main()
