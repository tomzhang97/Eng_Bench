import json
import tempfile
import unittest
from pathlib import Path

from tools.prepare_microtext_balance_queue import prepare_rows


class PrepareMicrotextBalanceQueueTests(unittest.TestCase):
    def test_combines_deterministically_and_holds_non_target_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path_b = root / "b.jsonl"
            path_a = root / "a.jsonl"
            path_b.write_text(
                json.dumps({"candidate_id": "pin", "doc_id": "d2", "category": "pin_label"})
                + "\n",
                encoding="utf-8",
            )
            path_a.write_text(
                json.dumps(
                    {"candidate_id": "dim", "doc_id": "d1", "category": "dimension_value"}
                )
                + "\n",
                encoding="utf-8",
            )

            combined, target, held, report = prepare_rows(
                [path_b, path_a], {"dimension_value"}
            )

            self.assertEqual(["dim", "pin"], [row["candidate_id"] for row in combined])
            self.assertEqual(["dim"], [row["candidate_id"] for row in target])
            self.assertEqual(["pin"], [row["candidate_id"] for row in held])
            self.assertEqual("machine_held", held[0]["review_status"])
            self.assertFalse(held[0]["safe_to_merge_gold"])
            self.assertEqual(2, report["totals"]["combined_rows"])
            self.assertEqual({"pin_label": 1}, report["held_category_counts"])

    def test_requires_target_category(self) -> None:
        with self.assertRaisesRegex(ValueError, "target category"):
            prepare_rows([], set())


if __name__ == "__main__":
    unittest.main()
