from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import audit_rights_remediation_readiness as audit


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class RightsRemediationReadinessTests(unittest.TestCase):
    def fixtures(self, root: Path):
        affected = [
            {"id": "old_mt", "task": "microtext", "split": "dev"},
            {"id": "old_vd", "task": "visualdiff", "split": "test"},
        ]
        selected = [
            {
                "candidate_id": "new_mt",
                "replacement_target_task": "microtext",
                "replacement_target_split": "dev",
                "replacement_reserved_split": "dev",
                "replacement_plan_status": "awaiting_human_review",
                "safe_to_merge_gold": False,
            },
            {
                "pair_id": "new_vd",
                "replacement_target_task": "visualdiff",
                "replacement_target_split": "test",
                "replacement_reserved_split": "test",
                "replacement_plan_status": "awaiting_human_review",
                "safe_to_merge_gold": False,
            },
        ]
        selected_path = root / "selected.jsonl"
        write_jsonl(selected_path, selected)
        selected_sha = hashlib.sha256(selected_path.read_bytes()).hexdigest()
        plan = {
            "blocked_active_source_docs": 2,
            "active_rows_requiring_replacement": 2,
            "review_only_replacement_rows_selected": 2,
            "residual_replacement_rows_needed": 0,
            "issues": [],
        }
        rights = {
            "valid": True,
            "input_rows": 2,
            "passing_rows": 2,
            "held_rows": 0,
            "paper_ready_documents": 2,
        }
        contract = {
            "active_gold_modified": False,
            "human_review_complete": False,
            "structurally_ready_for_human_return_promotion": True,
            "cohorts": [{"sha256": selected_sha}],
            "counts": {"rows": 2, "fatal_issue_rows": 0},
            "human_actions": {
                "microtext:decision_required": 1,
                "visualdiff:decision_required": 1,
            },
        }
        return plan, affected, selected, selected_path, rights, contract

    def test_accepts_complete_review_only_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            values = self.fixtures(Path(temp_dir))
            report = audit.build_report(
                plan=values[0],
                affected_rows=values[1],
                selected_rows=values[2],
                selected_path=values[3],
                rights_report=values[4],
                contract=values[5],
            )
            self.assertTrue(report["machine_remediation_path_ready"])
            self.assertFalse(report["safe_to_retire_blocked_active_rows"])
            self.assertEqual(report["human_decisions_remaining"], 2)
            self.assertEqual(report["issues"], [])

    def test_rejects_unsafe_flag_and_coverage_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            values = list(self.fixtures(Path(temp_dir)))
            values[2][0]["safe_to_merge_gold"] = True
            values[2][1]["replacement_target_split"] = "dev"
            write_jsonl(values[3], values[2])
            values[5]["cohorts"] = [
                {"sha256": hashlib.sha256(values[3].read_bytes()).hexdigest()}
            ]
            report = audit.build_report(
                plan=values[0],
                affected_rows=values[1],
                selected_rows=values[2],
                selected_path=values[3],
                rights_report=values[4],
                contract=values[5],
            )
            self.assertFalse(report["machine_remediation_path_ready"])
            self.assertTrue(any("coverage" in issue for issue in report["issues"]))
            self.assertTrue(any("pre-review merge" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
