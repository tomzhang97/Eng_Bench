import json
import tempfile
import unittest
from pathlib import Path

from tools.recover_visualdiff_human_decisions import recover_decisions


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class RecoverVisualDiffHumanDecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("old.png", "new.png"):
            (self.root / name).write_bytes(b"evidence")
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
            [],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def row(self, suffix: str) -> dict:
        return {
            "task": "visualdiff",
            "pair_id": f"vdiff__fixture__v1__to__v2__{suffix}",
            "project_id": "vdiff__fixture__v1__to__v2",
            "image_old": "old.png",
            "image_new": "new.png",
            "bbox_old": [0, 0, 10, 10],
            "bbox_new": [0, 0, 10, 10],
            "reserved_split": "train",
            "change_type": "text_change_candidate",
        }

    def test_recovers_only_substantive_human_decisions(self) -> None:
        assignment = self.root / "assignment.jsonl"
        rows = [self.row("001"), self.row("002")]
        write_jsonl(assignment, rows)
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_review_fixture.jsonl",
            [
                {
                    **rows[0],
                    "human_review_status": "edit",
                    "human_description": "标签由 A 改为 B。",
                    "description": "The label changes from A to B.",
                }
            ],
        )

        recovered, report = recover_decisions(
            self.root,
            assignment,
            date_label="fixture",
        )

        self.assertTrue(report["valid"])
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["pair_id"], rows[0]["pair_id"])
        self.assertIn("needs_english_localization", recovered[0]["recovery_flags"])
        self.assertFalse(recovered[0]["safe_to_merge_gold"])

    def test_flags_active_and_ambiguous_rows(self) -> None:
        assignment = self.root / "assignment.jsonl"
        row = self.row("001")
        row["change_type"] = "schematic_change_candidate"
        write_jsonl(assignment, [row])
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
            [row],
        )
        write_jsonl(
            self.root / "visualdiff" / "annotations" / "visualdiff_review_fixture.jsonl",
            [
                {
                    **row,
                    "human_review_status": "edit",
                    "human_description": "Changed block.",
                    "description": "CHANGE_DESC_GT_TODO",
                }
            ],
        )

        recovered, report = recover_decisions(
            self.root,
            assignment,
            date_label="fixture",
        )

        self.assertTrue(report["valid"])
        self.assertEqual(
            set(recovered[0]["recovery_flags"]),
            {"already_active_gold", "missing_machine_description", "ambiguous_change_type"},
        )


if __name__ == "__main__":
    unittest.main()
