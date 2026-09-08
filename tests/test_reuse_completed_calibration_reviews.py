from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import reuse_completed_calibration_reviews as reuse


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


class CompletedCalibrationReviewReuseTests(unittest.TestCase):
    def build_fixture(self) -> tuple[Path, Path, Path, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        cohort = root / "derived/quality/cohort"
        checklist = cohort / "calibration/machine_certification_calibration_checklist.csv"
        rows = [
            {
                "sample_index": str(index),
                "candidate_id": f"candidate_{index}",
                "category": "dimension_value",
                "proposed_text": f"{index} mm",
                "reviewer_decision": "",
                "corrected_text": "",
                "corrected_category": "",
                "reviewer_notes": "",
            }
            for index in range(1, 4)
        ]
        write_csv(checklist, rows)
        candidate_ids = [row["candidate_id"] for row in rows]
        eligibility = {
            "artifacts": {
                "auto_eligible": {
                    "path": "derived/quality/cohort/auto_eligible_pending_calibration.jsonl",
                    "sha256": "",
                }
            },
            "calibration_sample": {
                "candidate_ids": candidate_ids,
                "candidate_ids_sha256": hashlib.sha256(
                    "\n".join(candidate_ids).encode("utf-8")
                ).hexdigest(),
            }
        }
        eligible = cohort / "auto_eligible_pending_calibration.jsonl"
        write_jsonl(
            eligible,
            [
                {
                    "candidate_id": candidate_id,
                    "reserved_split": "train",
                    "category": "dimension_value",
                }
                for candidate_id in candidate_ids
            ],
        )
        eligibility["artifacts"]["auto_eligible"]["sha256"] = hashlib.sha256(
            eligible.read_bytes()
        ).hexdigest()
        (cohort / "eligibility_report.json").write_text(
            json.dumps(eligibility), encoding="utf-8"
        )
        reviewed = root / "reviewed.jsonl"
        write_jsonl(
            root / "microtext/annotations/microtext_items.jsonl",
            [
                {
                    "source_candidate_id": "candidate_1",
                    "text_gt": "1 mm",
                    "category": "dimension_value",
                    "split": "train",
                    "review_status": "accepted",
                }
            ],
        )
        write_jsonl(
            reviewed,
            [
                {
                    "candidate_id": "candidate_1",
                    "task": "microtext",
                    "reserved_split": "train",
                    "category": "dimension_value",
                    "target_text": "1 mm",
                    "human_review_status": "accepted",
                    "primary_reviewer_decision_code": 1,
                    "human_completion_workbook_sha256": "a" * 64,
                },
                {
                    "candidate_id": "candidate_2",
                    "task": "microtext",
                    "reserved_split": "train",
                    "category": "dimension_value",
                    "target_text": "2.0 mm",
                    "human_review_status": "accepted",
                    "primary_reviewer_decision_code": 1,
                },
                {
                    "candidate_id": "candidate_3",
                    "task": "microtext",
                    "reserved_split": "train",
                    "category": "dimension_value",
                    "target_text": "3 mm",
                    "human_review_status": "rejected",
                    "primary_reviewer_decision_code": 3,
                },
            ],
        )
        return root, cohort, reviewed, temp

    def test_reuses_only_exact_explicit_accepts(self) -> None:
        root, cohort, reviewed, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)
        output = root / "derived/quality/output"

        report = reuse.build_reuse(
            root=root,
            cohort_dir=cohort,
            reviewed_jsonls=[reviewed],
            output_dir=output,
            date_label="fixture",
        )

        self.assertEqual(1, report["counts"]["reused_correct_rows"])
        self.assertEqual(2, report["counts"]["remaining_rows"])
        self.assertEqual(3, report["counts"]["eligible_rows_original"])
        self.assertEqual(1, report["counts"]["eligible_rows_already_active"])
        self.assertEqual(2, report["counts"]["eligible_rows_current_pending"])
        self.assertEqual(2, report["counts"]["eligible_rows_current_pending_nonpin"])
        self.assertEqual(0, report["counts"]["eligible_rows_current_pending_pin"])
        self.assertFalse(report["ready_for_finalization"])
        self.assertFalse(report["active_gold_modified"])
        with (output / "prefilled_calibration_checklist_300.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual("correct", rows[0]["reviewer_decision"])
        self.assertEqual("", rows[1]["reviewer_decision"])
        self.assertEqual("", rows[2]["reviewer_decision"])
        self.assertEqual("a" * 64, rows[0]["reused_human_completion_workbook_sha256"])
        self.assertEqual(
            2,
            len(
                [
                    line
                    for line in (output / "current_pending_auto_eligible.jsonl").read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            ),
        )

    def test_refuses_drifted_frozen_candidate_hash(self) -> None:
        root, cohort, reviewed, temp = self.build_fixture()
        self.addCleanup(temp.cleanup)
        report_path = cohort / "eligibility_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["calibration_sample"]["candidate_ids_sha256"] = "0" * 64
        report_path.write_text(json.dumps(report), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "candidate hash"):
            reuse.build_reuse(
                root=root,
                cohort_dir=cohort,
                reviewed_jsonls=[reviewed],
                output_dir=root / "derived/quality/output",
                date_label="fixture",
            )


if __name__ == "__main__":
    unittest.main()
