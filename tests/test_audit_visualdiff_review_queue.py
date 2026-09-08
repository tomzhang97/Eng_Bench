from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import audit_visualdiff_review_queue


class VisualDiffReviewQueueAuditTest(unittest.TestCase):
    def test_holds_only_pixel_identical_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.png"
            same_path = root / "same.png"
            changed_path = root / "changed.png"
            Image.new("RGB", (40, 40), "white").save(old_path)
            Image.new("RGB", (40, 40), "white").save(same_path)
            changed = Image.new("RGB", (40, 40), "white")
            changed.putpixel((20, 20), (0, 0, 0))
            changed.save(changed_path)
            base = {
                "project_id": "vdiff__example__v1__to__v2",
                "image_old": str(old_path),
                "bbox_old": [10, 10, 30, 30],
                "bbox_new": [10, 10, 30, 30],
            }
            rows = [
                {**base, "pair_id": "same", "image_new": str(same_path)},
                {**base, "pair_id": "changed", "image_new": str(changed_path)},
            ]

            review_rows, held_rows, report = audit_visualdiff_review_queue.build_audit(
                root, rows, pad_px=0
            )

            self.assertEqual([row["pair_id"] for row in review_rows], ["changed"])
            self.assertEqual([row["pair_id"] for row in held_rows], ["same"])
            self.assertEqual(report["review_ready_rows"], 1)
            self.assertEqual(report["machine_held_rows"], 1)
            self.assertTrue(held_rows[0]["machine_audit"]["pixel_exact_match"])

    def test_holds_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = {
                "pair_id": "missing",
                "image_old": "missing-old.png",
                "image_new": "missing-new.png",
                "bbox_old": [0, 0, 10, 10],
                "bbox_new": [0, 0, 10, 10],
            }

            review_rows, held_rows, _ = audit_visualdiff_review_queue.build_audit(root, [row])

            self.assertEqual(review_rows, [])
            self.assertEqual(held_rows[0]["machine_audit"]["disposition"], "hold_missing_or_invalid_evidence")

    def test_mark_review_ready_is_explicit_and_never_mergeable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.png"
            new_path = root / "new.png"
            Image.new("RGB", (40, 40), "white").save(old_path)
            changed = Image.new("RGB", (40, 40), "white")
            changed.putpixel((20, 20), (0, 0, 0))
            changed.save(new_path)
            row = {
                "pair_id": "changed",
                "project_id": "vdiff__example__v1__to__v2",
                "image_old": str(old_path),
                "image_new": str(new_path),
                "bbox_old": [10, 10, 30, 30],
                "bbox_new": [10, 10, 30, 30],
                "review_bucket": "fixture_unverified",
                "safe_to_merge_gold": True,
            }

            review_rows, held_rows, report = audit_visualdiff_review_queue.build_audit(
                root, [row], pad_px=0, mark_review_ready=True
            )

            self.assertEqual(held_rows, [])
            self.assertEqual(review_rows[0]["review_status"], "needs_review")
            self.assertEqual(review_rows[0]["machine_qa_status"], "visualdiff_queue_audit_pass")
            self.assertEqual(review_rows[0]["review_bucket"], "fixture_machine_passing")
            self.assertFalse(review_rows[0]["safe_to_merge_gold"])
            self.assertTrue(report["marked_review_ready"])


if __name__ == "__main__":
    unittest.main()
