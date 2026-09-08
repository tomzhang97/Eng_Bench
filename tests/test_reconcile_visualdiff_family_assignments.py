import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from reconcile_visualdiff_family_assignments import reconcile


class VisualDiffFamilyAssignmentReconciliationTests(unittest.TestCase):
    def test_partitions_with_reviewed_precedence(self) -> None:
        candidates = [
            {"pair_id": "p1", "project_id": "f1"},
            {"pair_id": "p2", "project_id": "f2"},
            {"pair_id": "p3", "project_id": "f3"},
        ]
        reviewed = [{"pair_id": "p1", "review_status": "accepted"}]
        assigned = [
            {"record_id": "p1", "current_assignment_status": "issued"},
            {"record_id": "p2", "current_assignment_status": "issued"},
        ]

        partitions, report = reconcile(candidates, reviewed, assigned)

        self.assertEqual([row["pair_id"] for row in partitions["reviewed"]], ["p1"])
        self.assertEqual(
            [row["pair_id"] for row in partitions["currently_assigned"]], ["p2"]
        )
        self.assertEqual([row["pair_id"] for row in partitions["unassigned"]], ["p3"])
        self.assertTrue(report["all_candidates_accounted"])
        self.assertTrue(report["partitions_disjoint"])
        self.assertTrue(report["safe_to_issue_unassigned_only"])

    def test_duplicate_candidate_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate identity"):
            reconcile([{"pair_id": "p1"}, {"record_id": "p1"}], [], [])

    def test_missing_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing row identity"):
            reconcile([{"project_id": "family"}], [], [])


if __name__ == "__main__":
    unittest.main()
