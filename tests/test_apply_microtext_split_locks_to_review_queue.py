import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "apply_microtext_split_locks_to_review_queue.py"
SPEC = importlib.util.spec_from_file_location("apply_microtext_split_locks_to_review_queue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ApplyMicrotextSplitLocksTests(unittest.TestCase):
    def test_plan_and_active_gold_agreement_locks_row(self):
        rows = [{"candidate_id": "a", "doc_id": "drawing"}]
        plan = {"reservations": [{"task": "microtext", "unit_id": "drawing", "split": "train"}]}
        active = [{"task": "microtext", "doc_id": "drawing", "split": "train"}]

        passing, held, report = MODULE.apply_split_locks(rows, plan, active)

        self.assertEqual(held, [])
        self.assertEqual(passing[0]["split"], "train")
        self.assertTrue(passing[0]["split_locked"])
        self.assertFalse(passing[0]["safe_to_merge_gold"])
        self.assertEqual(report["split_counts"], {"train": 1})

    def test_active_gold_fallback_locks_unplanned_document(self):
        passing, held, _ = MODULE.apply_split_locks(
            [{"candidate_id": "a", "doc_id": "drawing"}],
            {"reservations": []},
            [{"task": "microtext", "doc_id": "drawing", "split": "dev"}],
        )

        self.assertEqual(held, [])
        self.assertEqual(passing[0]["split"], "dev")
        self.assertEqual(passing[0]["machine_split_lock_source"], "active_gold_doc_family")

    def test_plan_active_gold_conflict_is_held(self):
        passing, held, report = MODULE.apply_split_locks(
            [{"candidate_id": "a", "doc_id": "drawing"}],
            {"reservations": [{"task": "microtext", "unit_id": "drawing", "split": "test"}]},
            [{"task": "microtext", "doc_id": "drawing", "split": "train"}],
        )

        self.assertEqual(passing, [])
        self.assertEqual(held[0]["machine_hold_reason"], "staged_plan_active_gold_split_conflict")
        self.assertFalse(report["valid"])

    def test_unresolved_document_is_held(self):
        passing, held, _ = MODULE.apply_split_locks(
            [{"candidate_id": "a", "doc_id": "new"}],
            {"reservations": []},
            [],
        )

        self.assertEqual(passing, [])
        self.assertEqual(held[0]["machine_hold_reason"], "unresolved_split_lock")


if __name__ == "__main__":
    unittest.main()
