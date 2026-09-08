import json
import tempfile
import unittest
from pathlib import Path

from tools import build_source_atomic_provenance_slice as slicer


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class BuildSourceAtomicProvenanceSliceTest(unittest.TestCase):
    def test_selects_only_exact_reviewed_contract_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_doc = "blocked_doc"
            parent_plan = root / "parent.json"
            affected_path = root / "affected.jsonl"
            candidates_path = root / "candidates.jsonl"
            reviewed_path = root / "reviewed.jsonl"
            provenance_path = root / "provenance.json"
            output_dir = root / "out"

            write_json(parent_plan, {"blocked_active_source_docs": [source_doc]})
            affected = [
                {
                    "active_id": f"q{i}",
                    "blocked_source_doc_ids": [source_doc],
                    "task": "microtext",
                    "split": "dev",
                    "category": "dimension_value",
                }
                for i in range(2)
            ]
            write_jsonl(affected_path, affected)
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {"id": f"q{i}", "task": "microtext", "split": "dev"}
                    for i in range(2)
                ],
            )
            base = {
                "task": "microtext",
                "source_candidate_id": "src",
                "reserved_split": "dev",
                "split_reservation_plan": "plan.json",
                "provenance_replacement_candidate": True,
                "provenance_replacement_date_label": "fixture",
                "replacement_for_task": "microtext",
                "replacement_for_split": "dev",
                "replacement_for_category": "dimension_value",
                "replacement_match_level": "task_split_fallback",
                "replacement_origin_phase": "current_assignment",
                "replacement_rights_check": "release_safe_status",
                "replacement_source_unit": "safe_doc",
                "replacement_evidence_fingerprint_status": "pixel_crop_sha256",
                "doc_id": "safe_doc",
                "version_id": "v1",
                "page_index": 0,
                "image_path": "image.png",
                "bbox": [0, 0, 10, 10],
                "safe_to_merge_gold": False,
                "review_status": "needs_review",
                "promotion_state": "unreviewed_provenance_replacement_candidate",
            }
            candidates = []
            reviews = []
            for i in range(3):
                candidate = {
                    **base,
                    "candidate_id": f"c{i}",
                    "split_reservation_id": f"r{i}",
                    "replacement_evidence_fingerprint": f"microtext:sha256:{i:064x}",
                }
                candidates.append(candidate)
                reviews.append(
                    {
                        **candidate,
                        "human_review_status": "accepted",
                        "review_status": "accepted",
                    }
                )
            write_jsonl(candidates_path, candidates)
            write_jsonl(reviewed_path, list(reversed(reviews)))
            write_json(
                provenance_path,
                {"documents": [{"doc_id": source_doc, "active_row_references": 2}]},
            )

            report = slicer.build_slice(
                root=root,
                parent_plan_path=parent_plan,
                affected_path=affected_path,
                candidates_path=candidates_path,
                reviewed_paths=[reviewed_path],
                source_doc=source_doc,
                output_dir=output_dir,
                date_label="fixture",
                provenance_report_path=provenance_path,
            )

            self.assertTrue(report["ready_for_migration_readiness_audit"])
            self.assertEqual(2, report["selected_reviewed_replacements"])
            selected = slicer.read_jsonl(output_dir / "source_atomic_candidates.jsonl")
            self.assertEqual(["c0", "c1"], [row["candidate_id"] for row in selected])
            plan = slicer.read_json(output_dir / "source_atomic_plan.json")
            self.assertEqual(2, plan["affected_gold_rows"])
            self.assertEqual(2, plan["selected_replacement_candidates"])
            self.assertEqual([], slicer.read_jsonl(output_dir / "source_atomic_outstanding.jsonl"))

    def test_fails_closed_when_reviewed_supply_is_short(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "parent.json", {"blocked_active_source_docs": ["blocked"]})
            affected = [
                {
                    "active_id": "q1",
                    "blocked_source_doc_ids": ["blocked"],
                    "task": "microtext",
                    "split": "dev",
                    "category": "dimension_value",
                }
            ]
            write_jsonl(root / "affected.jsonl", affected)
            write_jsonl(root / "candidates.jsonl", [])
            write_jsonl(root / "reviewed.jsonl", [])
            write_jsonl(
                root / "eng_bench.jsonl",
                [{"id": "q1", "task": "microtext", "split": "dev"}],
            )
            write_json(
                root / "provenance.json",
                {"documents": [{"doc_id": "blocked", "active_row_references": 1}]},
            )

            report = slicer.build_slice(
                root=root,
                parent_plan_path=root / "parent.json",
                affected_path=root / "affected.jsonl",
                candidates_path=root / "candidates.jsonl",
                reviewed_paths=[root / "reviewed.jsonl"],
                source_doc="blocked",
                output_dir=root / "out",
                date_label="fixture",
                provenance_report_path=root / "provenance.json",
            )

            self.assertFalse(report["ready_for_migration_readiness_audit"])
            self.assertIn(
                "reviewed_replacement_shortfall",
                {issue["code"] for issue in report["issues"]},
            )
            self.assertEqual(1, report["unfillable_replacement_rows"])

    def test_writes_exact_unreviewed_rows_needed_to_close_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_doc = "blocked"
            write_json(root / "parent.json", {"blocked_active_source_docs": [source_doc]})
            write_jsonl(
                root / "affected.jsonl",
                [
                    {
                        "active_id": f"q{i}",
                        "blocked_source_doc_ids": [source_doc],
                        "task": "microtext",
                        "split": "dev",
                        "category": "dimension_value",
                    }
                    for i in range(2)
                ],
            )
            write_jsonl(
                root / "eng_bench.jsonl",
                [{"id": f"q{i}", "task": "microtext", "split": "dev"} for i in range(2)],
            )
            candidates = []
            reviews = []
            for i in range(2):
                candidate = {
                    "candidate_id": f"c{i}",
                    "task": "microtext",
                    "replacement_for_task": "microtext",
                    "replacement_for_split": "dev",
                    "replacement_for_category": "dimension_value",
                    "replacement_evidence_fingerprint": f"microtext:sha256:{i:064x}",
                    "safe_to_merge_gold": False,
                }
                candidates.append(candidate)
                if i == 0:
                    reviews.append(
                        {
                            **candidate,
                            "human_review_status": "accepted",
                            "review_status": "accepted",
                        }
                    )
            write_jsonl(root / "candidates.jsonl", candidates)
            write_jsonl(root / "reviewed.jsonl", reviews)
            write_json(
                root / "provenance.json",
                {"documents": [{"doc_id": source_doc, "active_row_references": 2}]},
            )

            report = slicer.build_slice(
                root=root,
                parent_plan_path=root / "parent.json",
                affected_path=root / "affected.jsonl",
                candidates_path=root / "candidates.jsonl",
                reviewed_paths=[root / "reviewed.jsonl"],
                source_doc=source_doc,
                output_dir=root / "out",
                date_label="fixture",
                provenance_report_path=root / "provenance.json",
            )

            outstanding = slicer.read_jsonl(root / "out" / "source_atomic_outstanding.jsonl")
            self.assertEqual(["c1"], [row["candidate_id"] for row in outstanding])
            self.assertEqual(1, report["selected_outstanding_review_rows"])
            self.assertEqual(0, report["unfillable_replacement_rows"])
            self.assertFalse(report["ready_for_migration_readiness_audit"])


if __name__ == "__main__":
    unittest.main()
