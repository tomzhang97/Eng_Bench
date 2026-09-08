import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_unmerged_reviewed_rows import audit


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class AuditUnmergedReviewedRowsExtraTest(unittest.TestCase):
    def test_extra_inputs_use_human_status_and_report_unmerged_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            image = root / "derived" / "pages" / "doc_a.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            microtext = root / "derived" / "processed" / "new_microtext_reviewed.jsonl"
            visualdiff = root / "derived" / "processed" / "new_visualdiff_reviewed.jsonl"
            write_jsonl(
                microtext,
                [
                    {
                        "candidate_id": "mt_1",
                        "doc_id": "doc_a",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                        "review_status": "accepted",
                        "image_path": "derived/pages/doc_a.png",
                    }
                ],
            )
            write_jsonl(
                visualdiff,
                [
                    {
                        "pair_id": "vd_1",
                        "project_id": "project_a",
                        "page_old": 0,
                        "page_new": 0,
                        "bbox_old": [1, 2, 3, 4],
                        "bbox_new": [5, 6, 7, 8],
                        "review_status": "needs_review",
                        "human_review_status": "edit",
                        "image_old": "derived/pages/doc_a.png",
                        "image_new": "derived/pages/doc_a.png",
                    }
                ],
            )

            report = audit(root, [microtext], [visualdiff])

            self.assertEqual(2, report["totals"]["mergeable_review_rows"])
            self.assertEqual(2, report["totals"]["unmerged_mergeable"])
            self.assertEqual(0, report["totals"]["blocked_missing_evidence"])
            self.assertEqual(
                "derived/processed/new_visualdiff_reviewed.jsonl",
                report["extra_inputs"]["visualdiff"][0],
            )


if __name__ == "__main__":
    unittest.main()
