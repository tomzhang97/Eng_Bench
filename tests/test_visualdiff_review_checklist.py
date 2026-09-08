import importlib.util
import unittest
from pathlib import Path


def load_review_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "tools" / "visualdiff_review_checklist.py"
    spec = importlib.util.spec_from_file_location("visualdiff_review_checklist", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VisualdiffReviewChecklistTest(unittest.TestCase):
    def test_export_rows_prefers_reserved_split(self) -> None:
        module = load_review_module()
        rows = module.export_rows(
            [
                {
                    "pair_id": "p1",
                    "split": "provisional_review",
                    "reserved_split": "test",
                },
                {"pair_id": "p2", "split": "dev"},
            ]
        )

        self.assertEqual([row["split"] for row in rows], ["test", "dev"])

    def test_apply_checklist_accepts_human_friendly_status_aliases(self) -> None:
        review = [
            {"pair_id": "p1", "description": "Existing description."},
            {"pair_id": "p2", "description": "CHANGE_DESC_GT_TODO"},
            {"pair_id": "p3", "description": "Existing description."},
            {"pair_id": "p4", "description": "Existing description."},
        ]
        checklist = [
            {"pair_id": "p1", "human_status": "accepted"},
            {
                "pair_id": "p2",
                "human_status": "edited",
                "human_description": "Capacitor label changed from C1 to C2.",
            },
            {"pair_id": "p3", "human_status": "rejected", "human_notes": "old/new identical"},
            {"pair_id": "p4", "human_status": "full_page"},
        ]

        module = load_review_module()
        updated, stats, errors = module.apply_checklist(review, checklist)
        by_id = {row["pair_id"]: row for row in updated}

        self.assertEqual(errors, [])
        self.assertEqual(stats["valid"], 1)
        self.assertEqual(stats["edit"], 1)
        self.assertEqual(stats["reject_unclear"], 1)
        self.assertEqual(stats["needs_full_page"], 1)
        self.assertEqual(by_id["p1"]["human_review_status"], "valid")
        self.assertEqual(by_id["p2"]["human_review_status"], "edit")
        self.assertEqual(by_id["p2"]["description"], "Capacitor label changed from C1 to C2.")
        self.assertEqual(by_id["p3"]["human_review_status"], "reject_unclear")
        self.assertEqual(by_id["p4"]["human_review_status"], "needs_full_page")

    def test_apply_checklist_rejects_layout_status_with_guidance(self) -> None:
        review = [{"pair_id": "p1", "description": "Existing description."}]
        checklist = [
            {
                "pair_id": "p1",
                "human_status": "layout",
                "human_description": "A label moved relative to the title block.",
            }
        ]

        module = load_review_module()
        _updated, stats, errors = module.apply_checklist(review, checklist)

        self.assertEqual(stats["invalid_status"], 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("Use human_status=edit with human_description", errors[0])
        self.assertIn("reject_unclear", errors[0])

    def test_apply_checklist_promotes_accepted_todo_when_description_is_supplied(self) -> None:
        review = [{"pair_id": "p1", "description": "CHANGE_DESC_GT_TODO"}]
        checklist = [
            {
                "pair_id": "p1",
                "human_status": "accepted",
                "human_description": "The visible revision date changed from 2023-24 to 2024-25.",
            }
        ]

        module = load_review_module()
        updated, stats, errors = module.apply_checklist(review, checklist)

        self.assertEqual(errors, [])
        self.assertEqual(stats["edit"], 1)
        self.assertEqual(stats["mergeable"], 1)
        self.assertEqual(updated[0]["human_review_status"], "edit")
        self.assertEqual(updated[0]["description"], "The visible revision date changed from 2023-24 to 2024-25.")
        self.assertEqual(updated[0]["desc_source"], "human")

    def test_apply_checklist_blocks_accepted_todo_without_description(self) -> None:
        review = [{"pair_id": "p1", "description": "CHANGE_DESC_GT_TODO"}]
        checklist = [{"pair_id": "p1", "human_status": "accepted"}]

        module = load_review_module()
        _updated, stats, errors = module.apply_checklist(review, checklist)

        self.assertEqual(stats["valid_on_todo_description"], 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("accepted rows on TODO descriptions require human_description", errors[0])


if __name__ == "__main__":
    unittest.main()
