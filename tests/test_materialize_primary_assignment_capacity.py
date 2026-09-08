import unittest

from tools.materialize_primary_assignment_capacity import build_assignment


class MaterializePrimaryAssignmentCapacityTests(unittest.TestCase):
    def test_combines_primary_and_specialist_without_gold_safety(self) -> None:
        payload = {
            "rows": [
                {
                    "record_id": "micro-main",
                    "primary_index": 1,
                    "reserved_split": "dev",
                    "source_group": "doc-main",
                    "engineering_required": False,
                }
            ],
            "specialist": {
                "visualdiff_english": [
                    {
                        "record_id": "visual-specialist",
                        "specialist_index": 1,
                        "task": "visualdiff_english",
                        "reserved_split": "test",
                        "source_group": "family-a",
                        "type_required": True,
                    }
                ],
                "microtext_balance": [
                    {
                        "record_id": "micro-specialist",
                        "specialist_index": 1,
                        "task": "microtext_balance",
                        "reserved_split": "train",
                        "source_group": "doc-specialist",
                    }
                ],
            },
            "counts": {"total_human_actions": 3},
        }
        rows, report = build_assignment(
            payload,
            [{"candidate_id": "micro-main", "doc_id": "doc-main", "bbox": [1, 2, 3, 4]}],
            [{"pair_id": "visual-specialist", "project_id": "family-a"}],
            [{"candidate_id": "micro-specialist", "doc_id": "doc-specialist", "bbox": [5, 6, 7, 8]}],
            date_label="fixture",
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["role_counts"], {"primary": 1, "specialist": 2})
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in rows))
        self.assertEqual(rows[1]["type_required"], True)

    def test_missing_specialist_source_fails_closed(self) -> None:
        payload = {
            "rows": [],
            "specialist": {
                "visualdiff_english": [
                    {
                        "record_id": "missing",
                        "specialist_index": 1,
                        "task": "visualdiff_english",
                    }
                ],
                "microtext_balance": [],
            },
            "counts": {"total_human_actions": 1},
        }
        rows, report = build_assignment(
            payload,
            [],
            [],
            [],
            date_label="fixture",
        )
        self.assertEqual(rows, [])
        self.assertFalse(report["valid"])
        self.assertIn("specialist_visual_source_missing", {issue["reason"] for issue in report["issues"]})


if __name__ == "__main__":
    unittest.main()
