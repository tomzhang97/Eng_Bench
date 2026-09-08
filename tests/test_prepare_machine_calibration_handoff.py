from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import prepare_machine_calibration_handoff as handoff


class PrepareMachineCalibrationHandoffTest(unittest.TestCase):
    def test_copy_evidence_enriches_hashes_and_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            output = root / "output"
            (source / "crops").mkdir(parents=True)
            (source / "contexts").mkdir(parents=True)
            Image.new("RGB", (80, 20), "white").save(source / "crops" / "one.png")
            Image.new("RGB", (400, 300), "white").save(
                source / "contexts" / "one.png"
            )
            rows = [
                {
                    "sample_index": "1",
                    "candidate_id": "candidate-1",
                    "crop_path": "crops/one.png",
                    "context_path": "contexts/one.png",
                }
            ]

            enriched = handoff.copy_evidence(
                source_dir=source, output_dir=output, rows=rows
            )

            self.assertEqual(enriched[0]["crop_width"], 80)
            self.assertEqual(enriched[0]["context_height"], 300)
            self.assertEqual(len(enriched[0]["crop_sha256"]), 64)
            self.assertTrue((output / "crops" / "one.png").is_file())
            self.assertTrue((output / "contexts" / "one.png").is_file())

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            handoff.safe_relative_path("../secret.png", "crops")

    def test_generated_text_uses_actual_row_count(self) -> None:
        rows = [
            {
                "sample_index": str(index),
                "crop_path": "crops/one.png",
                "context_path": "contexts/one.png",
                "proposed_text": "10k",
                "category": "component_value",
                "candidate_id": f"candidate-{index}",
            }
            for index in range(293)
        ]

        index_html = handoff.render_index(rows)
        instructions = handoff.chinese_instructions(len(rows))

        self.assertIn("机器校准 293", index_html)
        self.assertIn("共 293 条", index_html)
        self.assertIn("MACHINE_CALIBRATION_293.xlsx", instructions)
        self.assertIn("这 293 条是冻结的统计校准样本", instructions)
        self.assertNotIn("294 条", index_html + instructions)


if __name__ == "__main__":
    unittest.main()
