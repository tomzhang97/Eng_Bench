from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.probe_held_microtext_labels import physically_overlaps, probe


def row(candidate_id: str, text: str, bbox: list[int], confidence: float = 0.99) -> dict:
    return {
        "candidate_id": candidate_id,
        "doc_id": "doc",
        "page_index": 1,
        "bbox": bbox,
        "proposed_text": text,
        "ocr_confidence": confidence,
    }


class ProbeHeldMicrotextLabelsTest(unittest.TestCase):
    def test_overlap_uses_iou_or_containment(self) -> None:
        self.assertTrue(physically_overlaps([0, 0, 10, 10], [1, 1, 9, 9]))
        self.assertFalse(physically_overlaps([0, 0, 10, 10], [9, 9, 20, 20]))

    def test_probe_is_fail_closed_and_excludes_selected_or_admin_rows(self) -> None:
        held = [
            row("a", "NEW PUMP", [20, 20, 40, 40]),
            row("b", "OLD PUMP", [0, 0, 10, 10]),
            row("c", "ORIGINAL PAGE IS OF POOR QUALITY", [50, 50, 80, 80]),
            row("d", "LOW CONF", [90, 90, 110, 110], 0.5),
        ]
        selected = [row("selected", "OLD PUMP", [0, 0, 10, 10])]
        rows, report = probe(held, selected)
        self.assertEqual([item["candidate_id"] for item in rows], ["a"])
        self.assertFalse(rows[0]["safe_to_merge_gold"])
        self.assertEqual(report["probe_rows"], 1)
        self.assertFalse(report["safe_to_merge_gold"])

    def test_direct_script_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            held = root / "held.jsonl"
            selected = root / "selected.jsonl"
            output = root / "output.jsonl"
            report = root / "report.json"
            held.write_text(json.dumps(row("a", "NEW PUMP", [20, 20, 40, 40])) + "\n", encoding="utf-8")
            selected.write_text("", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "tools" / "probe_held_microtext_labels.py"),
                    "--root",
                    str(root),
                    "--held",
                    "held.jsonl",
                    "--selected",
                    "selected.jsonl",
                    "--output",
                    "output.jsonl",
                    "--report-json",
                    "report.json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["probe_rows"], 1)


if __name__ == "__main__":
    unittest.main()
