from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import materialize_review_evidence_plan as materializer


class MaterializeReviewEvidencePlanTests(unittest.TestCase):
    def test_materializes_only_after_evidence_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "derived" / "pages_300dpi" / "doc" / "page_004.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            queue = root / "microtext" / "annotations" / "microtext_review_source.jsonl"
            queue.parent.mkdir(parents=True)
            queue.write_text(
                json.dumps(
                    {
                        "candidate_id": "candidate_1",
                        "doc_id": "doc",
                        "page_index": 4,
                        "category": "dimension_value",
                        "image_path": "derived/pages_300dpi/doc/page_004.png",
                        "review_status": "needs_review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan = root / "plan.csv"
            with plan.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "candidate_id", "page_index_0based", "queue_path"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "doc",
                        "candidate_id": "candidate_1",
                        "page_index_0based": "4",
                        "queue_path": "microtext/annotations/microtext_review_source.jsonl",
                    }
                )

            rows, report = materializer.materialize(root.resolve(), plan.resolve())

            self.assertTrue(report["valid"])
            self.assertEqual(1, len(rows))
            self.assertFalse(rows[0]["safe_to_merge_gold"])
            self.assertEqual("evidence_repair_materialized", rows[0]["machine_qa_status"])

    def test_fails_closed_when_evidence_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "microtext" / "annotations" / "microtext_review_source.jsonl"
            queue.parent.mkdir(parents=True)
            queue.write_text(
                json.dumps(
                    {
                        "candidate_id": "candidate_1",
                        "doc_id": "doc",
                        "page_index": 4,
                        "image_path": "derived/pages_300dpi/doc/page_004.png",
                        "review_status": "needs_review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan = root / "plan.csv"
            with plan.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "candidate_id", "page_index_0based", "queue_path"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "doc",
                        "candidate_id": "candidate_1",
                        "page_index_0based": "4",
                        "queue_path": "microtext/annotations/microtext_review_source.jsonl",
                    }
                )

            rows, report = materializer.materialize(root.resolve(), plan.resolve())

            self.assertFalse(report["valid"])
            self.assertEqual([], rows)
            self.assertIn("still has missing evidence", report["issues"][0])


if __name__ == "__main__":
    unittest.main()
