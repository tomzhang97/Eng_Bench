import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_reviewed_reclamation as auditor


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class ReviewedReclamationAuditTests(unittest.TestCase):
    def test_accounts_for_active_geometry_rights_duplicate_and_ready_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active_path = root / "microtext/annotations/microtext_items.jsonl"
            write_jsonl(
                active_path,
                [
                    {
                        "item_id": "mt__active",
                        "source_candidate_id": "cand-active",
                        "doc_id": "doc-safe",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "text_gt": "A1",
                        "category": "pin_label",
                    },
                    {
                        "item_id": "mt__geometry",
                        "source_candidate_id": "cand-old",
                        "doc_id": "doc-safe",
                        "version_id": "v1",
                        "page_index": 1,
                        "bbox": [5, 6, 7, 8],
                        "text_gt": "B2",
                        "category": "pin_label",
                    },
                ],
            )
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["doc_id", "public_status"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"doc_id": "doc-safe", "public_status": "public_domain_us_federal_candidate"},
                        {"doc_id": "doc-hold", "public_status": "rights_hold"},
                    ]
                )
            evidence = root / "evidence.png"
            evidence.write_bytes(b"evidence")
            reviewed_path = root / "microtext/annotations/microtext_review_test.jsonl"
            base = {
                "version_id": "v1",
                "review_status": "accepted",
                "category": "pin_label",
                "image_path": "evidence.png",
            }
            write_jsonl(
                reviewed_path,
                [
                    {**base, "candidate_id": "cand-active", "doc_id": "doc-safe", "page_index": 0, "bbox": [1, 2, 3, 4], "target_text": "A1"},
                    {**base, "candidate_id": "cand-equivalent", "doc_id": "doc-safe", "page_index": 1, "bbox": [5, 6, 7, 8], "target_text": "B2"},
                    {**base, "candidate_id": "cand-conflict", "doc_id": "doc-safe", "page_index": 1, "bbox": [5, 6, 7, 8], "target_text": "B3"},
                    {**base, "candidate_id": "cand-hold", "doc_id": "doc-hold", "page_index": 2, "bbox": [9, 10, 11, 12], "target_text": "C3"},
                    {**base, "candidate_id": "cand-ready", "doc_id": "doc-safe", "page_index": 3, "bbox": [13, 14, 15, 16], "target_text": "D4"},
                    {**base, "candidate_id": "cand-ready", "doc_id": "doc-safe", "page_index": 4, "bbox": [17, 18, 19, 20], "target_text": "D4"},
                ],
            )
            inventory = {
                "files": [
                    {
                        "path": "microtext/annotations/microtext_review_test.jsonl",
                        "kind": "microtext",
                        "mergeable_status_rows": 6,
                    }
                ]
            }
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

            report = auditor.build_audit(root, inventory_path, root / "out", "test")

            self.assertTrue(report["all_rows_accounted"])
            self.assertEqual(report["counts"], {"input_rows": 6, "promotion_ready": 1, "nonpromotion_rows": 5})
            self.assertEqual(
                report["disposition_counts"],
                {
                    "already_active_candidate_id": 1,
                    "already_active_geometry_conflict": 1,
                    "already_active_geometry_equivalent": 1,
                    "duplicate_reviewed_candidate_id": 1,
                    "promotion_ready": 1,
                    "rights_hold": 1,
                },
            )
            self.assertEqual(len(auditor.read_jsonl(root / "out/promotion_ready_microtext.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
