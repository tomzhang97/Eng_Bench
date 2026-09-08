import json
import tempfile
import unittest
from pathlib import Path

from tools.plan_staged_splits import build_plan


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def setup_root(root: Path, manifest: list[dict], staged: list[dict]) -> Path:
    write_jsonl(root / "manifest.jsonl", manifest)
    write_jsonl(root / "microtext/annotations/microtext_items.jsonl", [])
    write_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl", [])
    for split in ("train", "dev", "test"):
        (root / "splits").mkdir(parents=True, exist_ok=True)
        (root / f"splits/microtext_{split}.txt").write_text("", encoding="utf-8")
        (root / f"splits/visualdiff_{split}.txt").write_text("", encoding="utf-8")
    staged_path = root / "staged.jsonl"
    write_jsonl(staged_path, staged)
    report_path = root / "capacity.json"
    report_path.write_text(
        json.dumps({"cohorts": [{"name": "staged", "path": "staged.jsonl"}]}),
        encoding="utf-8",
    )
    return report_path


class PlanStagedSplitsTests(unittest.TestCase):
    def test_active_payload_alias_locks_staged_doc_to_same_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = "a" * 64
            capacity = setup_root(
                root,
                [
                    {"type": "doc", "doc_id": "active", "sha256": digest},
                    {"type": "doc", "doc_id": "alias", "sha256": digest},
                ],
                [{"candidate_id": "c1", "doc_id": "alias", "category": "room_label"}],
            )
            (root / "splits/microtext_train.txt").write_text("active\n", encoding="utf-8")
            write_jsonl(
                root / "microtext/annotations/microtext_items.jsonl",
                [{"item_id": "i1", "doc_id": "active", "split": "train"}],
            )

            report = build_plan(root, capacity, row_target=10)

            self.assertTrue(report["valid"])
            self.assertEqual(report["reservations"][0]["split"], "train")
            self.assertEqual(report["reservations"][0]["assignment_basis"], "active_family_lock")

    def test_new_one_row_units_follow_release_deficit_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = [
                {"type": "doc", "doc_id": f"doc_{index}", "sha256": f"{index:064x}"}
                for index in range(10)
            ]
            staged = [
                {"candidate_id": f"c{index}", "doc_id": f"doc_{index}", "category": "room_label"}
                for index in range(10)
            ]
            capacity = setup_root(root, manifest, staged)

            report = build_plan(root, capacity, row_target=10)

            self.assertTrue(report["valid"])
            self.assertEqual(
                report["staged"]["reserved_rows_by_split"],
                {"train": 6, "dev": 2, "test": 2},
            )

    def test_conflicting_active_alias_splits_are_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = "b" * 64
            capacity = setup_root(
                root,
                [
                    {"type": "doc", "doc_id": "left", "sha256": digest},
                    {"type": "doc", "doc_id": "right", "sha256": digest},
                    {"type": "doc", "doc_id": "staged", "sha256": digest},
                ],
                [{"candidate_id": "c1", "doc_id": "staged", "category": "room_label"}],
            )
            (root / "splits/microtext_train.txt").write_text("left\n", encoding="utf-8")
            (root / "splits/microtext_test.txt").write_text("right\n", encoding="utf-8")

            report = build_plan(root, capacity, row_target=10)

            self.assertFalse(report["valid"])
            self.assertIn("active_split_conflict", {issue["type"] for issue in report["issues"]})

    def test_prior_plan_preserves_existing_staged_unit_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capacity = setup_root(
                root,
                [{"type": "doc", "doc_id": "stable", "sha256": "c" * 64}],
                [
                    {"candidate_id": "c1", "doc_id": "stable", "category": "pin_label"},
                    {"candidate_id": "c2", "doc_id": "stable", "category": "pin_label"},
                ],
            )
            prior = root / "prior.json"
            prior.write_text(
                json.dumps(
                    {
                        "reservations": [
                            {
                                "task": "microtext",
                                "unit_id": "stable",
                                "split": "test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_plan(root, capacity, row_target=10, prior_plan=prior)

            self.assertTrue(report["valid"])
            self.assertEqual(report["reservations"][0]["split"], "test")
            self.assertEqual(report["reservations"][0]["assignment_basis"], "prior_plan_lock")
            self.assertEqual(report["prior_plan"]["unit_locks"], 1)

    def test_conflicting_prior_alias_splits_are_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = "d" * 64
            capacity = setup_root(
                root,
                [
                    {"type": "doc", "doc_id": "left", "sha256": digest},
                    {"type": "doc", "doc_id": "right", "sha256": digest},
                ],
                [
                    {"candidate_id": "c1", "doc_id": "left", "category": "pin_label"},
                    {"candidate_id": "c2", "doc_id": "right", "category": "pin_label"},
                ],
            )
            prior = root / "prior.json"
            prior.write_text(
                json.dumps(
                    {
                        "reservations": [
                            {"task": "microtext", "unit_id": "left", "split": "train"},
                            {"task": "microtext", "unit_id": "right", "split": "test"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_plan(root, capacity, row_target=10, prior_plan=prior)

            self.assertFalse(report["valid"])
            self.assertIn(
                "prior_plan_split_conflict",
                {issue["type"] for issue in report["issues"]},
            )

    def test_forced_split_overrides_unpromoted_prior_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capacity = setup_root(
                root,
                [{"type": "doc", "doc_id": "future", "sha256": "e" * 64}],
                [{"candidate_id": "c1", "doc_id": "future", "category": "room_label"}],
            )
            prior = root / "prior.json"
            prior.write_text(
                json.dumps(
                    {
                        "reservations": [
                            {"task": "microtext", "unit_id": "future", "split": "train"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_plan(
                root,
                capacity,
                row_target=10,
                prior_plan=prior,
                forced_splits={"microtext:future": "test"},
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["reservations"][0]["split"], "test")
            self.assertEqual(
                report["reservations"][0]["assignment_basis"],
                "forced_review_reservation",
            )
            self.assertIn(
                "forced_prior_plan_override",
                {issue["type"] for issue in report["issues"]},
            )

    def test_conflicting_forced_splits_in_connected_component_are_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = "f" * 64
            capacity = setup_root(
                root,
                [
                    {"type": "doc", "doc_id": "left", "sha256": digest},
                    {"type": "doc", "doc_id": "right", "sha256": digest},
                ],
                [
                    {"candidate_id": "c1", "doc_id": "left", "category": "pin_label"},
                    {"candidate_id": "c2", "doc_id": "right", "category": "pin_label"},
                ],
            )

            report = build_plan(
                root,
                capacity,
                row_target=10,
                forced_splits={
                    "microtext:left": "dev",
                    "microtext:right": "test",
                },
            )

            self.assertFalse(report["valid"])
            self.assertIn(
                "forced_split_conflict",
                {issue["type"] for issue in report["issues"]},
            )

    def test_forced_split_cannot_override_active_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capacity = setup_root(
                root,
                [{"type": "doc", "doc_id": "active", "sha256": "1" * 64}],
                [{"candidate_id": "c1", "doc_id": "active", "category": "pin_label"}],
            )
            (root / "splits/microtext_train.txt").write_text("active\n", encoding="utf-8")

            report = build_plan(
                root,
                capacity,
                row_target=10,
                forced_splits={"microtext:active": "test"},
            )

            self.assertFalse(report["valid"])
            self.assertIn(
                "forced_active_split_conflict",
                {issue["type"] for issue in report["issues"]},
            )

    def test_unknown_forced_unit_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capacity = setup_root(
                root,
                [{"type": "doc", "doc_id": "known", "sha256": "2" * 64}],
                [{"candidate_id": "c1", "doc_id": "known", "category": "pin_label"}],
            )

            report = build_plan(
                root,
                capacity,
                row_target=10,
                forced_splits={"microtext:missing": "test"},
            )

            self.assertFalse(report["valid"])
            self.assertIn(
                "forced_unit_not_found",
                {issue["type"] for issue in report["issues"]},
            )


if __name__ == "__main__":
    unittest.main()
