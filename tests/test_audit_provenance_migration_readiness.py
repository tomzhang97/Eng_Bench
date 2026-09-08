import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_provenance_migration_readiness as auditor
import build_provenance_replacement_plan as planner


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ProvenanceMigrationReadinessTest(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path, Path, dict]:
        image_path = root / "pages" / "doc.png"
        image_path.parent.mkdir(parents=True)
        Image.new("RGB", (40, 40), color=(255, 255, 255)).save(image_path)
        (root / "SOURCE_INVENTORY.csv").write_text(
            "doc_id,public_status\nreplacement_doc,cc_by_sa_4_0_candidate\n",
            encoding="utf-8",
        )
        write_jsonl(
            root / "manifest.jsonl",
            [{"type": "doc", "doc_id": "replacement_doc", "task": "microtext"}],
        )
        write_jsonl(
            root / "eng_bench.jsonl",
            [{"id": "active_1", "task": "microtext", "split": "dev", "answer": "OLD"}],
        )
        candidate = {
            "candidate_id": "candidate_1",
            "task": "microtext",
            "doc_id": "replacement_doc",
            "image_path": "pages/doc.png",
            "bbox": [10, 10, 20, 20],
            "category": "pin_label",
            "replacement_for_task": "microtext",
            "replacement_for_split": "dev",
            "replacement_for_category": "pin_label",
            "reserved_split": "dev",
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
        affected = [{
            "active_id": "active_1",
            "task": "microtext",
            "split": "dev",
            "category": "pin_label",
            "blocked_source_doc_ids": ["blocked_doc"],
        }]
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
        write_json(plan_path, plan)
        write_jsonl(affected_path, affected)
        write_jsonl(candidates_path, [candidate])
        return plan_path, affected_path, candidates_path, candidate

    def test_structural_readiness_does_not_imply_migration_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, _ = self.build_fixture(root)
            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
            )
            self.assertTrue(report["plan_structurally_ready"])
            self.assertFalse(report["human_review_complete"])
            self.assertFalse(report["ready_for_atomic_migration"])
            self.assertEqual(report["counts"]["outstanding_review_rows"], 1)

    def test_surplus_available_preferred_rows_do_not_fail_structural_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, _ = self.build_fixture(root)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            payload.update(
                {
                    "preferred_issued_requested": 2,
                    "preferred_issued_available": 2,
                    "preferred_issued_selected": 1,
                    "preferred_issued_retired_active_gold": 0,
                    "preferred_issued_missing": 0,
                }
            )
            write_json(plan, payload)

            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
            )

            self.assertTrue(report["plan_structurally_ready"], report["structural_issues"])

    def test_inconsistent_preferred_partition_fails_structural_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, _ = self.build_fixture(root)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            payload.update(
                {
                    "preferred_issued_requested": 2,
                    "preferred_issued_available": 1,
                    "preferred_issued_selected": 1,
                    "preferred_issued_retired_active_gold": 0,
                    "preferred_issued_missing": 0,
                }
            )
            write_json(plan, payload)

            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
            )

            self.assertFalse(report["plan_structurally_ready"])
            self.assertIn(
                "preferred_issued_summary_inconsistent",
                {issue["code"] for issue in report["structural_issues"]},
            )

    def test_all_accepted_review_rows_can_complete_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, candidate = self.build_fixture(root)
            reviewed_path = root / "reviewed.jsonl"
            write_jsonl(reviewed_path, [{**candidate, "human_review_status": "accepted"}])
            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
                reviewed_paths=[reviewed_path],
            )
            self.assertTrue(report["plan_structurally_ready"])
            self.assertTrue(report["human_review_complete"])
            self.assertTrue(report["ready_for_atomic_migration"])

    def test_legacy_human_status_does_not_override_planner_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, candidate = self.build_fixture(root)
            write_jsonl(candidates, [{**candidate, "human_review_status": "accepted"}])
            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
            )
            self.assertTrue(report["plan_structurally_ready"])
            self.assertFalse(report["human_review_complete"])
            self.assertFalse(report["ready_for_atomic_migration"])

    def test_active_underlying_candidate_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, _ = self.build_fixture(root)
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {
                        "id": "active_1",
                        "task": "microtext",
                        "split": "dev",
                        "answer": "OLD",
                        "metadata": {"item_id": "candidate_1", "item_ids": ["candidate_1"]},
                    }
                ],
            )
            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
            )
            self.assertFalse(report["plan_structurally_ready"])
            self.assertIn(
                "replacement_candidates_already_active_gold",
                {issue["code"] for issue in report["structural_issues"]},
            )

    def test_reviewed_row_with_changed_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, candidate = self.build_fixture(root)
            reviewed_path = root / "reviewed.jsonl"
            write_jsonl(
                reviewed_path,
                [
                    {
                        **candidate,
                        "bbox": [11, 10, 20, 20],
                        "human_review_status": "accepted",
                    }
                ],
            )
            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
                reviewed_paths=[reviewed_path],
            )
            self.assertTrue(report["plan_structurally_ready"])
            self.assertFalse(report["human_review_complete"])
            self.assertFalse(report["ready_for_atomic_migration"])
            self.assertEqual(report["counts"]["reviewed_metadata_mismatch_rows"], 1)
            self.assertIn(
                "reviewed_candidate_metadata_mismatch",
                {issue["code"] for issue in report["review_issues"]},
            )

    def test_duplicate_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan, affected, candidates, candidate = self.build_fixture(root)
            write_jsonl(
                root / "eng_bench.jsonl",
                [
                    {"id": "active_1", "task": "microtext", "split": "dev"},
                    {"id": "active_2", "task": "microtext", "split": "dev"},
                ],
            )
            write_jsonl(
                affected,
                [
                    {"active_id": "active_1", "task": "microtext", "split": "dev"},
                    {"active_id": "active_2", "task": "microtext", "split": "dev"},
                ],
            )
            write_jsonl(candidates, [candidate, {**candidate, "candidate_id": "candidate_2"}])
            payload = json.loads(plan.read_text(encoding="utf-8"))
            payload.update({
                "affected_gold_rows": 2,
                "selected_replacement_candidates": 2,
                "selected_unique_evidence_fingerprints": 1,
            })
            write_json(plan, payload)
            report = auditor.build_report(
                root=root,
                plan_path=plan,
                affected_path=affected,
                candidates_path=candidates,
            )
            self.assertFalse(report["plan_structurally_ready"])
            self.assertIn(
                "evidence_fingerprints_duplicate",
                {issue["code"] for issue in report["structural_issues"]},
            )


if __name__ == "__main__":
    unittest.main()
