from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.process_machine_calibration_workbook_return import (
    file_sha256,
    materialize_current_cohort,
    merge_completed_checklists,
    verify_remaining_contract,
)


BASE_FIELDS = [
    "sample_index",
    "candidate_id",
    "category",
    "proposed_text",
    "reviewer_decision",
    "corrected_text",
    "corrected_category",
    "reviewer_notes",
]
REUSE_FIELDS = ["reused_human_review_source"]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


class MachineCalibrationWorkbookReturnTest(unittest.TestCase):
    def test_enriched_packet_matches_frozen_remaining_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "remaining.csv"
            packet = root / "packet.csv"
            rows = [
                {
                    "sample_index": str(index),
                    "candidate_id": f"candidate-{index}",
                    "category": "component_value",
                    "proposed_text": f"{index}k",
                    "reviewer_decision": "",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                }
                for index in range(1, 3)
            ]
            write_csv(frozen, BASE_FIELDS, rows)
            enriched = [{**row, "crop_sha256": "a" * 64} for row in rows]
            write_csv(packet, BASE_FIELDS + ["crop_sha256"], enriched)

            report = verify_remaining_contract(packet, frozen)

            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual(2, report["packet_rows"])

    def test_packet_contract_rejects_missing_frozen_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "remaining.csv"
            packet = root / "packet.csv"
            rows = [
                {
                    "sample_index": str(index),
                    "candidate_id": f"candidate-{index}",
                    "category": "component_value",
                    "proposed_text": f"{index}k",
                    "reviewer_decision": "",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                }
                for index in range(1, 3)
            ]
            write_csv(frozen, BASE_FIELDS, rows)
            write_csv(packet, BASE_FIELDS, rows[:1])

            report = verify_remaining_contract(packet, frozen)

            self.assertFalse(report["valid"])
            self.assertIn("packet_remaining_row_count_mismatch", report["issues"])

    def test_merges_return_with_reused_decision_in_frozen_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefilled = root / "prefilled.csv"
            returned = root / "returned.csv"
            output = root / "combined.csv"
            rows = [
                {
                    "sample_index": "1",
                    "candidate_id": "reused",
                    "category": "component_value",
                    "proposed_text": "1k",
                    "reviewer_decision": "correct",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                    "reused_human_review_source": "review.jsonl",
                },
                {
                    "sample_index": "2",
                    "candidate_id": "new-a",
                    "category": "dimension_value",
                    "proposed_text": "2 mm",
                    "reviewer_decision": "",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                    "reused_human_review_source": "",
                },
                {
                    "sample_index": "3",
                    "candidate_id": "new-b",
                    "category": "tolerance_value",
                    "proposed_text": "+/-3%",
                    "reviewer_decision": "",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                    "reused_human_review_source": "",
                },
            ]
            write_csv(prefilled, BASE_FIELDS + REUSE_FIELDS, rows)
            write_csv(
                returned,
                BASE_FIELDS,
                [
                    {**{key: row[key] for key in BASE_FIELDS}, "reviewer_decision": "correct"}
                    for row in rows[1:]
                ],
            )

            report = merge_completed_checklists(
                prefilled_path=prefilled,
                completed_remaining_path=returned,
                output_path=output,
            )

            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual(1, report["reused_rows"])
            self.assertEqual(2, report["newly_completed_rows"])
            self.assertEqual({"correct": 3}, report["decision_counts"])

    def test_merge_fails_closed_when_return_omits_pending_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefilled = root / "prefilled.csv"
            returned = root / "returned.csv"
            row = {
                "sample_index": "1",
                "candidate_id": "pending",
                "category": "component_value",
                "proposed_text": "1k",
                "reviewer_decision": "",
                "corrected_text": "",
                "corrected_category": "",
                "reviewer_notes": "",
                "reused_human_review_source": "",
            }
            write_csv(prefilled, BASE_FIELDS + REUSE_FIELDS, [row])
            write_csv(returned, BASE_FIELDS, [])

            report = merge_completed_checklists(
                prefilled_path=prefilled,
                completed_remaining_path=returned,
                output_path=root / "combined.csv",
            )

            self.assertFalse(report["valid"])
            self.assertFalse((root / "combined.csv").exists())
            self.assertIn(
                "returned_rows_do_not_match_prefilled_pending_rows", report["issues"]
            )

    def test_materializes_hash_bound_current_strict_ready_cohort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / "derived/quality"
            strict = quality / "strict.jsonl"
            rows = [
                {
                    "candidate_id": f"candidate-{index}",
                    "category": "component_value",
                    "doc_id": "doc",
                    "reserved_split": "train",
                }
                for index in range(2)
            ]
            write_jsonl(strict, rows)
            split = quality / "split.json"
            write_json(split, {"valid": True, "reservations": []})
            original = quality / "original.json"
            write_json(
                original,
                {
                    "policy_version": "1.0",
                    "counts": {},
                    "artifacts": {},
                    "calibration_sample": {
                        "candidate_ids": ["sample-a", "sample-b"],
                        "candidate_ids_sha256": hashlib.sha256(
                            b"sample-a\nsample-b"
                        ).hexdigest(),
                    },
                    "split_plan": split.relative_to(root).as_posix(),
                    "split_plan_sha256": file_sha256(split),
                },
            )
            readiness = quality / "readiness.json"
            reuse = quality / "reuse.json"
            write_json(readiness, {"valid": True})
            write_json(reuse, {"valid": True})
            output = quality / "materialized"

            result = materialize_current_cohort(
                root=root,
                original_report_path=original,
                strict_ready_path=strict,
                readiness_report_path=readiness,
                reuse_report_path=reuse,
                output_dir=output,
                date_label="fixture",
            )

            report = json.loads(
                (output / "eligibility_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, result["rows"])
            self.assertEqual(2, report["counts"]["auto_eligible_pending_calibration"])
            self.assertEqual(
                file_sha256(output / "auto_eligible_pending_calibration.jsonl"),
                report["artifacts"]["auto_eligible"]["sha256"],
            )
            self.assertFalse(report["materialization"]["safe_to_merge_gold"])


if __name__ == "__main__":
    unittest.main()
