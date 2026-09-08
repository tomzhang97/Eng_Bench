import json
import tempfile
import unittest
from pathlib import Path

from tools.prioritize_visualdiff_assignment_families import (
    has_substantive_human_decision,
    select_priority_overlay,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class PrioritizeVisualDiffAssignmentFamiliesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "visualdiff" / "annotations").mkdir(parents=True)
        for name in ("old.png", "new.png"):
            (self.root / name).write_bytes(b"evidence")
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
            [{"pair_id": "vdiff__active__v1__to__v2__001"}],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assignment_row(
        self,
        family: str,
        suffix: str,
        source: str,
        *,
        workbook: str = "",
    ) -> dict:
        return {
            "task": "visualdiff",
            "pair_id": f"vdiff__{family}__v1__to__v2__{suffix}",
            "project_id": f"vdiff__{family}__v1__to__v2",
            "source_candidate_id": source,
            "image_old": "old.png",
            "image_new": "new.png",
            "bbox_old": [0, 0, 10, 10],
            "bbox_new": [0, 0, 10, 10],
            "reserved_split": "train",
            "assignment_workbook": workbook,
            "review_status": "needs_review",
        }

    def test_selects_distinct_diverse_families_and_excludes_active(self) -> None:
        assignment = self.root / "assignment.jsonl"
        rows = [
            self.assignment_row("active", "001", "source_active"),
            self.assignment_row("alpha", "001", "source_a", workbook="PRIMARY.xlsx"),
            self.assignment_row("alpha", "002", "source_a", workbook="PRIMARY.xlsx"),
            self.assignment_row("beta", "001", "source_a"),
            self.assignment_row("beta", "002", "source_a"),
            self.assignment_row("gamma", "001", "source_b"),
            self.assignment_row("gamma", "002", "source_b"),
            self.assignment_row("delta", "001", "source_c"),
            self.assignment_row("delta", "002", "source_c"),
        ]
        write_jsonl(assignment, rows)
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_review_enriched.jsonl",
            [
                {
                    **rows[5],
                    "description": "A visible connector is added.",
                    "change_type": "addition",
                    "old_text": "",
                    "new_text": "J4",
                    "machine_qa_status": "visualdiff_queue_audit_pass",
                }
            ],
        )

        selected, report = select_priority_overlay(
            self.root,
            assignment,
            target_new_families=3,
            rows_per_family=2,
            max_families_per_source_candidate=1,
            date_label="test",
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["selected_new_families"], 3)
        self.assertEqual(len(selected), 6)
        families = {row["visualdiff_family_id"] for row in selected}
        self.assertNotIn("vdiff__active__v1", families)
        self.assertEqual(len(families), 3)
        self.assertEqual(report["selected_source_candidates"], 3)
        self.assertEqual(selected[0]["assignment_workbook"], "PRIMARY.xlsx")

    def test_prefers_enriched_described_row_within_family(self) -> None:
        assignment = self.root / "assignment.jsonl"
        rows = [
            self.assignment_row("alpha", "001", "source_a"),
            self.assignment_row("alpha", "002", "source_a"),
        ]
        write_jsonl(assignment, rows)
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_review_curated.jsonl",
            [
                {
                    **rows[1],
                    "description": "The resistor value changes from 10K to 20K.",
                    "change_type": "value",
                    "old_text": "10K",
                    "new_text": "20K",
                    "machine_visual_qa_status": "selected_for_human_review",
                }
            ],
        )

        selected, report = select_priority_overlay(
            self.root,
            assignment,
            target_new_families=1,
            rows_per_family=1,
            date_label="test",
        )

        self.assertTrue(report["valid"])
        self.assertEqual(selected[0]["pair_id"], rows[1]["pair_id"])
        self.assertIn("resistor value", selected[0]["description"])
        self.assertFalse(selected[0]["safe_to_merge_gold"])

    def test_excludes_rows_with_existing_human_decisions(self) -> None:
        assignment = self.root / "assignment.jsonl"
        rows = [
            self.assignment_row("alpha", "001", "source_a"),
            self.assignment_row("alpha", "002", "source_a"),
        ]
        write_jsonl(assignment, rows)
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_review_human.jsonl",
            [
                {
                    **rows[0],
                    "human_review_status": "edit",
                    "human_description": "Existing human decision.",
                }
            ],
        )

        selected, report = select_priority_overlay(
            self.root,
            assignment,
            target_new_families=1,
            rows_per_family=1,
            date_label="test",
        )

        self.assertTrue(has_substantive_human_decision({"human_review_status": "edit"}))
        self.assertFalse(has_substantive_human_decision({"human_review_status": "unassigned"}))
        self.assertEqual(selected[0]["pair_id"], rows[1]["pair_id"])
        self.assertEqual(report["counters"]["excluded_existing_human_decision"], 1)


if __name__ == "__main__":
    unittest.main()
