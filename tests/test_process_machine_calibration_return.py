import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.process_machine_calibration_return import run_control


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class MachineCalibrationReturnControlTest(unittest.TestCase):
    def build_fixture(self, decision: str = "correct") -> tuple[Path, Path, Path, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        cohort = root / "derived/quality/cohort"
        cohort.mkdir(parents=True)
        eligible = cohort / "auto_eligible_pending_calibration.jsonl"
        rows = [
            {
                "candidate_id": f"candidate_{index:03d}",
                "reserved_split": "train",
                "machine_certification_policy_version": "1.0",
                "machine_certification_tier": "auto_gold_train",
                "machine_certification_evidence_sha256": hashlib.sha256(
                    str(index).encode()
                ).hexdigest(),
            }
            for index in range(300)
        ]
        write_jsonl(eligible, rows)
        split_plan = root / "derived/quality/split-plan.json"
        write_json(split_plan, {"valid": True, "reservations": []})
        sample_ids = [row["candidate_id"] for row in rows]
        eligibility_report = cohort / "eligibility_report.json"
        write_json(
            eligibility_report,
            {
                "policy_version": "1.0",
                "artifacts": {
                    "auto_eligible": {
                        "path": eligible.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(eligible.read_bytes()).hexdigest(),
                    }
                },
                "split_plan": split_plan.relative_to(root).as_posix(),
                "split_plan_sha256": hashlib.sha256(split_plan.read_bytes()).hexdigest(),
                "calibration_sample": {
                    "candidate_ids": sample_ids,
                    "candidate_ids_sha256": hashlib.sha256(
                        "\n".join(sample_ids).encode()
                    ).hexdigest(),
                },
            },
        )
        report_sha = hashlib.sha256(eligibility_report.read_bytes()).hexdigest()
        write_json(
            cohort / "finalization_pending_report.json",
            {
                "inputs": {
                    "eligibility_report": eligibility_report.relative_to(root).as_posix(),
                    "eligibility_report_sha256": report_sha,
                }
            },
        )
        checklist = root / "returned.csv"
        with checklist.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("candidate_id", "reviewer_decision"))
            writer.writeheader()
            for index, candidate_id in enumerate(sample_ids):
                writer.writerow(
                    {
                        "candidate_id": candidate_id,
                        "reviewer_decision": (
                            "incorrect" if decision == "incorrect" and index == 0 else decision
                        ),
                    }
                )
        return root, cohort, checklist, temp

    @staticmethod
    def passing_preview(*args, **kwargs) -> dict:
        return {
            "goal": "Gold v2.0 Global",
            "ready_for_apply": True,
            "active_gold_modified": False,
            "counts": {
                "input_rows": 300,
                "prepared_microtext_rows": 300,
                "prepared_visualdiff_rows": 0,
                "held_rows": 0,
                "combined_gold_rows": 300,
            },
            "gates": {"fixture": True},
            "hold_reasons": {},
            "interpretation": "fixture",
        }

    def test_passing_return_stages_all_rows_and_runs_preview(self) -> None:
        root, cohort, checklist, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)
        output = root / "derived/quality/output"

        report = run_control(
            root=root,
            cohort_dir=cohort,
            completed_checklist=checklist,
            output_dir=output,
            date_label="fixture",
            preview_builder=self.passing_preview,
        )

        self.assertTrue(report["ready_for_separate_hash_locked_apply"])
        self.assertTrue(report["promotion_preview_attempted"])
        self.assertEqual(300, report["finalization"]["counts"]["certified_output_rows"])
        self.assertFalse(report["active_gold_modified"])
        self.assertFalse(report["safe_to_merge_gold"])

    def test_one_incorrect_decision_fails_closed_without_preview(self) -> None:
        root, cohort, checklist, temp = self.build_fixture("incorrect")
        self.addCleanup(temp.cleanup)
        called = False

        def forbidden_preview(*args, **kwargs):
            nonlocal called
            called = True
            return self.passing_preview()

        report = run_control(
            root=root,
            cohort_dir=cohort,
            completed_checklist=checklist,
            output_dir=root / "derived/quality/output",
            date_label="fixture",
            preview_builder=forbidden_preview,
        )

        self.assertFalse(report["ready_for_separate_hash_locked_apply"])
        self.assertFalse(report["promotion_preview_attempted"])
        self.assertFalse(called)
        self.assertEqual(0, report["finalization"]["counts"]["certified_output_rows"])
        self.assertEqual("", (root / "derived/quality/output/certified_machine_rows.jsonl").read_text())

    def test_eligibility_report_hash_mismatch_fails_closed(self) -> None:
        root, cohort, checklist, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)
        report_path = cohort / "eligibility_report.json"
        report_path.write_text(report_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        report = run_control(
            root=root,
            cohort_dir=cohort,
            completed_checklist=checklist,
            output_dir=root / "derived/quality/output",
            date_label="fixture",
            preview_builder=self.passing_preview,
        )

        self.assertIn("eligibility_report_sha256_mismatch", report["issues"])
        self.assertFalse(report["promotion_preview_attempted"])
        self.assertFalse(report["ready_for_separate_hash_locked_apply"])
        self.assertEqual(0, report["finalization"]["counts"]["certified_output_rows"])

    def test_preview_exception_is_reported_and_never_authorizes_apply(self) -> None:
        root, cohort, checklist, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)

        def broken_preview(*args, **kwargs):
            raise RuntimeError("fixture failure")

        report = run_control(
            root=root,
            cohort_dir=cohort,
            completed_checklist=checklist,
            output_dir=root / "derived/quality/output",
            date_label="fixture",
            preview_builder=broken_preview,
        )

        self.assertTrue(report["promotion_preview_attempted"])
        self.assertFalse(report["ready_for_separate_hash_locked_apply"])
        self.assertTrue(
            any(issue.startswith("promotion_preview_exception:RuntimeError") for issue in report["issues"])
        )
        self.assertFalse(report["active_gold_modified"])


if __name__ == "__main__":
    unittest.main()
