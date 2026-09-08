from __future__ import annotations

import unittest

from tools.audit_agreement_rebuild_capacity import summarize_capacity


class AgreementRebuildCapacityTest(unittest.TestCase):
    def test_task_quotas_fail_closed_when_visualdiff_is_short(self) -> None:
        rows = [
            {"id": f"m{index}", "task": "microtext", "split": "test", "metadata": {"category": "pin_label"}}
            for index in range(5)
        ]
        rows.extend(
            {"id": f"v{index}", "task": "visualdiff", "split": "dev", "metadata": {"change_type": ["text"]}}
            for index in range(2)
        )
        report = summarize_capacity(rows, {"microtext": 4, "visualdiff": 3})
        self.assertFalse(report["exact_sample_feasible"])
        self.assertEqual(report["task_capacity"]["microtext"]["gap"], 0)
        self.assertEqual(report["task_capacity"]["visualdiff"]["gap"], 1)


if __name__ == "__main__":
    unittest.main()
