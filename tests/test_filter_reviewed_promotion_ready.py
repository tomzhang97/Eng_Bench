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

import audit_staged_v2_capacity as staged
import filter_reviewed_promotion_ready as subject


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class FilterReviewedPromotionReadyTests(unittest.TestCase):
    def test_partitions_fatal_and_explicit_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            micro = [
                {"candidate_id": "keep", "task": "microtext", "doc_id": "doc", "page_index": 0, "bbox": [1, 2, 3, 4]},
                {"candidate_id": "fatal", "task": "microtext", "doc_id": "doc", "page_index": 0, "bbox": [5, 6, 7, 8]},
                {"candidate_id": "conflict", "task": "microtext", "doc_id": "doc", "page_index": 0, "bbox": [9, 10, 11, 12]},
            ]
            visual = [{"pair_id": "pair", "task": "visualdiff"}]
            write_jsonl(root / "micro.jsonl", micro)
            write_jsonl(root / "visual.jsonl", visual)
            issues_path = root / "issues.csv"
            with issues_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["severity", "issue", "cohort", "task", "identity", "detail"])
                writer.writeheader()
                writer.writerow(
                    {
                        "severity": "fatal",
                        "issue": "missing_reserved_split",
                        "cohort": "micro",
                        "task": "microtext",
                        "identity": staged.capacity_identity(micro[1]),
                        "detail": "",
                    }
                )
            report = subject.build_partition(
                root,
                [("micro", Path("micro.jsonl")), ("visual", Path("visual.jsonl"))],
                Path("issues.csv"),
                {"conflict"},
                Path("out"),
                "fixture",
            )
            self.assertEqual(report["counts"]["input_rows"], 4)
            self.assertEqual(report["counts"]["ready_rows"], 2)
            self.assertEqual(report["counts"]["held_rows"], 2)
            holds = [json.loads(line) for line in (root / "out/promotion_holds.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                {reason for row in holds for reason in row["hold_reasons"]},
                {"missing_reserved_split", "explicit_historical_review_conflict"},
            )

    def test_fails_on_unmatched_fatal_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "micro.jsonl", [{"candidate_id": "keep", "task": "microtext", "doc_id": "doc", "page_index": 0, "bbox": [1, 2, 3, 4]}])
            issues_path = root / "issues.csv"
            with issues_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["severity", "issue", "cohort", "task", "identity", "detail"])
                writer.writeheader()
                writer.writerow({"severity": "fatal", "issue": "x", "cohort": "micro", "task": "microtext", "identity": "missing", "detail": ""})
            with self.assertRaisesRegex(ValueError, "did not match"):
                subject.build_partition(root, [("micro", Path("micro.jsonl"))], Path("issues.csv"), set(), Path("out"), "fixture")


if __name__ == "__main__":
    unittest.main()
