import json
import tempfile
import unittest
from pathlib import Path

from tools.build_unstaged_test_ocr_reservoir import build_reservoir, resolve_globs, write_outputs


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def row(
    candidate_id: str,
    bbox: list[int],
    *,
    doc_id: str = "doc_test",
    image_path: str = "images/page.png",
    confidence: float = 0.9,
    review_status: str = "needs_review",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "doc_id": doc_id,
        "page_index": 0,
        "bbox": bbox,
        "proposed_text": candidate_id,
        "category": "pin_label",
        "image_path": image_path,
        "ocr_confidence": confidence,
        "review_status": review_status,
    }


class BuildUnstagedTestOcrReservoirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "images").mkdir(parents=True)
        (self.root / "images" / "page.png").write_bytes(b"image")
        write_jsonl(
            self.root / "microtext" / "annotations" / "microtext_items.jsonl",
            [row("active", [0, 0, 10, 10])],
        )
        self.cohort = self.root / "cohort.jsonl"
        write_jsonl(self.cohort, [row("staged", [20, 0, 30, 10])])
        self.capacity = self.root / "capacity.json"
        self.capacity.write_text(
            json.dumps(
                {
                    "capacity_input_clean": True,
                    "cohorts": [{"path": "cohort.jsonl"}],
                }
            ),
            encoding="utf-8",
        )
        self.plan = self.root / "plan.json"
        self.plan.write_text(
            json.dumps(
                {
                    "valid": True,
                    "reservations": [
                        {"task": "microtext", "unit_id": "doc_test", "split": "test"},
                        {"task": "microtext", "unit_id": "doc_train", "split": "train"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.holds = self.root / "holds.jsonl"
        write_jsonl(self.holds, [row("held", [40, 0, 50, 10]), row("held_near", [60, 0, 70, 10])])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_filters_staged_holds_non_test_missing_and_terminal_rows(self) -> None:
        input_path = self.root / "raw.jsonl"
        rows = [
            row("active_copy", [0, 0, 10, 10]),
            row("active_near", [21, 0, 31, 10]),
            row("held_copy", [40, 0, 50, 10]),
            row("held_shift", [61, 0, 71, 10]),
            row("train", [80, 0, 90, 10], doc_id="doc_train"),
            row("missing", [90, 0, 100, 10], image_path="images/missing.png"),
            row("terminal", [95, 0, 105, 10], review_status="machine_held"),
            row("fresh_low", [110, 0, 120, 10], confidence=0.5),
            row("fresh_high", [110, 0, 120, 10], confidence=0.99),
            row("fresh_near", [111, 0, 121, 10], confidence=0.8),
        ]
        write_jsonl(input_path, rows)

        selected, report = build_reservoir(
            self.root,
            capacity_report_path=self.capacity,
            split_plan_path=self.plan,
            input_paths=[input_path],
            hold_paths=[self.holds],
        )

        self.assertTrue(report["valid"])
        self.assertEqual([item["candidate_id"] for item in selected], ["fresh_high"])
        self.assertEqual(selected[0]["reserved_split"], "test")
        self.assertFalse(selected[0]["safe_to_merge_gold"])
        self.assertEqual(report["outcomes"]["active_or_staged_exact"], 1)
        self.assertEqual(report["outcomes"]["active_or_staged_near"], 1)
        self.assertEqual(report["outcomes"]["prior_hold_exact"], 1)
        self.assertEqual(report["outcomes"]["prior_hold_near"], 1)
        self.assertEqual(report["outcomes"]["non_selected_split_source"], 1)
        self.assertEqual(report["outcomes"]["missing_source_image"], 1)

    def test_can_select_multiple_reserved_splits(self) -> None:
        input_path = self.root / "raw_multi_split.jsonl"
        write_jsonl(
            input_path,
            [
                row("test_fresh", [110, 0, 120, 10]),
                row("train_fresh", [130, 0, 140, 10], doc_id="doc_train"),
            ],
        )

        selected, report = build_reservoir(
            self.root,
            capacity_report_path=self.capacity,
            split_plan_path=self.plan,
            input_paths=[input_path],
            hold_paths=[],
            included_splits={"train", "test"},
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["included_splits"], ["test", "train"])
        self.assertEqual(
            {item["candidate_id"]: item["reserved_split"] for item in selected},
            {"test_fresh": "test", "train_fresh": "train"},
        )
        self.assertEqual(report["selected_by_split"], {"test": 1, "train": 1})

    def test_resolve_globs_deduplicates_paths(self) -> None:
        write_jsonl(self.root / "inputs" / "a.jsonl", [row("a", [1, 1, 2, 2])])
        paths = resolve_globs(self.root, ["inputs/*.jsonl", "inputs/a.jsonl"])
        self.assertEqual(paths, [(self.root / "inputs" / "a.jsonl").resolve()])

    def test_write_outputs_records_review_only_status(self) -> None:
        output = self.root / "out" / "rows.jsonl"
        report_json = self.root / "out" / "report.json"
        report_md = self.root / "out" / "report.md"
        report = {
            "valid": True,
            "included_splits": ["test"],
            "input_rows": 1,
            "selected_rows": 1,
            "selected_documents": 1,
            "selected_unique_texts": 1,
            "selected_by_category": {"pin_label": 1},
            "selected_by_split": {"test": 1},
            "selected_by_document": {"doc_test": 1},
            "outcomes": {"selected": 1},
        }
        write_outputs([row("fresh", [1, 1, 2, 2])], report, output, report_json, report_md)
        self.assertTrue(output.is_file())
        self.assertTrue(json.loads(report_json.read_text(encoding="utf-8"))["valid"])
        self.assertIn("Safe to merge gold: no", report_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
