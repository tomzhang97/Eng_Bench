from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import build_nonpin_machine_calibration_cohort as builder


class NonpinMachineCalibrationCohortTests(unittest.TestCase):
    def test_filters_pins_and_preserves_origin_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            source_dir.mkdir()
            eligible = source_dir / "eligible.jsonl"
            rows = [
                {
                    "candidate_id": "a",
                    "category": "component_value",
                    "machine_certification_origin_cohort": "current",
                },
                {
                    "candidate_id": "b",
                    "category": "dimension_value",
                    "machine_certification_origin_cohort": "future",
                },
                {
                    "candidate_id": "c",
                    "category": "pin_label",
                    "machine_certification_origin_cohort": "future",
                },
            ]
            builder.write_jsonl(eligible, rows)
            eligible_sha = builder.file_sha256(eligible)
            report = source_dir / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "auto_eligible": {
                                "path": "source/eligible.jsonl",
                                "sha256": eligible_sha,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            report_sha = hashlib.sha256(report.read_bytes()).hexdigest()

            result = builder.build_cohort(
                root=root,
                eligibility_report_path=report,
                output_dir=root / "output",
                expected_report_sha256=report_sha,
            )

            self.assertTrue(result["valid"])
            self.assertEqual(result["counts"]["nonpin_rows"], 2)
            self.assertEqual(result["counts"]["excluded_historically_calibrated_pin_rows"], 1)
            self.assertEqual(result["counts"]["origins"], {"current": 1, "future": 1})
            current = builder.read_jsonl(root / "output" / "nonpin_current.jsonl")
            future = builder.read_jsonl(root / "output" / "nonpin_future.jsonl")
            self.assertEqual([row["candidate_id"] for row in current], ["a"])
            self.assertEqual([row["candidate_id"] for row in future], ["b"])
            self.assertFalse(current[0]["safe_to_merge_gold"])

    def test_fails_on_report_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligible = root / "eligible.jsonl"
            builder.write_jsonl(eligible, [])
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "auto_eligible": {
                                "path": "eligible.jsonl",
                                "sha256": builder.file_sha256(eligible),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = builder.build_cohort(
                root=root,
                eligibility_report_path=report,
                output_dir=root / "output",
                expected_report_sha256="0" * 64,
            )

            self.assertFalse(result["valid"])
            self.assertIn("eligibility_report_sha256_mismatch", result["issues"])


if __name__ == "__main__":
    unittest.main()
