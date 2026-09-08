from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image

from tools import build_canonical_staged_capacity


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class CanonicalStagedCapacityTest(unittest.TestCase):
    def add_page(self, root: Path, doc_id: str, size: tuple[int, int]) -> None:
        path = root / "derived" / "pages_300dpi" / doc_id / "page_000.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "white").save(path)

    def test_current_assignment_wins_and_stale_rows_are_held(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_microtext = {
                "item_id": "active_mt",
                "doc_id": "doc_active",
                "page_index": 0,
                "bbox": [0, 0, 20, 20],
            }
            active_visualdiff = {"pair_id": "vdiff__active__v1__to__v2__p0000__000"}
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [active_microtext],
            )
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [active_visualdiff],
            )
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_resolved_reviewed.jsonl",
                [{"pair_id": "vdiff__terminal__v1__to__v2__p0000__000", "review_status": "reject"}],
            )

            current = root / "current.jsonl"
            write_jsonl(
                current,
                [
                    {
                        "candidate_id": "current_mt",
                        "source_candidate_id": "shared_source",
                        "doc_id": "doc_current",
                        "page_index": 0,
                        "bbox": [100, 100, 120, 120],
                    },
                    {"pair_id": "vdiff__current__v1__to__v2__p0000__000"},
                ],
            )

            cohort_one = root / "cohort_one.jsonl"
            write_jsonl(
                cohort_one,
                [
                    {
                        "candidate_id": "same_region_new_alias",
                        "doc_id": "doc_current",
                        "page_index": 0,
                        "bbox": [100, 100, 120, 120],
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "kept_mt",
                        "source_candidate_id": "shared_source",
                        "doc_id": "doc_future",
                        "page_index": 0,
                        "bbox": [200, 200, 220, 220],
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "active_alias",
                        "doc_id": "doc_active",
                        "page_index": 0,
                        "bbox": [0, 0, 20, 20],
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "active_mt",
                        "doc_id": "doc_active_alias_collision",
                        "page_index": 0,
                        "bbox": [40, 40, 60, 60],
                        "review_status": "needs_review",
                    },
                    {
                        "pair_id": "vdiff__terminal__v1__to__v2__p0000__000",
                        "review_status": "needs_review",
                    },
                ],
            )
            cohort_two = root / "cohort_two.jsonl"
            write_jsonl(
                cohort_two,
                [
                    {
                        "candidate_id": "kept_mt_duplicate",
                        "doc_id": "doc_future",
                        "page_index": 0,
                        "bbox": [200, 200, 220, 220],
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "near_kept_mt",
                        "doc_id": "doc_future",
                        "page_index": 0,
                        "bbox": [201, 201, 221, 221],
                        "review_status": "needs_review",
                    },
                    {
                        "pair_id": "vdiff__kept__v1__to__v2__p0000__000",
                        "review_status": "needs_review",
                    },
                ],
            )
            source_report = root / "source_report.json"
            source_report.write_text(
                json.dumps(
                    {
                        "cohorts": [
                            {"phase": "current", "name": "old_current", "path": "cohort_one.jsonl"},
                            {"phase": "future", "name": "old_future", "path": "cohort_two.jsonl"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            selected, held, report = build_canonical_staged_capacity.build_canonical_capacity(
                root,
                current_paths=[current],
                source_report_path=source_report,
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["current_rows"], 2)
            self.assertEqual(report["canonical_future_rows"], 2)
            self.assertEqual(
                {row.get("candidate_id") or row.get("pair_id") for row in selected},
                {"kept_mt", "vdiff__kept__v1__to__v2__p0000__000"},
            )
            reasons = Counter(row["canonical_capacity_hold_reason"] for row in held)
            self.assertEqual(reasons["current_assignment_overlap"], 1)
            self.assertEqual(reasons["active_gold_overlap"], 2)
            self.assertEqual(reasons["terminal_review_overlap"], 1)
            self.assertEqual(reasons["duplicate_staged_identity"], 1)
            self.assertEqual(reasons["near_future_capacity_region_overlap"], 1)

    def test_missing_source_cohort_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            write_jsonl(current, [])
            source_report = root / "source_report.json"
            source_report.write_text(
                json.dumps(
                    {
                        "cohorts": [
                            {"phase": "future", "name": "missing", "path": "missing.jsonl"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            _selected, _held, report = build_canonical_staged_capacity.build_canonical_capacity(
                root,
                current_paths=[current],
                source_report_path=source_report,
            )

            self.assertFalse(report["valid"])
            self.assertIn("missing_source_cohort_paths", report["issues"])

    def test_preferred_cohort_is_added_first_and_wins_duplicate_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            write_jsonl(current, [])
            preferred = root / "preferred.jsonl"
            write_jsonl(
                preferred,
                [
                    {
                        "candidate_id": "preferred_alias",
                        "doc_id": "doc_future",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "review_status": "needs_review",
                    }
                ],
            )
            existing = root / "existing.jsonl"
            write_jsonl(
                existing,
                [
                    {
                        "candidate_id": "existing_alias",
                        "doc_id": "doc_future",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "review_status": "needs_review",
                    }
                ],
            )
            source_report = root / "source_report.json"
            source_report.write_text(
                json.dumps(
                    {
                        "cohorts": [
                            {"phase": "future", "name": "existing", "path": "existing.jsonl"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            selected, held, report = build_canonical_staged_capacity.build_canonical_capacity(
                root,
                current_paths=[current],
                source_report_path=source_report,
                preferred_cohorts=[
                    {"phase": "future_preferred", "name": "preferred", "path": "preferred.jsonl"}
                ],
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["preferred_cohorts"], 1)
            self.assertEqual([row["candidate_id"] for row in selected], ["preferred_alias"])
            self.assertEqual(held[0]["candidate_id"], "existing_alias")
            self.assertEqual(
                held[0]["canonical_capacity_hold_reason"],
                "duplicate_staged_identity",
            )

    def test_payload_alias_regions_are_held_against_active_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for doc_id, size in (
                ("canonical", (400, 200)),
                ("alias_one", (200, 100)),
                ("alias_two", (800, 400)),
            ):
                self.add_page(root, doc_id, size)
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "active",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [40, 20, 80, 40],
                    }
                ],
            )
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            write_jsonl(
                current,
                [
                    {
                        "candidate_id": "current",
                        "doc_id": "alias_one",
                        "page_index": 0,
                        "bbox": [100, 40, 140, 60],
                    }
                ],
            )
            future = root / "future.jsonl"
            write_jsonl(
                future,
                [
                    {
                        "candidate_id": "duplicates_active",
                        "doc_id": "alias_two",
                        "page_index": 0,
                        "bbox": [80, 40, 160, 80],
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "duplicates_current",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [200, 80, 280, 120],
                        "review_status": "needs_review",
                    },
                    {
                        "candidate_id": "clean",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [300, 150, 340, 180],
                        "review_status": "needs_review",
                    },
                ],
            )
            source_report = root / "source_report.json"
            source_report.write_text(
                json.dumps(
                    {"cohorts": [{"phase": "future", "name": "future", "path": "future.jsonl"}]}
                ),
                encoding="utf-8",
            )
            alias_report = root / "aliases.json"
            alias_report.write_text(
                json.dumps(
                    {
                        "duplicate_groups": [
                            {
                                "canonical_doc_id": "canonical",
                                "doc_ids": ["canonical", "alias_one", "alias_two"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            selected, held, report = build_canonical_staged_capacity.build_canonical_capacity(
                root,
                current_paths=[current],
                source_report_path=source_report,
                payload_alias_report_path=alias_report,
            )

            self.assertTrue(report["valid"])
            self.assertEqual([row["candidate_id"] for row in selected], ["clean"])
            reasons = Counter(row["canonical_capacity_hold_reason"] for row in held)
            self.assertEqual(reasons["payload_alias_active_gold_overlap"], 1)
            self.assertEqual(reasons["payload_alias_current_assignment_overlap"], 1)

    def test_payload_alias_overlap_in_current_assignment_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.add_page(root, "canonical", (400, 200))
            self.add_page(root, "alias", (200, 100))
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "active",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [100, 40, 200, 80],
                    }
                ],
            )
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            write_jsonl(
                current,
                [
                    {
                        "candidate_id": "duplicate",
                        "doc_id": "alias",
                        "page_index": 0,
                        "bbox": [50, 20, 100, 40],
                    }
                ],
            )
            future = root / "future.jsonl"
            write_jsonl(future, [])
            source_report = root / "source_report.json"
            source_report.write_text(
                json.dumps(
                    {"cohorts": [{"phase": "future", "name": "future", "path": "future.jsonl"}]}
                ),
                encoding="utf-8",
            )
            alias_report = root / "aliases.json"
            alias_report.write_text(
                json.dumps(
                    {
                        "duplicate_groups": [
                            {
                                "canonical_doc_id": "canonical",
                                "doc_ids": ["canonical", "alias"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            _selected, _held, report = build_canonical_staged_capacity.build_canonical_capacity(
                root,
                current_paths=[current],
                source_report_path=source_report,
                payload_alias_report_path=alias_report,
            )

            self.assertFalse(report["valid"])
            self.assertEqual(report["current_payload_alias_active_gold_overlaps"], 1)
            self.assertIn(
                "current_assignment_payload_alias_active_gold_overlap",
                report["issues"],
            )

    def test_payload_alias_shifted_same_label_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "active",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [100, 100, 180, 140],
                        "text_gt": "MK 01 A",
                        "category": "equipment_tag",
                    }
                ],
            )
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", []
            )
            current = root / "current.jsonl"
            write_jsonl(current, [])
            future = root / "future.jsonl"
            write_jsonl(
                future,
                [
                    {
                        "candidate_id": "shifted_duplicate",
                        "doc_id": "alias",
                        "page_index": 0,
                        "bbox": [100, 300, 180, 340],
                        "proposed_text": "mk  01 a",
                        "category": "equipment_tag",
                        "review_status": "needs_review",
                    }
                ],
            )
            source_report = root / "source_report.json"
            source_report.write_text(
                json.dumps(
                    {
                        "cohorts": [
                            {"phase": "future", "name": "future", "path": "future.jsonl"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            alias_report = root / "aliases.json"
            alias_report.write_text(
                json.dumps(
                    {
                        "duplicate_groups": [
                            {
                                "canonical_doc_id": "canonical",
                                "doc_ids": ["canonical", "alias"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            selected, held, report = build_canonical_staged_capacity.build_canonical_capacity(
                root,
                current_paths=[current],
                source_report_path=source_report,
                payload_alias_report_path=alias_report,
            )

            self.assertTrue(report["valid"])
            self.assertEqual(selected, [])
            self.assertEqual(len(held), 1)
            self.assertEqual(
                held[0]["canonical_capacity_hold_reason"],
                "payload_alias_active_gold_text_category_overlap",
            )


if __name__ == "__main__":
    unittest.main()
