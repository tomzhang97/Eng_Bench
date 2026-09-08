import importlib.util
import unittest
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "align_pair_orb", ROOT / "tools" / "03_align_pair_orb.py"
)
assert SPEC and SPEC.loader
ALIGN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ALIGN)


class AlignPairOrbTests(unittest.TestCase):
    def test_explicit_page_pairs_support_cross_page_mapping(self):
        self.assertEqual(
            [(38, 109), (22, 108)],
            ALIGN.parse_page_pairs("0-2", "38:109,22:108"),
        )

    def test_page_pairs_default_to_same_index(self):
        self.assertEqual(
            [(3, 3), (4, 4), (5, 5)],
            ALIGN.parse_page_pairs("3-5", ""),
        )

    def test_explicit_page_pairs_reject_duplicate_destinations(self):
        with self.assertRaisesRegex(ValueError, "destination pages must be unique"):
            ALIGN.parse_page_pairs("0", "1:7,2:7")

    def test_weak_homography_fails_closed(self):
        homography = np.eye(3, dtype=np.float64)
        accepted, info = ALIGN.enforce_alignment_quality(
            homography,
            {"status": "ok", "inliers": 21, "inlier_ratio": 0.02625},
            min_inliers=20,
            min_inlier_ratio=0.08,
        )
        self.assertIsNone(accepted)
        self.assertEqual("fail", info["status"])
        self.assertEqual("weak_homography", info["reason"])

    def test_supported_homography_is_retained(self):
        homography = np.eye(3, dtype=np.float64)
        accepted, info = ALIGN.enforce_alignment_quality(
            homography,
            {"status": "ok", "inliers": 80, "inlier_ratio": 0.25},
            min_inliers=20,
            min_inlier_ratio=0.08,
        )
        self.assertIs(accepted, homography)
        self.assertEqual("ok", info["status"])

    def test_normalized_matching_recovers_two_x_canvas_scale(self):
        image = np.full((700, 1000), 255, dtype=np.uint8)
        rng = np.random.default_rng(17)
        for index in range(120):
            x = int(rng.integers(30, 970))
            y = int(rng.integers(30, 670))
            radius = int(rng.integers(2, 9))
            cv2.circle(image, (x, y), radius, int(rng.integers(0, 140)), 1)
            if index % 6 == 0:
                cv2.line(image, (x, y), (min(990, x + 45), min(690, y + 25)), 0, 2)
        cv2.putText(image, "REVISION A1", (80, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 0, 3)
        scaled = cv2.resize(image, (2000, 1400), interpolation=cv2.INTER_CUBIC)

        homography, info = ALIGN.normalized_orb_homography(
            image,
            scaled,
            max_dimension=900,
        )

        self.assertEqual("ok", info["status"])
        self.assertGreater(info["inlier_ratio"], 0.7)
        self.assertIsNotNone(homography)
        points = np.asarray([[[100.0, 100.0]], [[800.0, 500.0]]], dtype=np.float32)
        mapped = cv2.perspectiveTransform(points, homography).reshape(-1, 2)
        np.testing.assert_allclose(mapped, np.asarray([[200.0, 200.0], [1600.0, 1000.0]]), atol=6.0)


if __name__ == "__main__":
    unittest.main()
