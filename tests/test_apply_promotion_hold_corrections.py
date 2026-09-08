from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.apply_promotion_hold_corrections import (
    apply_corrections,
    require_quality_output,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class ApplyPromotionHoldCorrectionsTest(unittest.TestCase):
    def test_cli_output_guard_rejects_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "outputs must be under derived/quality"):
                require_quality_output(root, Path("eng_bench.jsonl"))
            allowed = require_quality_output(
                root, Path("derived/quality/corrected_review.jsonl")
            )
            self.assertEqual(
                root / "derived/quality/corrected_review.jsonl",
                allowed,
            )

    def fixture(self) -> tuple[tempfile.TemporaryDirectory, Path, Path, list[str]]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        identity = "vdiff__fixture__v1__to__v2__p0000__000"
        hold = {
            "task": "visualdiff",
            "identity": identity,
            "source_path": "reviewed.jsonl",
            "reasons": [
                "unresolved_visualdiff_change_type",
                "visualdiff_description_requires_english_localization",
            ],
            "row": {
                "pair_id": identity,
                "project_id": "vdiff__fixture__v1__to__v2",
                "reserved_split": "test",
                "human_review_status": "edit",
                "human_description": "电阻值发生变化。",
                "change_type": "schematic_change_candidate",
                "safe_to_merge_gold": False,
            },
        }
        holds = root / "holds.jsonl"
        write_jsonl(holds, [hold])
        fields = [
            "task", "identity", "reasons", "source_path", "project_id", "doc_id",
            "reserved_split", "human_status", "corrected_english_description",
            "confirmed_change_type", "reviewer_notes",
        ]
        completed = root / "completed.csv"
        with completed.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "task": "visualdiff",
                "identity": identity,
                "reasons": ";".join(hold["reasons"]),
                "source_path": "reviewed.jsonl",
                "project_id": "vdiff__fixture__v1__to__v2",
                "doc_id": "",
                "reserved_split": "test",
                "human_status": "edited",
                "corrected_english_description": "The resistor value changed from 10K to 5.1K.",
                "confirmed_change_type": "value+text",
                "reviewer_notes": "Verified against both crops.",
            })
        return temp, holds, completed, fields

    def test_applies_valid_visualdiff_corrections_non_destructively(self) -> None:
        temp, holds, completed, _ = self.fixture()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        holds_before = holds.read_bytes()
        csv_before = completed.read_bytes()
        output = root / "corrected.jsonl"
        report = root / "report.json"

        result = apply_corrections(holds, completed, output, report)

        row = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            "The resistor value changed from 10K to 5.1K.",
            row["human_description"],
        )
        self.assertEqual(["value", "text"], row["human_change_type"])
        self.assertEqual("edit", row["human_review_status"])
        self.assertTrue(result["source_inputs_unchanged"])
        self.assertEqual(holds_before, holds.read_bytes())
        self.assertEqual(csv_before, completed.read_bytes())

    def test_rejects_cjk_in_english_description(self) -> None:
        temp, holds, completed, fields = self.fixture()
        self.addCleanup(temp.cleanup)
        with completed.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["corrected_english_description"] = "电阻值由 10K 改为 5.1K。"
        with completed.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        root = Path(temp.name)

        with self.assertRaisesRegex(ValueError, "english_description_lacks_english_prose"):
            apply_corrections(
                holds,
                completed,
                root / "corrected.jsonl",
                root / "report.json",
            )

    def test_allows_english_description_with_verbatim_chinese_label(self) -> None:
        temp, holds, completed, fields = self.fixture()
        self.addCleanup(temp.cleanup)
        with completed.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["corrected_english_description"] = (
            "The visible switch label changed from 开 to 关."
        )
        with completed.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        root = Path(temp.name)

        apply_corrections(
            holds,
            completed,
            root / "corrected.jsonl",
            root / "report.json",
        )

        row = json.loads((root / "corrected.jsonl").read_text(encoding="utf-8"))
        self.assertEqual("The visible switch label changed from 开 to 关.", row["human_description"])

    def test_rejects_missing_semantic_type(self) -> None:
        temp, holds, completed, fields = self.fixture()
        self.addCleanup(temp.cleanup)
        with completed.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["confirmed_change_type"] = ""
        with completed.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        root = Path(temp.name)

        with self.assertRaisesRegex(ValueError, "missing_or_invalid_confirmed_change_type"):
            apply_corrections(
                holds,
                completed,
                root / "corrected.jsonl",
                root / "report.json",
            )
