import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.apply_visualdiff_machine_decisions import apply_decisions


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class ApplyVisualDiffMachineDecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_path = self.root / "input.jsonl"
        self.decisions_path = self.root / "decisions.json"
        write_jsonl(
            self.input_path,
            [
                {
                    "pair_id": "pair_1",
                    "project_id": "family_1",
                    "description": "CHANGE_DESC_GT_TODO",
                    "change_type": "text_change_candidate",
                },
                {
                    "pair_id": "pair_2",
                    "project_id": "family_2",
                    "description": "CHANGE_DESC_GT_TODO",
                    "change_type": "text_change_candidate",
                },
                {
                    "pair_id": "pair_3",
                    "project_id": "family_3",
                    "description": "CHANGE_DESC_GT_TODO",
                    "change_type": "text_change_candidate",
                },
            ],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_decisions(self, payload: dict) -> None:
        self.decisions_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_applies_full_coverage_keep_hold_and_correction(self) -> None:
        self.write_decisions(
            {
                "input_sha256": sha256(self.input_path),
                "keep_rows": [1, 3],
                "holds": [{"row_number": 2, "reason": "no_visible_change"}],
                "corrections": [
                    {
                        "row_number": 1,
                        "change_type": "titleblock_change_candidate",
                        "description": "The title block year changes.",
                        "reason": "readable_titleblock_change",
                    },
                    {
                        "row_number": 3,
                        "description": "A component is removed.",
                    },
                ],
            }
        )
        kept, held, report, audit = apply_decisions(
            self.input_path, self.decisions_path
        )
        self.assertEqual([row["pair_id"] for row in kept], ["pair_1", "pair_3"])
        self.assertEqual([row["pair_id"] for row in held], ["pair_2"])
        self.assertFalse(any(row["safe_to_merge_gold"] for row in kept + held))
        self.assertEqual(kept[0]["change_type"], "titleblock_change_candidate")
        self.assertEqual(kept[0]["description_source"], "machine_visual_candidate")
        self.assertEqual(held[0]["review_status"], "machine_held")
        self.assertEqual(report["totals"]["kept_families"], 2)
        self.assertEqual(len(audit), 3)

    def test_rejects_stale_hash(self) -> None:
        self.write_decisions(
            {
                "input_sha256": "0" * 64,
                "keep_rows": [1, 2, 3],
                "holds": [],
                "corrections": [],
            }
        )
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            apply_decisions(self.input_path, self.decisions_path)

    def test_rejects_incomplete_decision_coverage(self) -> None:
        self.write_decisions(
            {
                "input_sha256": sha256(self.input_path),
                "keep_rows": [1],
                "holds": [{"row_number": 2, "reason": "hold"}],
                "corrections": [
                    {"row_number": 1, "description": "Visible text change."}
                ],
            }
        )
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            apply_decisions(self.input_path, self.decisions_path)


if __name__ == "__main__":
    unittest.main()
