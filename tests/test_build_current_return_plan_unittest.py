from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import build_current_return_plan


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = fields or list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class CurrentReturnPlanUnittest(unittest.TestCase):
    def test_builds_process_step_for_human_packet_index_batch_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_root = root / "derived" / "human_adjudication" / "2026-07-01_demo_batch"
            write_csv(
                packet_root / "NEXT_REVIEW_BATCH_MANIFEST.csv",
                [
                    {
                        "pack_name": "microtext_demo",
                        "kind": "microtext",
                        "checklist": "microtext_demo_validation_checklist.csv",
                        "source_jsonl": "microtext/annotations/microtext_review_demo.jsonl",
                    }
                ],
            )
            index_path = root / "derived" / "quality" / "human_packet_index_demo.json"
            write_json(
                index_path,
                {
                    "date_label": "demo",
                    "packets": [
                        {
                            "priority": 1,
                            "packet_id": "demo_ready",
                            "folder_path": "derived/human_adjudication/2026-07-01_demo_batch",
                            "zip_path": "derived/human_adjudication/demo.zip",
                            "ready_to_send": True,
                            "review_rows": 10,
                        },
                        {
                            "priority": 2,
                            "packet_id": "demo_unready",
                            "folder_path": "derived/human_adjudication/missing_unready",
                            "ready_to_send": False,
                            "review_rows": 5,
                        },
                    ],
                },
            )

            report = build_current_return_plan.build_report(
                root,
                index_json=index_path,
                return_label="returned_demo",
                returned_root="<returned_packet_root>",
            )

            self.assertEqual(report["totals"]["packets"], 1)
            self.assertEqual(report["totals"]["skipped_unready_packets"], 1)
            self.assertEqual(report["totals"]["processable_checklists"], 1)
            self.assertEqual(report["totals"]["uncovered_checklists"], 0)
            self.assertEqual(report["totals"]["issues"], 0)
            step = report["steps"][0]
            self.assertEqual(step["kind"], "process_next_review_batch_return")
            self.assertEqual(step["checklist_name"], "NEXT_REVIEW_BATCH_MANIFEST.csv")
            self.assertIn("process_next_review_batch_return.py", step["command"])
            self.assertIn("<returned_packet_root>\\2026-07-01_demo_batch", step["command"])
            self.assertIn("derived\\human_adjudication\\processed_returns\\returned_demo\\demo_ready", step["command"])


if __name__ == "__main__":
    unittest.main()
