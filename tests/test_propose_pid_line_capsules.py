from __future__ import annotations

import unittest

from tools.propose_pid_line_capsules import (
    associate_ocr,
    bbox_iou,
    build_proposals,
    suppress_overlaps,
)


class ProposePidLineCapsulesTests(unittest.TestCase):
    def test_bbox_iou_and_overlap_suppression(self) -> None:
        self.assertAlmostEqual(bbox_iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)
        rows = [
            {"bbox": [0, 0, 80, 24], "orientation": "horizontal", "contour_extent": 0.8},
            {"bbox": [1, 1, 79, 23], "orientation": "horizontal", "contour_extent": 0.8},
            {"bbox": [100, 0, 180, 24], "orientation": "horizontal", "contour_extent": 0.8},
        ]
        self.assertEqual(len(suppress_overlaps(rows)), 2)

    def test_associate_ocr_orders_unique_alternatives(self) -> None:
        rows = [
            {"bbox": [10, 10, 40, 20], "proposed_text": "215/2Q", "ocr_confidence": 0.9},
            {"bbox": [10, 10, 40, 20], "proposed_text": "215/2Q", "ocr_confidence": 0.9},
            {"bbox": [15, 10, 45, 20], "proposed_text": "215-1/2-Q", "ocr_confidence": 0.8},
            {"bbox": [500, 500, 530, 510], "proposed_text": "outside", "ocr_confidence": 1.0},
        ]
        alternatives = associate_ocr([0, 0, 60, 30], rows)
        self.assertEqual([row["text"] for row in alternatives], ["215/2Q", "215-1/2-Q"])

    def test_build_proposals_is_review_only_and_respects_exclusions(self) -> None:
        geometry = [
            {"bbox": [0, 0, 80, 24], "orientation": "horizontal", "contour_extent": 0.8},
            {"bbox": [100, 0, 124, 80], "orientation": "vertical", "contour_extent": 0.81},
        ]
        ocr = [
            {"bbox": [10, 5, 70, 20], "proposed_text": "215/2Q", "ocr_confidence": 0.95},
            {"bbox": [104, 10, 120, 70], "proposed_text": "116-2", "ocr_confidence": 0.92},
        ]
        proposals = build_proposals(
            geometry_rows=geometry,
            ocr_rows=ocr,
            excluded_bboxes=[[95, 0, 130, 85]],
            doc_id="doc",
            version_id="v1",
            page_index=0,
            image_path="page.png",
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["proposed_text"], "215/2Q")
        self.assertEqual(proposals[0]["category"], "unknown_microtext")
        self.assertFalse(proposals[0]["safe_to_merge_gold"])
        self.assertTrue(proposals[0]["candidate_id"].startswith("pidcapsule__"))


if __name__ == "__main__":
    unittest.main()
