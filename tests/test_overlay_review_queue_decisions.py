import unittest

from tools.overlay_review_queue_decisions import overlay_pending_rows


class OverlayReviewQueueDecisionsTests(unittest.TestCase):
    def test_overlays_pending_rows_and_holds_selected_rows(self) -> None:
        base = [
            {"pair_id": "a", "description": "TODO", "review_status": "needs_review"},
            {"pair_id": "b", "description": "TODO", "review_status": "needs_review"},
            {"pair_id": "c", "description": "TODO", "review_status": "needs_review"},
        ]
        overlay = [
            {
                "pair_id": "a",
                "description": "Readable change.",
                "change_type": "layout_change_candidate",
                "safe_to_merge_gold": False,
            },
            {"pair_id": "outside", "description": "Not in base."},
        ]
        drops = [
            {"pair_id": "b", "machine_hold_reason": "no_visible_change"},
            {"pair_id": "outside_drop", "machine_hold_reason": "hold"},
        ]
        output, held, report = overlay_pending_rows(base, overlay, drops)
        self.assertEqual([row["pair_id"] for row in output], ["a", "c"])
        self.assertEqual([row["pair_id"] for row in held], ["b"])
        self.assertEqual(output[0]["description"], "Readable change.")
        self.assertEqual(held[0]["review_status"], "machine_held")
        self.assertEqual(report["totals"]["overlay_rows_applied"], 1)
        self.assertEqual(report["totals"]["unmatched_overlay_rows"], 1)
        self.assertTrue(report["valid"])

    def test_refuses_to_modify_human_decisions(self) -> None:
        base = [
            {
                "pair_id": "a",
                "description": "Human text",
                "human_review_status": "edit",
            }
        ]
        with self.assertRaisesRegex(ValueError, "human decisions"):
            overlay_pending_rows(
                base,
                [{"pair_id": "a", "description": "Machine text"}],
                [],
            )

    def test_overlays_pending_microtext_taxonomy_without_promoting(self) -> None:
        base = [
            {
                "candidate_id": "micro-a",
                "category": "unknown_microtext",
                "proposed_text": "MAIN",
                "question_text": "What text is shown?",
                "review_status": "needs_review",
            }
        ]
        overlay = [
            {
                "candidate_id": "micro-a",
                "category": "process_label",
                "proposed_text": "MAIN DUCT",
                "question_text": "What process step or stream label is shown in this region?",
                "machine_qa_status": "selected_for_human_review",
                "safe_to_merge_gold": False,
            }
        ]

        output, held, report = overlay_pending_rows(base, overlay, [])

        self.assertEqual(held, [])
        self.assertEqual(output[0]["category"], "process_label")
        self.assertEqual(output[0]["proposed_text"], "MAIN DUCT")
        self.assertEqual(output[0]["review_status"], "needs_review")
        self.assertFalse(output[0]["safe_to_merge_gold"])
        self.assertEqual(report["changed_fields"]["category"], 1)
        self.assertTrue(report["valid"])

    def test_rejects_overlay_drop_conflict(self) -> None:
        base = [{"pair_id": "a"}]
        with self.assertRaisesRegex(ValueError, "both overlaid and dropped"):
            overlay_pending_rows(
                base,
                [{"pair_id": "a", "description": "Machine text"}],
                [{"pair_id": "a", "machine_hold_reason": "hold"}],
            )


if __name__ == "__main__":
    unittest.main()
