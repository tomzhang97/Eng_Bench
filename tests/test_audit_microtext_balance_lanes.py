from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_microtext_balance_lanes import build_report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class AuditMicrotextBalanceLanesTest(unittest.TestCase):
    def test_deduplicates_lanes_and_selects_minimum_floor_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "active.jsonl"
            strict = root / "strict.jsonl"
            primary = root / "primary.json"
            future = root / "future.jsonl"
            capacity = root / "capacity.json"
            precal = root / "precal.json"
            write_jsonl(
                active,
                [
                    {"item_id": "active_pin", "category": "pin_label", "split": "test"},
                    {"item_id": "active_dim", "category": "dimension_value", "split": "test"},
                ],
            )
            write_jsonl(
                strict,
                [
                    {"candidate_id": "machine_component", "category": "component_value", "reserved_split": "train"},
                    {"candidate_id": "active_dim", "category": "dimension_value", "reserved_split": "train"},
                ],
            )
            primary.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"task": "microtext", "candidate_id": "machine_component", "category": "component_value", "primary_index": 1},
                            {"task": "microtext", "candidate_id": "primary_equipment", "category": "equipment_tag", "primary_index": 2, "reserved_split": "test"},
                            {"task": "visualdiff", "candidate_id": "not_microtext", "category": "layout", "primary_index": 3},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            write_jsonl(
                future,
                [
                    {"candidate_id": "future_instrument", "category": "instrument_tag", "reserved_split": "dev"},
                    {"candidate_id": "primary_equipment", "category": "equipment_tag", "reserved_split": "train"},
                ],
            )
            capacity.write_text(
                json.dumps(
                    {
                        "capacity_input_clean": True,
                        "microtext_balance_capacity": {
                            "active": {"category_counts": {"pin_label": 1, "dimension_value": 1}},
                            "policy": {
                                "category_minimums": {
                                    category: (1 if category in {"pin_label", "dimension_value", "component_value", "equipment_tag", "instrument_tag"} else 0)
                                    for category in (
                                        "component_value", "dimension_value", "equipment_tag", "instrument_tag", "pin_label",
                                        "pipe_line_tag", "process_label", "process_value", "room_label", "tolerance_value",
                                    )
                                },
                                "pin_label_share_max": 0.45,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            precal.write_text(
                json.dumps({"artifacts": {"strict_ready": strict.name}}),
                encoding="utf-8",
            )

            report, primary_priority, future_closure = build_report(
                root,
                capacity_report=capacity,
                precalibration_report=precal,
                primary_payload=primary,
                future_capacity=future,
                active_items=active,
                date_label="fixture",
            )

            self.assertEqual(1, report["lanes"]["machine_strict_ready"]["active_overlap_rows"])
            self.assertEqual(1, report["lanes"]["primary_assignment"]["active_or_machine_overlap_rows"])
            self.assertEqual(1, report["lanes"]["future_capacity"]["prior_lane_overlap_rows"])
            self.assertEqual(
                1,
                report["human_work_reduction"][
                    "existing_primary_rows_reclaimed_after_calibration"
                ],
            )
            self.assertEqual(
                0,
                report["human_work_reduction"]["existing_primary_rows_already_active_gold"],
            )
            self.assertEqual([2], [row["primary_index"] for row in primary_priority])
            self.assertEqual(["future_instrument"], [row["candidate_id"] for row in future_closure])
            self.assertTrue(report["projections"]["after_future_floor_closure"]["passes_balance"])
            self.assertEqual("PASS", report["status"])
            self.assertFalse(report["active_gold_modified"])


if __name__ == "__main__":
    unittest.main()
