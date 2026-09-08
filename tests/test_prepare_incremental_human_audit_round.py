import unittest

from tools import prepare_incremental_human_audit_round as prepare


class PrepareIncrementalHumanAuditRoundTests(unittest.TestCase):
    def test_select_diverse_round_robins_groups(self):
        rows = [
            {"candidate_id": "a1", "doc_id": "a"},
            {"candidate_id": "a2", "doc_id": "a"},
            {"candidate_id": "b1", "doc_id": "b"},
            {"candidate_id": "b2", "doc_id": "b"},
        ]
        selected = prepare.select_diverse(rows, 3)
        self.assertEqual([prepare.identifier(row) for row in selected], ["a1", "b1", "a2"])

    def test_visual_family_prefers_project_id(self):
        row = {"pair_id": "vdiff__x__p0001__001", "project_id": "vdiff__x"}
        self.assertEqual(prepare.visual_family(row), "vdiff__x")

    def test_task_uses_pair_id(self):
        self.assertEqual(prepare.task({"pair_id": "p"}), "visualdiff")
        self.assertEqual(prepare.task({"candidate_id": "c"}), "microtext")


if __name__ == "__main__":
    unittest.main()
