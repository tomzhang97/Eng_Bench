from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.verify_review_batch_embedded_evidence import resolve_under, verify_batch


class ReviewBatchEmbeddedEvidenceTests(unittest.TestCase):
    def test_resolve_under_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir) / "pack"
            parent.mkdir()
            with self.assertRaisesRegex(ValueError, "escapes review pack"):
                resolve_under(parent, "../outside.png")

    def test_verify_batch_resolves_visualdiff_panel_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            batch = Path(temp_dir) / "batch"
            pack = batch / "review_packs" / "visualdiff_1"
            panel = pack / "panels" / "row.png"
            panel.parent.mkdir(parents=True)
            panel.write_bytes(b"not-used-by-mock")
            (pack / "manifest.jsonl").write_text(
                json.dumps({"pair_id": "p1", "panel_path": "panels/row.png"}) + "\n",
                encoding="utf-8",
            )
            payload = {
                "microtext": {"rows": 0},
                "visualdiff": {
                    "rows": 1,
                    "manifest": (pack / "manifest.jsonl").as_posix(),
                    "workbook": "visual.xlsx",
                    "sheet": "Visual",
                },
            }
            (batch / "workbook_build_payload.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            expected_report = {
                "expected_rows": 1,
                "picture_rows": 1,
                "exact_image_matches": 1,
            }
            with patch(
                "tools.verify_review_batch_embedded_evidence.verify_workbook",
                return_value=(expected_report, []),
            ) as mocked:
                report = verify_batch(batch)
            self.assertTrue(report["valid"])
            self.assertEqual(report["exact_image_matches"], 1)
            evidence_rows = mocked.call_args.args[2]
            self.assertEqual(evidence_rows[0]["evidence_path"], panel.resolve())


if __name__ == "__main__":
    unittest.main()
