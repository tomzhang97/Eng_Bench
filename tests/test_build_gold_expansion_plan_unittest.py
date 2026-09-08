from __future__ import annotations

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_gold_expansion_plan


class BuildGoldExpansionPlanUnittest(unittest.TestCase):
    def test_logical_source_groups_distinguish_visualdiff_projects_from_documents(self) -> None:
        unified = [
            {
                "task": "microtext",
                "metadata": {"doc_id": "micro_doc_a"},
            },
            {
                "task": "microtext",
                "metadata": {"doc_id": "micro_doc_b"},
            },
            {
                "task": "visualdiff",
                "metadata": {"project_id": "visual_project"},
            },
        ]
        pairs = [
            {
                "pair_id": "visual_project__0001",
                "project_id": "visual_project",
                "doc_id_old": "visual_revision_a",
                "doc_id_new": "visual_revision_b",
            },
            {
                "pair_id": "visual_project__0002",
                "project_id": "visual_project",
                "doc_id_old": "visual_revision_a",
                "doc_id_new": "visual_revision_b",
            },
        ]

        groups = build_gold_expansion_plan.logical_source_group_ids(unified, pairs)

        self.assertEqual(groups, {"micro_doc_a", "micro_doc_b", "visual_project"})


if __name__ == "__main__":
    unittest.main()
