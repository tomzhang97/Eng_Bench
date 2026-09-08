from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_machine_first_responsibility import build_report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MachineFirstResponsibilityTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        eligible = root / "eligible.jsonl"
        human = root / "human.jsonl"
        checklist = root / "calibration.csv"
        contract = root / "contract.jsonl"
        write_jsonl(
            eligible,
            [
                {"candidate_id": "a", "task": "microtext", "reserved_split": "train", "machine_certification_origin_cohort": "current"},
                {"candidate_id": "b", "task": "microtext", "reserved_split": "train", "machine_certification_origin_cohort": "future"},
            ],
        )
        write_jsonl(
            human,
            [{"candidate_id": "c", "task": "visualdiff", "reserved_split": "test", "machine_certification_origin_cohort": "current"}],
        )
        checklist.write_text("candidate_id,reviewer_decision\na,\n", encoding="utf-8")
        write_jsonl(contract, [{"candidate_id": "c"}])
        eligibility = {
            "counts": {"auto_eligible_pending_calibration": 2, "human_required": 1, "reject_or_hold": 0, "input_rows": 3},
            "calibration_sample": {"rows": 1},
            "artifacts": {
                "auto_eligible": {"path": "eligible.jsonl", "sha256": sha(eligible)},
                "human_required": {"path": "human.jsonl", "sha256": sha(human)},
                "calibration_checklist": {"path": "calibration.csv", "sha256": sha(checklist)},
            },
        }
        eligibility_path = root / "eligibility.json"
        eligibility_path.write_text(json.dumps(eligibility), encoding="utf-8")
        agreement = {
            "contract": str(contract),
            "contract_sha256": sha(contract),
            "formal_detailed_agreement_required_rows": 1,
            "formal_detailed_reviewer_actions_remaining": 2,
            "primary_promotion_review_pending": 1,
            "channel_counts": {"new_independent_screen_required": 1, "pending_issued_independent_screen": 0},
        }
        agreement_path = root / "agreement.json"
        agreement_path.write_text(json.dumps(agreement), encoding="utf-8")
        return eligibility_path, agreement_path

    def test_reports_machine_savings_and_human_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            report = build_report(root, eligibility, agreement, date_label="test")
            self.assertTrue(report["structurally_valid"])
            self.assertEqual(report["machine_owned"]["eligible_after_one_calibration"], 2)
            self.assertEqual(report["machine_owned"]["human_row_by_row_decisions_avoided_net"], 1)
            self.assertEqual(report["machine_owned"]["current_primary_rows_reclaimable_after_calibration"], 1)
            self.assertEqual(report["human_owned"]["formal_agreement_reviewer_actions_remaining"], 2)

    def test_fails_on_artifact_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            (root / "eligible.jsonl").write_text("{}\n", encoding="utf-8")
            report = build_report(root, eligibility, agreement, date_label="test")
            self.assertFalse(report["structurally_valid"])
            self.assertIn("artifact_sha256_mismatch:auto_eligible", report["issues"])

    def test_rejects_pin_rows_in_nonpin_calibration_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            nonpin = root / "nonpin.jsonl"
            checklist = root / "nonpin.csv"
            write_jsonl(
                nonpin,
                [
                    {
                        "candidate_id": "a",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "pin_label",
                    }
                ],
            )
            checklist.write_text(
                "candidate_id,category,reviewer_decision\na,pin_label,\n",
                encoding="utf-8",
            )
            nonpin_report = root / "nonpin_report.json"
            nonpin_report.write_text(
                json.dumps(
                    {
                        "counts": {
                            "auto_eligible_pending_calibration": 1,
                            "auto_eligible_deferred_pin": 1,
                        },
                        "calibration_sample": {"rows": 1},
                        "artifacts": {
                            "auto_eligible": {
                                "path": "nonpin.jsonl",
                                "sha256": sha(nonpin),
                            },
                            "calibration_checklist": {
                                "path": "nonpin.csv",
                                "sha256": sha(checklist),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                nonpin_eligibility_report_path=nonpin_report,
            )

            self.assertFalse(report["structurally_valid"])
            self.assertIn("nonpin_eligible_contains_pin_label", report["issues"])
            self.assertIn("nonpin_calibration_contains_pin_label", report["issues"])

    def test_adds_verified_source_intake_funnel_savings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            funnel = root / "funnel.json"
            funnel.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "issues": [],
                        "active_gold_modified": False,
                        "raw_candidate_rows": 100,
                        "machine_row_decisions_removed": 80,
                        "human_review_rows": 20,
                        "packet_checklist_rows": 20,
                        "machine_corrected_rows": 3,
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                curation_funnel_paths=[funnel],
            )

            self.assertTrue(report["structurally_valid"])
            self.assertEqual(
                report["machine_owned"]["source_intake_row_decisions_avoided"],
                80,
            )
            self.assertEqual(
                report["machine_owned"]["total_machine_row_decisions_avoided"],
                81,
            )
            self.assertEqual(
                report["human_owned"]["source_intake_rows_remaining_human"],
                20,
            )

    def test_invalid_source_intake_funnel_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            funnel = root / "funnel.json"
            funnel.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "issues": [],
                        "active_gold_modified": False,
                        "raw_candidate_rows": 10,
                        "machine_row_decisions_removed": 8,
                        "human_review_rows": 1,
                        "packet_checklist_rows": 2,
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                curation_funnel_paths=[funnel],
            )

            self.assertFalse(report["structurally_valid"])
            self.assertTrue(
                any(
                    issue.startswith("curation_funnel_partition_mismatch:")
                    for issue in report["issues"]
                )
            )
            self.assertTrue(
                any(
                    issue.startswith("curation_funnel_packet_mismatch:")
                    for issue in report["issues"]
                )
            )

    def test_verified_human_semantics_recovery_removes_repeat_human_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            recovery_input = root / "recovery.jsonl"
            write_jsonl(recovery_input, [{"candidate_id": "c", "pair_id": "c"}])
            recovery_report = root / "recovery_report.json"
            recovery_report.write_text(
                json.dumps({
                    "valid": True,
                    "active_gold_rows_modified": 0,
                    "input": "recovery.jsonl",
                    "input_sha256": sha(recovery_input),
                    "input_rows": 1,
                    "ready_rows": 1,
                    "held_rows": 0,
                }),
                encoding="utf-8",
            )
            visual_pairs = root / "visualdiff/annotations/visualdiff_pairs.jsonl"
            write_jsonl(visual_pairs, [{"pair_id": "c"}])
            unified = root / "eng_bench.jsonl"
            write_jsonl(unified, [{"id": "c", "task": "visualdiff"}])
            promotion_report = root / "promotion_report.json"
            promotion_report.write_text(
                json.dumps({
                    "applied": True,
                    "rolled_back": False,
                    "validation": {"promoted_visualdiff_rows": 1},
                    "post_apply_validation": {
                        "strict_errors": 0,
                        "split_leaks": 0,
                        "question_leaks": 0,
                        "provenance_regressions": 0,
                    },
                    "after_hashes": {
                        "visualdiff/annotations/visualdiff_pairs.jsonl": sha(visual_pairs),
                        "eng_bench.jsonl": sha(unified),
                    },
                }),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                human_recovery_report_path=recovery_report,
                human_recovery_promotion_report_path=promotion_report,
            )

            self.assertTrue(report["structurally_valid"])
            self.assertEqual(1, report["machine_owned"]["repeat_review_actions_avoided"])
            self.assertEqual(2, report["machine_owned"]["total_machine_row_decisions_avoided"])
            self.assertEqual(0, report["human_owned"]["human_required_rows"])
            self.assertEqual(0, report["human_owned"]["current_assignment_rows_remaining_human"])

    def test_human_recovery_allows_review_only_manifest_growth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            recovery_input = root / "recovery.jsonl"
            write_jsonl(recovery_input, [{"candidate_id": "c", "pair_id": "c"}])
            recovery_report = root / "recovery_report.json"
            recovery_report.write_text(
                json.dumps({
                    "valid": True,
                    "active_gold_rows_modified": 0,
                    "input": "recovery.jsonl",
                    "input_sha256": sha(recovery_input),
                    "input_rows": 1,
                    "ready_rows": 1,
                    "held_rows": 0,
                }),
                encoding="utf-8",
            )
            visual_pairs = root / "visualdiff/annotations/visualdiff_pairs.jsonl"
            write_jsonl(visual_pairs, [{"pair_id": "c"}])
            manifest = root / "manifest.jsonl"
            write_jsonl(manifest, [{"type": "doc", "doc_id": "new-review-only-source"}])
            promotion_report = root / "promotion_report.json"
            promotion_report.write_text(
                json.dumps({
                    "applied": True,
                    "rolled_back": False,
                    "validation": {"promoted_visualdiff_rows": 1},
                    "post_apply_validation": {
                        "strict_errors": 0,
                        "split_leaks": 0,
                        "question_leaks": 0,
                        "provenance_regressions": 0,
                    },
                    "after_hashes": {
                        "visualdiff/annotations/visualdiff_pairs.jsonl": sha(visual_pairs),
                        "manifest.jsonl": "historical-manifest-hash",
                    },
                }),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                human_recovery_report_path=recovery_report,
                human_recovery_promotion_report_path=promotion_report,
            )

            self.assertTrue(report["structurally_valid"])
            self.assertEqual(1, report["machine_owned"]["repeat_review_actions_avoided"])

    def test_reconciles_current_pending_and_nonpin_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            gold = root / "eng_bench.jsonl"
            write_jsonl(gold, [{"id": "gold"}])

            pending = root / "pending.jsonl"
            already_active = root / "already_active.jsonl"
            conflicts = root / "conflicts.jsonl"
            remaining = root / "remaining.csv"
            write_jsonl(
                pending,
                [
                    {
                        "candidate_id": "a",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                        "machine_certification_origin_cohort": "current",
                    }
                ],
            )
            write_jsonl(
                already_active,
                [
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                        "machine_certification_origin_cohort": "future",
                    }
                ],
            )
            write_jsonl(conflicts, [])
            remaining.write_text("candidate_id,reviewer_decision\n", encoding="utf-8")
            active_hashes = {"eng_bench.jsonl": sha(gold)}
            reuse = {
                "schema": "eng_bench_machine_calibration_review_reuse_v1",
                "active_gold_modified": False,
                "safe_to_merge_gold": False,
                "cohort": {"eligibility_report_sha256": sha(eligibility)},
                "counts": {
                    "calibration_rows": 1,
                    "reused_correct_rows": 1,
                    "remaining_rows": 0,
                    "eligible_rows_original": 2,
                    "eligible_rows_current_pending": 1,
                    "eligible_rows_already_active": 1,
                    "eligible_rows_current_pending_nonpin": 1,
                    "eligible_rows_current_pending_pin": 0,
                    "conflict_rows": 0,
                },
                "active_file_hashes_before": active_hashes,
                "active_file_hashes_after": active_hashes,
                "artifacts": {
                    "current_pending_auto_eligible": {
                        "path": "pending.jsonl",
                        "sha256": sha(pending),
                    },
                    "already_active_auto_eligible": {
                        "path": "already_active.jsonl",
                        "sha256": sha(already_active),
                    },
                    "conflicts": {"path": "conflicts.jsonl", "sha256": sha(conflicts)},
                    "remaining_checklist": {
                        "path": "remaining.csv",
                        "sha256": sha(remaining),
                    },
                },
            }
            reuse_path = root / "reuse.json"
            reuse_path.write_text(json.dumps(reuse), encoding="utf-8")

            strict_ready = root / "strict_ready.jsonl"
            structural_holds = root / "structural_holds.jsonl"
            near_holds = root / "near_holds.jsonl"
            duplicate_holds = root / "duplicate_holds.jsonl"
            write_jsonl(strict_ready, [{"candidate_id": "a"}])
            for path in (structural_holds, near_holds, duplicate_holds):
                write_jsonl(path, [])
            readiness_artifacts = {
                "strict_ready": strict_ready,
                "structural_holds": structural_holds,
                "near_region_holds": near_holds,
                "strict_duplicate_holds": duplicate_holds,
            }
            readiness = {
                "schema": "eng_bench_nonpin_precalibration_readiness_v1",
                "precalibration_forecast_valid": True,
                "active_gold_modified": False,
                "calibration_required": True,
                "ready_for_promotion": False,
                "safe_to_merge_gold": False,
                "gates": {"strict": True},
                "inputs": {"reuse_report_sha256": sha(reuse_path)},
                "counts": {
                    "current_pending_input_rows": 1,
                    "nonpin_input_rows": 1,
                    "strict_ready_if_calibrated": 1,
                    "held_rows": 0,
                    "strict_duplicate_holds": 0,
                    "strict_duplicate_collisions_with_active_gold": 0,
                },
                "artifacts": {
                    name: path.name for name, path in readiness_artifacts.items()
                },
                "artifact_sha256": {
                    name: sha(path) for name, path in readiness_artifacts.items()
                },
            }
            readiness_path = root / "readiness.json"
            readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                calibration_reuse_report_path=reuse_path,
                nonpin_readiness_report_path=readiness_path,
            )

            self.assertTrue(report["structurally_valid"], report["issues"])
            machine = report["machine_owned"]
            self.assertEqual(1, machine["eligible_after_one_calibration"])
            self.assertEqual(1, machine["eligible_rows_already_active_through_human_review"])
            self.assertEqual(1, machine["calibration_rows_completed_reused"])
            self.assertEqual(0, machine["calibration_rows_remaining"])
            self.assertEqual(1, machine["precalibration_strict_ready_nonpin_rows"])
            self.assertEqual(1, machine["human_row_by_row_decisions_avoided_net"])

    def test_reuse_can_bind_to_frozen_nonpin_subcohort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            eligible_path = root / "eligible.jsonl"
            write_jsonl(
                eligible_path,
                [
                    {
                        "candidate_id": "a",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "pin_label",
                        "machine_certification_origin_cohort": "current",
                    },
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                        "machine_certification_origin_cohort": "future",
                    },
                ],
            )
            eligibility_payload = json.loads(eligibility.read_text(encoding="utf-8"))
            eligibility_payload["artifacts"]["auto_eligible"]["sha256"] = sha(
                eligible_path
            )
            eligibility.write_text(json.dumps(eligibility_payload), encoding="utf-8")

            nonpin_rows = root / "nonpin.jsonl"
            nonpin_checklist = root / "nonpin.csv"
            write_jsonl(
                nonpin_rows,
                [
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                        "machine_certification_origin_cohort": "future",
                    }
                ],
            )
            nonpin_checklist.write_text(
                "candidate_id,category,reviewer_decision\nb,component_value,\n",
                encoding="utf-8",
            )
            nonpin_report = root / "nonpin_report.json"
            nonpin_report.write_text(
                json.dumps(
                    {
                        "counts": {
                            "auto_eligible_pending_calibration": 1,
                            "auto_eligible_deferred_pin": 0,
                        },
                        "calibration_sample": {"rows": 1},
                        "artifacts": {
                            "auto_eligible": {
                                "path": nonpin_rows.name,
                                "sha256": sha(nonpin_rows),
                            },
                            "calibration_checklist": {
                                "path": nonpin_checklist.name,
                                "sha256": sha(nonpin_checklist),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            pending = root / "pending.jsonl"
            already_active = root / "already_active.jsonl"
            conflicts = root / "conflicts.jsonl"
            remaining = root / "remaining.csv"
            write_jsonl(
                pending,
                [
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                        "machine_certification_origin_cohort": "future",
                    }
                ],
            )
            write_jsonl(already_active, [])
            write_jsonl(conflicts, [])
            remaining.write_text(
                "candidate_id,reviewer_decision\nb,\n", encoding="utf-8"
            )
            gold = root / "eng_bench.jsonl"
            write_jsonl(gold, [{"id": "gold"}])
            reuse = root / "reuse.json"
            reuse.write_text(
                json.dumps(
                    {
                        "schema": "eng_bench_machine_calibration_review_reuse_v1",
                        "active_gold_modified": False,
                        "safe_to_merge_gold": False,
                        "cohort": {
                            "eligibility_report_sha256": sha(nonpin_report)
                        },
                        "counts": {
                            "calibration_rows": 1,
                            "reused_correct_rows": 0,
                            "remaining_rows": 1,
                            "eligible_rows_original": 1,
                            "eligible_rows_current_pending": 1,
                            "eligible_rows_already_active": 0,
                            "conflict_rows": 0,
                        },
                        "active_file_hashes_before": {
                            "eng_bench.jsonl": sha(gold)
                        },
                        "active_file_hashes_after": {
                            "eng_bench.jsonl": sha(gold)
                        },
                        "artifacts": {
                            "current_pending_auto_eligible": {
                                "path": pending.name,
                                "sha256": sha(pending),
                            },
                            "already_active_auto_eligible": {
                                "path": already_active.name,
                                "sha256": sha(already_active),
                            },
                            "conflicts": {
                                "path": conflicts.name,
                                "sha256": sha(conflicts),
                            },
                            "remaining_checklist": {
                                "path": remaining.name,
                                "sha256": sha(remaining),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                nonpin_eligibility_report_path=nonpin_report,
                calibration_reuse_report_path=reuse,
            )

            self.assertTrue(report["structurally_valid"], report["issues"])
            self.assertEqual(1, report["machine_owned"]["eligible_after_one_calibration"])
            self.assertEqual(1, report["machine_owned"]["calibration_rows_remaining"])

    def test_reuse_can_bind_to_full_cohort_with_nonpin_report_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            nonpin_rows = root / "nonpin.jsonl"
            nonpin_checklist = root / "nonpin.csv"
            write_jsonl(
                nonpin_rows,
                [
                    {
                        "candidate_id": "a",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                    },
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                    },
                ],
            )
            nonpin_checklist.write_text(
                "candidate_id,category,reviewer_decision\na,component_value,\n",
                encoding="utf-8",
            )
            nonpin_report = root / "nonpin_report.json"
            nonpin_report.write_text(
                json.dumps(
                    {
                        "counts": {
                            "auto_eligible_pending_calibration": 2,
                            "auto_eligible_deferred_pin": 0,
                        },
                        "calibration_sample": {"rows": 1},
                        "artifacts": {
                            "auto_eligible": {
                                "path": nonpin_rows.name,
                                "sha256": sha(nonpin_rows),
                            },
                            "calibration_checklist": {
                                "path": nonpin_checklist.name,
                                "sha256": sha(nonpin_checklist),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            pending = root / "pending.jsonl"
            already_active = root / "already_active.jsonl"
            conflicts = root / "conflicts.jsonl"
            remaining = root / "remaining.csv"
            write_jsonl(
                pending,
                [
                    {
                        "candidate_id": "a",
                        "task": "microtext",
                        "reserved_split": "train",
                    },
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                    },
                ],
            )
            write_jsonl(already_active, [])
            write_jsonl(conflicts, [])
            remaining.write_text(
                "candidate_id,reviewer_decision\na,\n", encoding="utf-8"
            )
            gold = root / "eng_bench.jsonl"
            write_jsonl(gold, [{"id": "gold"}])
            active_hashes = {"eng_bench.jsonl": sha(gold)}
            reuse = root / "reuse.json"
            reuse.write_text(
                json.dumps(
                    {
                        "schema": "eng_bench_machine_calibration_review_reuse_v1",
                        "active_gold_modified": False,
                        "safe_to_merge_gold": False,
                        "cohort": {
                            "eligibility_report_sha256": sha(eligibility)
                        },
                        "counts": {
                            "calibration_rows": 1,
                            "reused_correct_rows": 0,
                            "remaining_rows": 1,
                            "eligible_rows_original": 2,
                            "eligible_rows_current_pending": 2,
                            "eligible_rows_already_active": 0,
                            "conflict_rows": 0,
                        },
                        "active_file_hashes_before": active_hashes,
                        "active_file_hashes_after": active_hashes,
                        "artifacts": {
                            "current_pending_auto_eligible": {
                                "path": pending.name,
                                "sha256": sha(pending),
                            },
                            "already_active_auto_eligible": {
                                "path": already_active.name,
                                "sha256": sha(already_active),
                            },
                            "conflicts": {
                                "path": conflicts.name,
                                "sha256": sha(conflicts),
                            },
                            "remaining_checklist": {
                                "path": remaining.name,
                                "sha256": sha(remaining),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                nonpin_eligibility_report_path=nonpin_report,
                calibration_reuse_report_path=reuse,
            )

            self.assertTrue(report["structurally_valid"], report["issues"])
            self.assertEqual(
                "full", report["inputs"]["calibration_reuse"]["eligibility_scope"]
            )

    def test_readiness_accepts_active_duplicates_when_all_are_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            eligibility, agreement = self.fixture(root)
            gold = root / "eng_bench.jsonl"
            write_jsonl(gold, [{"id": "active_duplicate"}])

            pending = root / "pending.jsonl"
            already_active = root / "already_active.jsonl"
            conflicts = root / "conflicts.jsonl"
            remaining = root / "remaining.csv"
            pending_row = {
                "candidate_id": "a",
                "task": "microtext",
                "reserved_split": "train",
                "category": "component_value",
                "machine_certification_origin_cohort": "current",
            }
            write_jsonl(pending, [pending_row])
            write_jsonl(
                already_active,
                [
                    {
                        "candidate_id": "b",
                        "task": "microtext",
                        "reserved_split": "train",
                        "category": "component_value",
                        "machine_certification_origin_cohort": "future",
                    }
                ],
            )
            write_jsonl(conflicts, [])
            remaining.write_text(
                "candidate_id,reviewer_decision\n", encoding="utf-8"
            )
            active_hashes = {"eng_bench.jsonl": sha(gold)}
            reuse = {
                "schema": "eng_bench_machine_calibration_review_reuse_v1",
                "active_gold_modified": False,
                "safe_to_merge_gold": False,
                "cohort": {"eligibility_report_sha256": sha(eligibility)},
                "counts": {
                    "calibration_rows": 1,
                    "reused_correct_rows": 1,
                    "remaining_rows": 0,
                    "eligible_rows_original": 2,
                    "eligible_rows_current_pending": 1,
                    "eligible_rows_already_active": 1,
                    "eligible_rows_current_pending_nonpin": 1,
                    "eligible_rows_current_pending_pin": 0,
                    "conflict_rows": 0,
                },
                "active_file_hashes_before": active_hashes,
                "active_file_hashes_after": active_hashes,
                "artifacts": {
                    "current_pending_auto_eligible": {
                        "path": pending.name,
                        "sha256": sha(pending),
                    },
                    "already_active_auto_eligible": {
                        "path": already_active.name,
                        "sha256": sha(already_active),
                    },
                    "conflicts": {"path": conflicts.name, "sha256": sha(conflicts)},
                    "remaining_checklist": {
                        "path": remaining.name,
                        "sha256": sha(remaining),
                    },
                },
            }
            reuse_path = root / "reuse.json"
            reuse_path.write_text(json.dumps(reuse), encoding="utf-8")

            strict_ready = root / "strict_ready.jsonl"
            structural_holds = root / "structural_holds.jsonl"
            near_holds = root / "near_holds.jsonl"
            duplicate_holds = root / "duplicate_holds.jsonl"
            write_jsonl(strict_ready, [])
            write_jsonl(structural_holds, [])
            write_jsonl(near_holds, [])
            duplicate_row = dict(pending_row)
            duplicate_row["precalibration_collides_with"] = "active_duplicate"
            duplicate_row["precalibration_hold_reasons"] = [
                "strict_duplicate_qa_key"
            ]
            write_jsonl(duplicate_holds, [duplicate_row])
            readiness_artifacts = {
                "strict_ready": strict_ready,
                "structural_holds": structural_holds,
                "near_region_holds": near_holds,
                "strict_duplicate_holds": duplicate_holds,
            }
            readiness = {
                "schema": "eng_bench_nonpin_precalibration_readiness_v1",
                "precalibration_forecast_valid": True,
                "active_gold_modified": False,
                "calibration_required": True,
                "ready_for_promotion": False,
                "safe_to_merge_gold": False,
                "gates": {"strict": True},
                "inputs": {"reuse_report_sha256": sha(reuse_path)},
                "counts": {
                    "current_pending_input_rows": 1,
                    "nonpin_input_rows": 1,
                    "strict_ready_if_calibrated": 0,
                    "held_rows": 1,
                    "strict_duplicate_holds": 1,
                    "strict_duplicate_collisions_with_active_gold": 1,
                    "strict_duplicate_collisions_within_forecast": 0,
                },
                "artifacts": {
                    name: path.name for name, path in readiness_artifacts.items()
                },
                "artifact_sha256": {
                    name: sha(path) for name, path in readiness_artifacts.items()
                },
            }
            readiness_path = root / "readiness.json"
            readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

            report = build_report(
                root,
                eligibility,
                agreement,
                date_label="test",
                calibration_reuse_report_path=reuse_path,
                nonpin_readiness_report_path=readiness_path,
            )

            self.assertTrue(report["structurally_valid"], report["issues"])
            self.assertEqual(
                1,
                report["machine_owned"]["precalibration_strict_duplicate_holds"],
            )


if __name__ == "__main__":
    unittest.main()
