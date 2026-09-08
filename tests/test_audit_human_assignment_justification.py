import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_human_assignment_justification import (
    build_report,
    human_assignment_reasons,
    materialize_human_owned_cohorts,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class HumanAssignmentJustificationTests(unittest.TestCase):
    def test_reason_mapping_is_conservative(self) -> None:
        row = {
            "candidate_id": "a",
            "task": "microtext",
            "reserved_split": "test",
            "category": "equipment_tag",
            "machine_certification_reasons": [
                "evaluation_split_requires_human_review",
                "semantic_category_requires_human_review",
                "independent_ocr_text_mismatch",
            ],
        }
        self.assertEqual(
            human_assignment_reasons(row),
            [
                "evaluation_truth",
                "semantic_engineering_category",
                "ambiguous_or_clipped_evidence",
            ],
        )

    def test_visualdiff_is_never_machine_semantic_gold(self) -> None:
        self.assertEqual(
            human_assignment_reasons(
                {
                    "pair_id": "vd_a",
                    "reserved_split": "train",
                    "machine_certification_reasons": ["visualdiff_requires_human_review"],
                }
            ),
            ["unresolved_visualdiff_semantics"],
        )

    def test_materializes_full_rows_with_audited_reason_and_replacement_flag(self) -> None:
        human_rows = [
            {"candidate_id": "current", "proposed_text": "A"},
            {"candidate_id": "future", "proposed_text": "B"},
            {"candidate_id": "recovered", "proposed_text": "C"},
        ]
        ledger = [
            {
                "identity": "current",
                "origin": "current",
                "primary_reason": "evaluation_truth",
                "human_assignment_reasons": ["evaluation_truth"],
                "machine_preflight_status": "eligibility_preflight_complete",
                "provenance_replacement": True,
            },
            {
                "identity": "future",
                "origin": "future",
                "primary_reason": "semantic_engineering_category",
                "human_assignment_reasons": ["semantic_engineering_category"],
                "machine_preflight_status": "eligibility_preflight_complete",
                "provenance_replacement": False,
            },
        ]

        cohorts, issues = materialize_human_owned_cohorts(human_rows, ledger)

        self.assertEqual(issues, [])
        self.assertEqual([row["candidate_id"] for row in cohorts["current"]], ["current"])
        self.assertEqual([row["candidate_id"] for row in cohorts["future"]], ["future"])
        self.assertTrue(cohorts["current"][0]["provenance_replacement"])
        self.assertEqual(
            cohorts["future"][0]["human_assignment_primary_reason"],
            "semantic_engineering_category",
        )
        self.assertFalse(cohorts["future"][0]["safe_to_merge_gold"])

    def test_report_removes_recovered_rows_and_covers_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human = root / "human.jsonl"
            recovered = root / "recovered.jsonl"
            provenance = root / "provenance.jsonl"
            responsibility = root / "responsibility.json"
            rows = [
                {
                    "candidate_id": "eval",
                    "reserved_split": "dev",
                    "category": "pin_label",
                    "machine_certification_reasons": ["evaluation_split_requires_human_review"],
                },
                {
                    "candidate_id": "semantic",
                    "reserved_split": "train",
                    "category": "equipment_tag",
                    "machine_certification_reasons": ["semantic_category_requires_human_review"],
                },
                {
                    "candidate_id": "recovered",
                    "task": "visualdiff",
                    "reserved_split": "test",
                    "machine_certification_reasons": ["visualdiff_requires_human_review"],
                },
            ]
            write_jsonl(human, rows)
            write_jsonl(recovered, [rows[-1]])
            write_jsonl(provenance, [rows[0]])
            write_json(
                responsibility,
                {
                    "human_owned": {
                        "human_required_rows": 2,
                        "formal_agreement_reviewer_actions_remaining": 4,
                    },
                    "machine_owned": {"calibration_rows_required_once": 3},
                },
            )
            report, ledger = build_report(
                root=root,
                human_required_path=human,
                recovered_path=recovered,
                provenance_candidates_path=provenance,
                responsibility_report_path=responsibility,
                date_label="fixture",
            )
            self.assertTrue(report["valid"], report)
            self.assertEqual(len(ledger), 2)
            self.assertEqual(report["counts"]["machine_recovered_rows_removed"], 1)
            self.assertEqual(report["counts"]["provenance_replacement_rows"], 1)
            self.assertEqual(report["counts"]["independent_calibration_actions"], 3)
            self.assertEqual(report["counts"]["independent_agreement_actions"], 4)

    def test_missing_provenance_identity_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "human.jsonl", [])
            write_jsonl(root / "recovered.jsonl", [])
            write_jsonl(
                root / "provenance.jsonl",
                [{"candidate_id": "missing", "reserved_split": "test"}],
            )
            write_json(
                root / "responsibility.json",
                {
                    "human_owned": {
                        "human_required_rows": 0,
                        "formal_agreement_reviewer_actions_remaining": 0,
                    },
                    "machine_owned": {"calibration_rows_required_once": 0},
                },
            )
            report, _ = build_report(
                root=root,
                human_required_path=Path("human.jsonl"),
                recovered_path=Path("recovered.jsonl"),
                provenance_candidates_path=Path("provenance.jsonl"),
                responsibility_report_path=Path("responsibility.json"),
                date_label="fixture",
            )
            self.assertFalse(report["valid"])
            self.assertTrue(
                any("provenance_candidates_not_in_current_human_ledger" in issue for issue in report["issues"])
            )


if __name__ == "__main__":
    unittest.main()
