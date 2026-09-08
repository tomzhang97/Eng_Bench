from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "process_microtext_review_workbook",
        TOOLS / "process_microtext_review_workbook.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_row() -> dict:
    return {
        "candidate_id": "c1",
        "doc_id": "doc",
        "version_id": "v1",
        "page_index": 2,
        "category": "equipment_tag",
        "proposed_text": "PT-10",
        "crop_path": "crops/c1.png",
        "page_path": "pages/doc__p0002.png",
        "image_path": "derived/pages_300dpi/doc/page_002.png",
        "text_context": "",
        "question_text": "What equipment tag is shown?",
        "review_status": "needs_review",
        "safe_to_merge_gold": False,
    }


def workbook_row(**decisions: str) -> dict[str, str]:
    row = {
        "review_index": "1",
        "candidate_id": "c1",
        "doc_id": "doc",
        "version_id": "v1",
        "page_index": "2",
        "category": "equipment_tag",
        "proposed_text": "PT-10",
        "crop_path": "crops/c1.png",
        "page_path": "pages/doc__p0002.png",
        "image_path": "derived/pages_300dpi/doc/page_002.png",
        "text_context": "",
        "question_text": "What equipment tag is shown?",
        "review_status": "",
        "corrected_text": "",
        "corrected_category": "",
        "review_notes": "",
    }
    row.update(decisions)
    return row


class ProcessMicrotextReviewWorkbookTests(unittest.TestCase):
    def test_compact_embedded_layout_restores_manifest_bindings(self):
        mod = load_module()
        matrix = [
            ["NASA review"],
            ["completion", "0 / 1"],
            [
                "#",
                "红框目标证据",
                "机器类别",
                "机器文字",
                "review_status",
                "corrected_text",
                "corrected_category",
                "review_notes（可选）",
                "candidate_id（勿改）",
            ],
            ["1", "", "equipment_tag", "PT-10", "", "", "", "", "c1"],
        ]

        rows = mod.compact_rows_from_matrix(matrix, [source_row()])

        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_id"], "c1")
        self.assertEqual(rows[0]["doc_id"], "doc")
        self.assertEqual(rows[0]["crop_path"], "crops/c1.png")
        self.assertEqual(rows[0]["review_status"], "")

    def test_compact_visible_immutable_change_fails_closed(self):
        mod = load_module()
        matrix = [
            [
                "#",
                "红框目标证据",
                "机器类别",
                "机器文字",
                "review_status",
                "corrected_text",
                "corrected_category",
                "review_notes（可选）",
                "candidate_id（勿改）",
            ],
            ["1", "", "equipment_tag", "CHANGED", "accepted", "", "", "", "c1"],
        ]
        returned = mod.compact_rows_from_matrix(matrix, [source_row()])
        assert returned is not None

        report, artifacts = mod.stage_rows(
            [source_row()],
            [source_row()],
            returned,
            date_label="test",
            workbook_path="return.xlsx",
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["counts"]["immutable_mismatches"], 1)
        self.assertEqual(artifacts["promotion"], [])

    def test_category_only_edit_is_staged_without_erasing_text(self):
        mod = load_module()
        queue = [source_row()]
        manifest = [workbook_row()]
        returned = [
            workbook_row(
                review_status="edited", corrected_category="instrument_tag"
            )
        ]

        report, artifacts = mod.stage_rows(
            queue,
            manifest,
            returned,
            date_label="test",
            workbook_path="return.xlsx",
        )

        self.assertTrue(report["complete"])
        self.assertEqual(report["counts"]["promotion_candidates"], 1)
        staged = artifacts["promotion"][0]
        self.assertEqual(staged["category"], "instrument_tag")
        self.assertEqual(staged["proposed_text"], "PT-10")
        self.assertEqual(staged["corrected_text"], "")
        self.assertEqual(staged["corrected_category"], "instrument_tag")
        self.assertEqual(staged["review_index"], "1")
        self.assertEqual(staged["human_review_return_status"], "edited")
        self.assertFalse(staged["safe_to_merge_gold"])

    def test_immutable_change_fails_closed(self):
        mod = load_module()
        queue = [source_row()]
        manifest = [workbook_row()]
        returned = [workbook_row(doc_id="changed", review_status="accepted")]

        report, artifacts = mod.stage_rows(
            queue,
            manifest,
            returned,
            date_label="test",
            workbook_path="return.xlsx",
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["counts"]["immutable_mismatches"], 1)
        self.assertEqual(artifacts["promotion"], [])
        self.assertEqual(len(artifacts["holds"]), 1)

    def test_blank_decision_remains_incomplete(self):
        mod = load_module()
        queue = [source_row()]
        manifest = [source_row()]
        returned = [workbook_row()]

        report, artifacts = mod.stage_rows(
            queue,
            manifest,
            returned,
            date_label="test",
            workbook_path="return.xlsx",
        )

        self.assertTrue(report["valid"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["incomplete_rows"], 1)
        self.assertEqual(len(artifacts["incomplete"]), 1)

    def test_manifest_shape_is_normalized_to_the_workbook_contract(self):
        mod = load_module()
        queue = [source_row()]
        manifest_row = source_row()
        manifest_row["crop_path"] = "derived/review_packs/pack/crops/c1.png"
        manifest_row["page_path"] = "derived/review_packs/pack/pages/doc__p0002.png"
        returned = [workbook_row(review_status="accepted")]

        report, artifacts = mod.stage_rows(
            queue,
            [manifest_row],
            returned,
            date_label="test",
            workbook_path="return.xlsx",
        )

        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["counts"]["immutable_mismatches"], 0)
        self.assertEqual(len(artifacts["promotion"]), 1)


if __name__ == "__main__":
    unittest.main()
