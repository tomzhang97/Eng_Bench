from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import audit_agreement_sample_readiness as audit


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def reference(identifier: str, doc_id: str, task: str) -> dict[str, str]:
    return {
        "id": identifier,
        "doc_id": doc_id,
        "source_doc_ids": doc_id,
        "split": "test",
        "task": task,
        "stratum": f"test|{task}|sample",
        "reference_evidence_json": json.dumps(
            [{"bbox": [1, 2, 10, 12], "image_index": 0}]
        ),
        "primary_evidence_path": f"evidence/{identifier}.png",
        "page_path": "",
        "old_page_path": "",
        "new_page_path": "",
        "answer_correct": "",
        "corrected_answer": "",
        "bbox_correct": "",
        "corrected_evidence_json": "",
        "accept_reject": "",
        "ambiguity": "",
        "rights_concern": "",
        "notes": "",
    }


def completed(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    result.update(
        {
            "answer_correct": "yes",
            "bbox_correct": "yes",
            "accept_reject": "accept",
            "ambiguity": "no",
            "rights_concern": "no",
        }
    )
    return result


class AgreementSampleReadinessTest(unittest.TestCase):
    def test_reports_rights_and_reviewer_completion_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                reference("q_ready", "ready_doc", "microtext"),
                reference("q_blocked", "blocked_doc", "visualdiff"),
            ]
            (root / "evidence").mkdir()
            for row in rows:
                (root / row["primary_evidence_path"]).write_bytes(b"png")
            (root / "eng_bench.jsonl").write_text(
                "".join(json.dumps({"id": row["id"]}) + "\n" for row in rows),
                encoding="utf-8",
            )
            reference_path = root / "sample_reference.csv"
            reviewer_a_path = root / "reviewer_a.csv"
            reviewer_b_path = root / "reviewer_b.csv"
            write_csv(reference_path, rows)
            write_csv(reviewer_a_path, [completed(rows[0]), rows[1]])
            write_csv(reviewer_b_path, rows)
            provenance_path = root / "provenance.json"
            provenance_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"doc_id": "ready_doc", "release_ready": True},
                            {
                                "doc_id": "blocked_doc",
                                "release_ready": False,
                                "blocker": "license_evidence_missing",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report, row_results = audit.build_report(
                root,
                reference_path=reference_path,
                reviewer_a_path=reviewer_a_path,
                reviewer_b_path=reviewer_b_path,
                provenance_path=provenance_path,
                expected_rows=2,
            )

            self.assertFalse(report["release_sample_ready"])
            self.assertFalse(report["sample_review_ready"])
            self.assertTrue(report["input_contract_valid"])
            self.assertTrue(report["review_completion_pending"])
            self.assertEqual(report["release_ready_rows"], 1)
            self.assertEqual(report["reviewer_a_complete_rows"], 1)
            self.assertEqual(report["reviewer_b_complete_rows"], 0)
            self.assertIn("sample_contains_non_release_ready_sources", report["issues"])
            self.assertEqual(row_results[1]["source_blockers"], "license_evidence_missing")

    def test_distinguishes_release_safe_sample_from_incomplete_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                reference("q_micro", "ready_micro", "microtext"),
                reference("q_visual", "ready_visual", "visualdiff"),
            ]
            (root / "evidence").mkdir()
            for row in rows:
                (root / row["primary_evidence_path"]).write_bytes(b"png")
            (root / "eng_bench.jsonl").write_text(
                "".join(json.dumps({"id": row["id"]}) + "\n" for row in rows),
                encoding="utf-8",
            )
            reference_path = root / "sample_reference.csv"
            reviewer_a_path = root / "reviewer_a.csv"
            reviewer_b_path = root / "reviewer_b.csv"
            write_csv(reference_path, rows)
            write_csv(reviewer_a_path, rows)
            write_csv(reviewer_b_path, rows)
            provenance_path = root / "provenance.json"
            provenance_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"doc_id": "ready_micro", "release_ready": True},
                            {"doc_id": "ready_visual", "release_ready": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report, _rows = audit.build_report(
                root,
                reference_path=reference_path,
                reviewer_a_path=reviewer_a_path,
                reviewer_b_path=reviewer_b_path,
                provenance_path=provenance_path,
                expected_rows=2,
            )

            self.assertTrue(report["sample_review_ready"])
            self.assertTrue(report["input_contract_valid"])
            self.assertTrue(report["review_completion_pending"])
            self.assertFalse(report["release_sample_ready"])
            self.assertEqual(report["sample_issues"], [])
            self.assertEqual(
                report["review_issues"],
                ["reviewer_a_incomplete", "reviewer_b_incomplete"],
            )


if __name__ == "__main__":
    unittest.main()
