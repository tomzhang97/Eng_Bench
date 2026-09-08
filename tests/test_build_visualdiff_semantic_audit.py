from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.build_visualdiff_semantic_audit import build_sheets, write_outputs


class BuildVisualDiffSemanticAuditTests(unittest.TestCase):
    def test_builds_evidence_only_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.png"
            new_path = root / "new.png"
            old = Image.new("RGB", (800, 600), "white")
            new = Image.new("RGB", (800, 600), "white")
            ImageDraw.Draw(old).rectangle((300, 200, 360, 260), outline="black", width=5)
            ImageDraw.Draw(new).ellipse((300, 200, 360, 260), outline="black", width=5)
            old.save(old_path)
            new.save(new_path)
            rows = [
                {
                    "pair_id": "pair_1",
                    "image_old": "old.png",
                    "image_new": "new.png",
                    "bbox_old": [300, 200, 360, 260],
                    "bbox_new": [300, 200, 360, 260],
                    "description": "draft",
                    "description_source": "machine_visual_candidate_missing_textlayer",
                }
            ]
            output_dir = root / "out"
            manifest, issues = build_sheets(root, rows, output_dir, rows_per_sheet=1)
            write_outputs(output_dir, manifest, issues)

            self.assertEqual([], issues)
            self.assertEqual(1, len(manifest))
            self.assertTrue((output_dir / "sheet_001.png").is_file())
            report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["valid"])
            self.assertFalse(report["safe_to_merge_gold"])

    def test_missing_page_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "pair_id": "missing_pair",
                    "image_old": "missing_old.png",
                    "image_new": "missing_new.png",
                    "bbox_old": [1, 1, 2, 2],
                    "bbox_new": [1, 1, 2, 2],
                    "description": "draft",
                }
            ]
            manifest, issues = build_sheets(root, rows, root / "out", rows_per_sheet=1)
            self.assertEqual(1, len(manifest))
            self.assertEqual(1, len(issues))
            self.assertIn("missing_pair", issues[0])


if __name__ == "__main__":
    unittest.main()
