import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_provenance_replacement_plan as planner
from tools.preview_provenance_replacement_migration import (
    audit_candidate_reservation_coverage,
    build_preview,
    merge_reservation_records,
    remove_affected_rows,
    source_provenance_delta,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ProvenanceReplacementMigrationPreviewTests(unittest.TestCase):
    def fixture(self):
        items = [
            {"item_id": "mt_1", "split": "dev", "category": "equipment_tag"},
            {"item_id": "mt_keep", "split": "train", "category": "pin_label"},
        ]
        micro_questions = [
            {"question_id": "q_mt_1", "item_ids": ["mt_1"]},
            {"question_id": "q_mt_keep", "item_ids": ["mt_keep"]},
        ]
        pairs = [
            {"pair_id": "vd_1", "split": "test", "change_type": ["text"]},
            {"pair_id": "vd_keep", "split": "train", "change_type": ["layout"]},
        ]
        visual_questions = [
            {"question_id": "q_vd_1", "pair_id": "vd_1"},
            {"question_id": "q_vd_keep", "pair_id": "vd_keep"},
        ]
        affected = [
            {"active_id": "q_mt_1", "task": "microtext", "split": "dev", "category": "equipment_tag"},
            {"active_id": "q_vd_1", "task": "visualdiff", "split": "test", "category": "text"},
        ]
        return items, micro_questions, pairs, visual_questions, affected

    def test_exact_underlying_rows_are_removed(self) -> None:
        items, micro_questions, pairs, visual_questions, affected = self.fixture()
        result = remove_affected_rows(
            items=items,
            micro_questions=micro_questions,
            pairs=pairs,
            visual_questions=visual_questions,
            affected_rows=affected,
        )
        remaining_items, remaining_micro, remaining_pairs, remaining_visual, ledger, issues = result
        self.assertEqual(issues, [])
        self.assertEqual([row["item_id"] for row in remaining_items], ["mt_keep"])
        self.assertEqual([row["question_id"] for row in remaining_micro], ["q_mt_keep"])
        self.assertEqual([row["pair_id"] for row in remaining_pairs], ["vd_keep"])
        self.assertEqual([row["question_id"] for row in remaining_visual], ["q_vd_keep"])
        self.assertEqual({row["underlying_id"] for row in ledger}, {"mt_1", "vd_1"})

    def test_metadata_mismatch_blocks_removal(self) -> None:
        items, micro_questions, pairs, visual_questions, affected = self.fixture()
        affected[0]["split"] = "test"
        result = remove_affected_rows(
            items=items,
            micro_questions=micro_questions,
            pairs=pairs,
            visual_questions=visual_questions,
            affected_rows=affected,
        )
        self.assertIn("microtext_split_mismatch", {row["reason"] for row in result[-1]})
        self.assertIn("affected_removal_count_mismatch", {row["reason"] for row in result[-1]})

    def test_shared_underlying_row_cannot_be_retired_twice(self) -> None:
        items, micro_questions, pairs, visual_questions, affected = self.fixture()
        micro_questions.append({"question_id": "q_mt_alias", "item_ids": ["mt_1"]})
        affected.append(
            {"active_id": "q_mt_alias", "task": "microtext", "split": "dev", "category": "equipment_tag"}
        )
        result = remove_affected_rows(
            items=items,
            micro_questions=micro_questions,
            pairs=pairs,
            visual_questions=visual_questions,
            affected_rows=affected,
        )
        self.assertIn("microtext_item_removed_twice", {row["reason"] for row in result[-1]})

    def test_multiple_split_plans_merge_without_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.json"
            second = root / "second.json"
            write_json(
                first,
                {
                    "valid": True,
                    "reservations": [
                        {
                            "task": "microtext",
                            "unit_id": "doc_a",
                            "split": "dev",
                            "reservation_id": "reserve-a",
                        }
                    ],
                },
            )
            write_json(
                second,
                {
                    "valid": True,
                    "reservations": [
                        {
                            "task": "microtext",
                            "unit_id": "doc_b",
                            "split": "test",
                            "reservation_id": "reserve-b",
                        }
                    ],
                },
            )
            reservations, issues, summaries = merge_reservation_records([first, second])
            self.assertEqual(issues, [])
            self.assertEqual(len(reservations), 2)
            self.assertEqual(len(summaries), 2)
            candidates = [
                {
                    "candidate_id": "candidate-a",
                    "task": "microtext",
                    "doc_id": "doc_a",
                    "reserved_split": "dev",
                    "split_reservation_id": "reserve-a",
                    "split_reservation_plan": first.as_posix(),
                },
                {
                    "candidate_id": "candidate-b",
                    "task": "microtext",
                    "doc_id": "doc_b",
                    "reserved_split": "test",
                    "split_reservation_id": "reserve-b",
                    "split_reservation_plan": second.as_posix(),
                },
            ]
            coverage = audit_candidate_reservation_coverage(
                root=root,
                candidate_rows=candidates,
                reservations=reservations,
                split_plan_paths=[first, second],
            )
            self.assertTrue(coverage["complete"], coverage)
            self.assertEqual(coverage["covered_rows"], 2)

    def test_conflicting_split_plans_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.json"
            second = root / "second.json"
            for path, split, reservation_id in (
                (first, "dev", "reserve-a"),
                (second, "test", "reserve-b"),
            ):
                write_json(
                    path,
                    {
                        "valid": True,
                        "reservations": [
                            {
                                "task": "microtext",
                                "unit_id": "doc_a",
                                "split": split,
                                "reservation_id": reservation_id,
                            }
                        ],
                    },
                )
            reservations, issues, _ = merge_reservation_records([first, second])
            self.assertEqual(len(reservations), 1)
            self.assertTrue(any("split_reservation_plan_conflict" in issue for issue in issues))

    def test_source_atomic_provenance_allows_only_existing_blockers(self) -> None:
        delta = source_provenance_delta(
            source_atomic=True,
            active_resolution_issues=[],
            preview_resolution_issues=[],
            active_source_issues={
                "retired": ["rights_blocked:license_evidence_missing"],
                "remaining": ["rights_blocked:license_evidence_missing"],
            },
            preview_source_issues={
                "remaining": ["rights_blocked:license_evidence_missing"]
            },
        )

        self.assertTrue(delta["passes"])
        self.assertEqual(
            {"retired": ["rights_blocked:license_evidence_missing"]},
            delta["retired_source_issues"],
        )

    def test_source_atomic_provenance_rejects_new_blocker(self) -> None:
        delta = source_provenance_delta(
            source_atomic=True,
            active_resolution_issues=[],
            preview_resolution_issues=[],
            active_source_issues={"remaining": ["old_issue"]},
            preview_source_issues={
                "remaining": ["old_issue"],
                "replacement": ["new_issue"],
            },
        )

        self.assertFalse(delta["passes"])
        self.assertEqual({"replacement": ["new_issue"]}, delta["new_source_issues"])

    def test_complete_migration_still_requires_zero_source_issues(self) -> None:
        delta = source_provenance_delta(
            source_atomic=False,
            active_resolution_issues=[],
            preview_resolution_issues=[],
            active_source_issues={"remaining": ["old_issue"]},
            preview_source_issues={"remaining": ["old_issue"]},
        )

        self.assertFalse(delta["passes"])

    def test_complete_review_builds_row_preserving_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "derived/pages_300dpi/replacement_doc/page_0000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (60, 60), color=(255, 255, 255)).save(page)
            source = root / "docs/replacement_source.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"release-safe fixture")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "path", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "replacement_doc",
                        "path": "docs/replacement_source.pdf",
                        "source_url": "https://example.test/replacement",
                        "public_status": "public_domain_us_federal",
                    }
                )
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "replacement_doc",
                        "task": "microtext",
                        "path": "docs/replacement_source.pdf",
                        "source_url": "https://example.test/replacement",
                        "public_status": "public_domain_us_federal",
                        "sha256": source_hash,
                        "version": {"revision": "v1"},
                    }
                ],
            )
            old_item = {
                "item_id": "mt_old",
                "doc_id": "blocked_doc",
                "version_id": "v1",
                "page_index": 0,
                "page_id": "blocked_doc__v1__p0000",
                "bbox": [1, 1, 10, 10],
                "text_gt": "OLD",
                "category": "equipment_tag",
                "split": "dev",
                "review_status": "accepted",
            }
            old_question = {
                "question_id": "q_mt_old",
                "item_ids": ["mt_old"],
                "doc_id": "blocked_doc",
                "version_id": "v1",
                "query_text": "Which tag is visible?",
                "answer_text": "OLD",
                "answer_type": "span",
                "split": "dev",
                "template_family": "equipment_tag",
            }
            write_jsonl(root / "microtext/annotations/microtext_items.jsonl", [old_item])
            write_jsonl(root / "microtext/annotations/microtext_questions.jsonl", [old_question])
            write_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl", [])
            write_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [{"id": "q_mt_old", "task": "microtext", "split": "dev", "answer": "OLD"}],
            )
            split_dir = root / "splits"
            split_dir.mkdir(parents=True)
            for split in ("train", "dev", "test"):
                (split_dir / f"microtext_{split}.txt").write_text(
                    "blocked_doc\n" if split == "dev" else "", encoding="utf-8"
                )
                (split_dir / f"visualdiff_{split}.txt").write_text("", encoding="utf-8")

            candidate = {
                "candidate_id": "mtcand__replacement_doc__v1__p0000__000001",
                "task": "microtext",
                "doc_id": "replacement_doc",
                "version_id": "v1",
                "page_index": 0,
                "image_path": "derived/pages_300dpi/replacement_doc/page_0000.png",
                "bbox": [5, 5, 25, 25],
                "category": "equipment_tag",
                "proposed_text": "P-101",
                "replacement_for_task": "microtext",
                "replacement_for_split": "dev",
                "replacement_for_category": "equipment_tag",
                "replacement_match_level": "task_split_category",
                "replacement_origin_phase": "future_capacity",
                "replacement_source_unit": "replacement_doc",
                "reserved_split": "dev",
                "split_reservation_plan": "split_plan.json",
                "split_reservation_id": "fixture-reservation",
                "replacement_rights_check": "release_safe_status",
                "provenance_replacement_candidate": True,
                "provenance_replacement_date_label": "fixture",
                "replacement_evidence_fingerprint_status": "pixel_crop_sha256",
                "promotion_state": "unreviewed_provenance_replacement_candidate",
                "review_status": "needs_review",
                "safe_to_merge_gold": False,
            }
            fingerprint, status = planner.candidate_evidence_fingerprint(root, candidate)
            self.assertEqual(status, "pixel_crop_sha256")
            candidate["replacement_evidence_fingerprint"] = fingerprint
            reviewed = {
                **candidate,
                "review_status": "accepted",
                "human_review_status": "accepted",
                "target_text": "P-101",
                "promotion_state": "human_reviewed_pending_release_gates",
            }
            affected = [
                {
                    "active_id": "q_mt_old",
                    "task": "microtext",
                    "split": "dev",
                    "category": "equipment_tag",
                    "blocked_source_doc_ids": ["blocked_doc"],
                }
            ]
            plan = {
                "blocked_active_source_docs": ["blocked_doc"],
                "affected_gold_rows": 1,
                "selected_replacement_candidates": 1,
                "selected_unique_evidence_fingerprints": 1,
                "preferred_issued_requested": 1,
                "preferred_issued_selected": 1,
                "preferred_issued_missing": 0,
                "remaining_replacement_gap": 0,
                "unresolved_active_visualdiff_source_mappings": 0,
                "active_gold_rows_modified": 0,
                "safe_to_merge_gold_rows": 0,
            }
            plan_path = root / "plan.json"
            affected_path = root / "affected.jsonl"
            candidates_path = root / "candidates.jsonl"
            reviewed_path = root / "reviewed.jsonl"
            split_plan_path = root / "split_plan.json"
            write_json(plan_path, plan)
            write_jsonl(affected_path, affected)
            write_jsonl(candidates_path, [candidate])
            write_jsonl(reviewed_path, [reviewed])
            write_json(
                split_plan_path,
                {
                    "valid": True,
                    "reservations": [
                        {
                            "task": "microtext",
                            "unit_id": "replacement_doc",
                            "split": "dev",
                            "reservation_id": "fixture-reservation",
                        }
                    ],
                },
            )

            report = build_preview(
                root=root,
                plan_path=plan_path,
                affected_path=affected_path,
                candidates_path=candidates_path,
                reviewed_paths=[reviewed_path],
                split_plan_path=split_plan_path,
                output_dir=Path("derived/quality/preview"),
                date_label="fixture",
            )
            self.assertTrue(report["preview_built"])
            self.assertTrue(report["ready_for_atomic_apply"], report)
            self.assertEqual(report["counts"]["removed_rows"], 1)
            self.assertEqual(report["counts"]["inserted_rows"], 1)
            self.assertEqual(report["counts"]["preview_gold_rows"], 1)
            self.assertFalse(report["active_gold_modified"])


if __name__ == "__main__":
    unittest.main()
