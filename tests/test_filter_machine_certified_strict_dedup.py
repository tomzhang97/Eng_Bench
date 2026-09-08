import unittest

from tools.filter_machine_certified_strict_dedup import select_strict_unique_item_ids


def row(qid, item_id, bbox):
    return {
        "id": qid,
        "task": "microtext",
        "split": "train",
        "question": "Read this label.",
        "answer": "P1",
        "evidence": [{"bbox": bbox, "image_index": 0}],
        "metadata": {"item_id": item_id},
    }


class StrictMachineDedupTests(unittest.TestCase):
    def test_active_rows_win_and_candidate_order_is_stable(self):
        active = [row("active", "active_item", [1, 2, 20, 30])]
        candidates = [
            row("candidate_1", "item_1", [1, 2, 20, 30]),
            row("candidate_2", "item_2", [40, 50, 60, 70]),
            row("candidate_3", "item_3", [40, 50, 60, 70]),
        ]
        kept, held = select_strict_unique_item_ids(active, candidates)
        self.assertEqual({"item_2"}, kept)
        self.assertEqual("active", held["item_1"])
        self.assertEqual("candidate_2", held["item_3"])


if __name__ == "__main__":
    unittest.main()
