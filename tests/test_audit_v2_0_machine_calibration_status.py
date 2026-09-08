from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import audit_v2_0_gate as gate


class MachineCalibrationGateStatusTests(unittest.TestCase):
    def build_fixture(self) -> tuple[Path, Path, Path, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        eligibility = root / "eligibility.json"
        eligibility.write_text(
            json.dumps(
                {
                    "active_gold_modified": False,
                    "counts": {
                        "auto_eligible_pending_calibration": 8247,
                        "auto_eligible_nonpin": 1149,
                        "auto_eligible_deferred_pin": 7098,
                        "human_required": 100,
                        "reject_or_hold": 2,
                    },
                    "calibration_sample": {"rows": 300},
                }
            ),
            encoding="utf-8",
        )
        reuse = root / "reuse.json"
        reuse.write_text(
            json.dumps(
                {
                    "active_gold_modified": False,
                    "cohort": {"eligibility_report_sha256": gate.file_sha256(eligibility)},
                    "counts": {
                        "calibration_rows": 300,
                        "reused_correct_rows": 6,
                        "remaining_rows": 294,
                        "conflict_rows": 0,
                        "eligible_rows_original": 8247,
                        "eligible_rows_already_active": 72,
                        "eligible_rows_current_pending": 8175,
                        "eligible_rows_current_pending_nonpin": 1138,
                        "eligible_rows_current_pending_pin": 7037,
                    },
                }
            ),
            encoding="utf-8",
        )
        return root, eligibility, reuse, temp

    def test_valid_reuse_reduces_remaining_human_actions(self) -> None:
        root, eligibility, reuse, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)

        status = gate.machine_certification_status(root, eligibility, reuse)

        self.assertEqual("valid", status["reuse_evidence_status"])
        self.assertEqual(6, status["calibration_rows_completed_reused"])
        self.assertEqual(294, status["calibration_rows_remaining"])
        self.assertEqual(8175, status["auto_eligible_pending_calibration"])
        self.assertEqual(72, status["eligible_rows_already_active"])
        self.assertEqual(1138, status["auto_eligible_balance_closing_nonpin"])
        self.assertEqual(7037, status["auto_eligible_deferred_pin"])
        self.assertEqual(7881, status["net_row_by_row_human_decisions_avoided"])
        self.assertEqual("pending_calibration", status["status"])

    def test_invalid_reuse_is_ignored(self) -> None:
        root, eligibility, reuse, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)
        payload = json.loads(reuse.read_text(encoding="utf-8"))
        payload["counts"]["conflict_rows"] = 1
        reuse.write_text(json.dumps(payload), encoding="utf-8")

        status = gate.machine_certification_status(root, eligibility, reuse)

        self.assertEqual("invalid", status["reuse_evidence_status"])
        self.assertEqual(0, status["calibration_rows_completed_reused"])
        self.assertEqual(300, status["calibration_rows_remaining"])
        self.assertEqual(7947, status["net_row_by_row_human_decisions_avoided"])


if __name__ == "__main__":
    unittest.main()
