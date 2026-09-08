import unittest

from tools.audit_primary_visualdiff_hold_alignment import (
    LANE_MACHINE_INSPECT,
    LANE_REBUILD,
    LANE_RERENDER,
    LANE_RETIRE_CROSS_LOCATION,
    LANE_RETIRE_MACHINE_NO_CHANGE,
    LANE_RETIRE_NO_CHANGE,
    bbox_iou,
    classify_hold,
    normalized_center_distance,
    transform_bbox,
)

import numpy as np


class HoldAlignmentClassificationTest(unittest.TestCase):
    def test_human_no_change_is_retired_without_more_review(self):
        lane, action = classify_hold(3, False, "unavailable", 0.0, None)
        self.assertEqual(lane, LANE_RETIRE_NO_CHANGE)
        self.assertEqual(action, "retire_candidate_keep_out_of_gold")

    def test_high_correspondence_without_text_confirmation_is_rerendered(self):
        lane, action = classify_hold(4, True, "high", 0.0, 8.0)
        self.assertEqual(lane, LANE_RERENDER)
        self.assertIn("inverse_homography", action)

    def test_low_far_correspondence_is_rerendered_at_corrected_coordinate(self):
        lane, action = classify_hold(4, True, "low", 0.0, 4.0)
        self.assertEqual(lane, LANE_RERENDER)
        self.assertIn("inverse_homography", action)

    def test_context_match_does_not_rescue_empty_target_box(self):
        lane, action = classify_hold(
            4, True, "medium", 0.2, 0.5,
            new_box_informative=False,
            proposed_old_box_informative=True,
        )
        self.assertEqual(lane, LANE_RERENDER)
        self.assertIn("inverse_homography", action)

    def test_empty_boxes_with_low_match_are_rerendered_as_possible_addition(self):
        lane, action = classify_hold(
            4, True, "low", 0.0, 4.0,
            new_box_informative=False,
            proposed_old_box_informative=False,
        )
        self.assertEqual(lane, LANE_RERENDER)
        self.assertIn("inverse_homography", action)

    def test_uninformative_valid_alignment_is_rerendered(self):
        lane, _ = classify_hold(4, True, "uninformative", 0.0, 4.0)
        self.assertEqual(lane, LANE_RERENDER)

    def test_matching_text_and_visual_signal_is_machine_retired(self):
        lane, action = classify_hold(
            4,
            True,
            "medium",
            0.0,
            0.0,
            corrected_text_relation="exact_sequence_match",
            best_offset_x=1,
            best_offset_y=-2,
            changed_pixel_ratio=0.12,
        )
        self.assertEqual(lane, LANE_RETIRE_MACHINE_NO_CHANGE)
        self.assertIn("no_engineering_change", action)

    def test_one_sided_blank_is_not_machine_retired(self):
        lane, _ = classify_hold(
            4,
            True,
            "low",
            0.0,
            5.0,
            new_box_informative=True,
            proposed_old_box_informative=False,
            corrected_text_relation="different_text",
            best_offset_x=0,
            best_offset_y=0,
            changed_pixel_ratio=0.4,
        )
        self.assertEqual(lane, LANE_RERENDER)

    def test_missing_alignment_is_rebuilt_before_rereview(self):
        lane, _ = classify_hold(4, False, "unavailable", 0.0, None)
        self.assertEqual(lane, LANE_REBUILD)

    def test_invalid_decision_stays_held(self):
        lane, _ = classify_hold(2, True, "high", 1.0, 0.0)
        self.assertEqual(lane, LANE_MACHINE_INSPECT)


class HoldAlignmentGeometryTest(unittest.TestCase):
    def test_identity_transform_preserves_box(self):
        box = [10, 20, 30, 50]
        transformed = transform_bbox(box, np.eye(3))
        self.assertEqual(transformed, [10.0, 20.0, 30.0, 50.0])
        self.assertEqual(bbox_iou(box, transformed), 1.0)
        self.assertEqual(normalized_center_distance(box, transformed), 0.0)

    def test_translation_transform_moves_box(self):
        matrix = np.asarray([[1, 0, 12], [0, 1, -7], [0, 0, 1]], dtype=float)
        self.assertEqual(transform_bbox([10, 20, 30, 50], matrix), [22.0, 13.0, 42.0, 43.0])


if __name__ == "__main__":
    unittest.main()
