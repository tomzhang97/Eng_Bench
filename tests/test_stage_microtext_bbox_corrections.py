import unittest

from tools.stage_microtext_bbox_corrections import corrected_row
from tools.preview_reviewed_gold_promotion import status_for, FINAL_MICROTEXT


class BboxCorrectionTests(unittest.TestCase):
    def test_preserves_semantics_and_prior_vote_but_does_not_claim_new_review(self):
        original = {"candidate_id": "c", "image_path": "p.png", "bbox": [10, 10, 20, 20],
                    "review_status": "accepted", "human_review_status": "accepted", "proposed_text": "P1",
                    "category": "pin_label", "crop_path": "stale.png"}
        correction = {"original_bbox": original["bbox"], "bbox": [8, 5, 22, 22], "reason": "clipped top"}
        proposal = corrected_row(original, correction, (100, 100), "hash")
        self.assertEqual("accepted", proposal["prior_human_review"]["review_status"])
        self.assertEqual("accepted", original["review_status"])
        self.assertEqual("P1", proposal["proposed_text"])
        self.assertEqual("", proposal["crop_path"])
        self.assertNotIn(status_for(proposal, "microtext"), FINAL_MICROTEXT)
        self.assertFalse(proposal["safe_to_merge_gold"])

    def test_stale_or_out_of_bounds_correction_fails(self):
        row = {"bbox": [1, 1, 3, 3], "image_path": "p.png"}
        for original, after in (([1, 0, 3, 3], [0, 0, 4, 4]), ([1, 1, 3, 3], [-1, 0, 4, 4])):
            with self.assertRaises(ValueError):
                corrected_row(row, {"original_bbox": original, "bbox": after, "reason": "repair"}, (10, 10), "hash")


if __name__ == "__main__":
    unittest.main()
