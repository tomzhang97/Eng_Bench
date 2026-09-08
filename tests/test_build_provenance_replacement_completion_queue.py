from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_provenance_replacement_completion_queue as builder


class ProvenanceReplacementCompletionQueueTest(unittest.TestCase):
    def test_partitions_current_primary_from_remaining_evidence_ready_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "page.png"
            Image.new("RGB", (100, 100), "white").save(image)
            replacements = [
                {
                    "candidate_id": "covered",
                    "replacement_for_task": "microtext",
                    "replacement_for_split": "dev",
                },
                {
                    "candidate_id": "remaining",
                    "replacement_for_task": "microtext",
                    "replacement_for_split": "test",
                    "replacement_source_unit": "source_b",
                },
            ]
            primary = {"rows": [{"record_id": "covered"}]}
            future = [
                {
                    "candidate_id": "remaining",
                    "doc_id": "source_b",
                    "page_index": 0,
                    "bbox": [10, 10, 30, 30],
                    "image_path": image.as_posix(),
                    "reserved_split": "test",
                    "review_status": "needs_review",
                }
            ]

            queue, report = builder.build_queue(
                root,
                replacement_rows=replacements,
                primary_payload=primary,
                future_rows=future,
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["covered_by_current_primary"], 1)
            self.assertEqual(report["remaining_review_rows"], 1)
            self.assertEqual(queue[0]["candidate_id"], "remaining")
            self.assertFalse(queue[0]["safe_to_merge_gold"])

    def test_counts_valid_specialist_and_exact_evidence_alias_coverage(self) -> None:
        replacements = [
            {"candidate_id": "direct"},
            {"candidate_id": "specialist"},
            {"candidate_id": "alias"},
        ]
        primary = {
            "rows": [
                {"record_id": "direct"},
                {"record_id": "alias_reviewer"},
            ],
            "specialist": {
                "visualdiff_english": [{"record_id": "specialist"}],
            },
            "provenance_replacement_coverage": {
                "direct_primary": ["direct"],
                "specialist": ["specialist"],
                "exact_evidence_alias": ["alias"],
                "missing": [],
            },
            "provenance_replacement_aliases": [
                {
                    "record_id": "alias",
                    "review_via_record_id": "alias_reviewer",
                    "evidence_sha256": "abc123",
                    "coverage_basis": "exact_rendered_evidence_sha256",
                    "safe_to_merge_gold": False,
                }
            ],
        }

        queue, report = builder.build_queue(
            Path("."),
            replacement_rows=replacements,
            primary_payload=primary,
            future_rows=[],
        )

        self.assertTrue(report["valid"])
        self.assertEqual(queue, [])
        self.assertEqual(report["covered_by_current_primary"], 3)
        self.assertEqual(
            report["coverage_by_mode"],
            {"direct_primary": 1, "specialist": 1, "exact_evidence_alias": 1},
        )

    def test_rejects_alias_without_a_primary_review_link(self) -> None:
        primary = {
            "rows": [],
            "provenance_replacement_coverage": {
                "direct_primary": [],
                "specialist": [],
                "exact_evidence_alias": ["alias"],
                "missing": [],
            },
            "provenance_replacement_aliases": [
                {
                    "record_id": "alias",
                    "review_via_record_id": "missing_reviewer",
                    "evidence_sha256": "abc123",
                    "coverage_basis": "exact_rendered_evidence_sha256",
                    "safe_to_merge_gold": False,
                }
            ],
        }

        _, report = builder.build_queue(
            Path("."),
            replacement_rows=[{"candidate_id": "alias"}],
            primary_payload=primary,
            future_rows=[],
        )

        self.assertFalse(report["valid"])
        self.assertIn("declared_alias_coverage_missing_valid_review_links", report["issues"])

    def test_rebased_contract_counts_actual_primary_rows_not_old_declaration(self) -> None:
        primary = {
            "rows": [
                {"record_id": "old-contract-row"},
                {"record_id": "new-contract-row"},
            ],
            "provenance_replacement_coverage": {
                "direct_primary": ["old-contract-row"],
                "specialist": [],
                "exact_evidence_alias": [],
                "missing": [],
            },
        }

        queue, report = builder.build_queue(
            Path("."),
            replacement_rows=[{"candidate_id": "new-contract-row"}],
            primary_payload=primary,
            future_rows=[],
        )

        self.assertTrue(report["valid"], report["issues"])
        self.assertEqual(queue, [])
        self.assertEqual(report["coverage_by_mode"]["direct_primary"], 1)

    def test_materializes_target_task_and_split_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "page.png"
            Image.new("RGB", (100, 100), "white").save(image)
            queue, report = builder.build_queue(
                root,
                replacement_rows=[
                    {
                        "candidate_id": "remaining",
                        "replacement_target_task": "microtext",
                        "replacement_target_split": "dev",
                    }
                ],
                primary_payload={"rows": []},
                future_rows=[
                    {
                        "candidate_id": "remaining",
                        "doc_id": "source",
                        "page_index": 0,
                        "bbox": [10, 10, 30, 30],
                        "image_path": image.as_posix(),
                    }
                ],
            )

            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual(queue[0]["task"], "microtext")
            self.assertEqual(queue[0]["reserved_split"], "dev")
            self.assertEqual(queue[0]["replacement_for_split"], "dev")


if __name__ == "__main__":
    unittest.main()
