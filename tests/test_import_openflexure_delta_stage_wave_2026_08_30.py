from __future__ import annotations

import unittest

from tools.import_openflexure_delta_stage_wave_2026_08_30 import visual_signal_disposition


class OpenFlexureDeltaStageImportTests(unittest.TestCase):
    def test_routes_visible_geometry_delta_to_review(self) -> None:
        self.assertEqual(
            "review",
            visual_signal_disposition(
                {"changed_pixel_ratio_gt16": 0.02, "mean_absolute_delta": 1.2}
            ),
        )

    def test_holds_tiny_render_delta(self) -> None:
        self.assertEqual(
            "hold",
            visual_signal_disposition(
                {"changed_pixel_ratio_gt16": 0.00002, "mean_absolute_delta": 0.001}
            ),
        )


if __name__ == "__main__":
    unittest.main()
