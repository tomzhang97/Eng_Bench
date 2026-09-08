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

import build_review_evidence_render_plan as planner


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ReviewEvidenceRenderPlanTests(unittest.TestCase):
    def test_uses_manifest_path_and_deduplicates_fresh_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "microtext" / "docs" / "mechanical.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"pdf")
            inventory = root / "SOURCE_INVENTORY.csv"
            with inventory.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "domain", "public_status", "source_path"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "mechanical",
                        "domain": "mechanical_cad",
                        "public_status": "public_domain_candidate",
                        "source_path": "",
                    }
                )
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "mechanical",
                        "path": "microtext/docs/mechanical.pdf",
                    }
                ],
            )
            missing = "derived/pages_300dpi/mechanical/page_004.png"
            rows = [
                {
                    "candidate_id": "candidate_1",
                    "doc_id": "mechanical",
                    "page_index": 4,
                    "category": "dimension_value",
                    "image_path": missing,
                    "review_status": "needs_review",
                },
                {
                    "candidate_id": "candidate_1",
                    "doc_id": "mechanical",
                    "page_index": 4,
                    "category": "dimension_value",
                    "image_path": missing,
                    "review_status": "needs_review",
                },
                {
                    "candidate_id": "candidate_held",
                    "doc_id": "mechanical",
                    "page_index": 8,
                    "image_path": "derived/pages_300dpi/mechanical/page_008.png",
                    "review_status": "machine_held",
                },
            ]
            write_jsonl(root / "microtext" / "annotations" / "microtext_review_test.jsonl", rows)

            report = planner.build_plan(
                root,
                packet_date_label="test",
                doc_ids={"mechanical"},
            )

            self.assertTrue(report["valid"])
            self.assertEqual(1, report["totals"]["unique_fresh_rows_missing_evidence"])
            self.assertEqual(1, report["totals"]["unique_pages_to_render"])
            self.assertEqual("5", report["documents"][0]["page_spec_1based"])
            self.assertEqual(
                "microtext/docs/mechanical.pdf",
                report["documents"][0]["source_path"],
            )
            self.assertEqual(1, report["totals"]["duplicate_identity"])

    def test_formats_page_ranges_as_one_based(self) -> None:
        self.assertEqual("1-3,5,8-9", planner.format_page_spec([0, 1, 2, 4, 7, 8]))


if __name__ == "__main__":
    unittest.main()
