import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from tools.process_human_completion_workbook import (
    build_source_indexes,
    process_workbook,
    resolve_source_row,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sheet_xml(rows: list[list[str]]) -> str:
    xml_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row):
            col = chr(ord("A") + col_index)
            cells.append(
                f'<c r="{col}{row_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )


def write_xlsx(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    workbook_sheets = []
    rels = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        for index, (name, rows) in enumerate(sheets.items(), start=1):
            workbook_sheets.append(
                f'<sheet name="{escape(name)}" sheetId="{index}" '
                f'r:id="rId{index}"/>'
            )
            rels.append(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
            )
            zf.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(rels)}</Relationships>',
        )


class ProcessHumanCompletionWorkbookTest(unittest.TestCase):
    def test_equivalent_duplicate_sources_resolve_to_richest_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = {
                "candidate_id": "mt_duplicate",
                "doc_id": "doc_a",
                "page_index": 0,
                "bbox": [1, 2, 3, 4],
                "category": "equipment_tag",
                "target_text": "P-101",
                "review_status": "candidate",
            }
            write_jsonl(root / "microtext" / "annotations" / "candidates_a.jsonl", [base])
            write_jsonl(root / "microtext" / "annotations" / "candidates_b.jsonl", [base])
            enriched = dict(base)
            enriched.update(
                {
                    "review_status": "needs_review",
                    "image_path": "derived/pages/doc_a/page_000.png",
                    "question_text": "What equipment tag is shown?",
                }
            )
            write_jsonl(root / "microtext" / "annotations" / "review.jsonl", [enriched])

            resolution = resolve_source_row(
                root,
                build_source_indexes(root),
                "microtext",
                "mt_duplicate",
            )

            self.assertEqual("resolved", resolution["status"])
            self.assertEqual("equivalent_duplicate_rows", resolution["resolution_reason"])
            self.assertEqual(3, resolution["match_count"])
            self.assertEqual(3, resolution["equivalent_match_count"])
            self.assertEqual("What equipment tag is shown?", resolution["row"]["question_text"])

    def test_conflicting_duplicate_sources_remain_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "microtext" / "annotations" / "candidates_a.jsonl",
                [
                    {
                        "candidate_id": "mt_conflict",
                        "doc_id": "doc_a",
                        "bbox": [1, 2, 3, 4],
                        "target_text": "P-101",
                    }
                ],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "candidates_b.jsonl",
                [
                    {
                        "candidate_id": "mt_conflict",
                        "doc_id": "doc_a",
                        "bbox": [10, 20, 30, 40],
                        "target_text": "P-101",
                    }
                ],
            )

            resolution = resolve_source_row(
                root,
                build_source_indexes(root),
                "microtext",
                "mt_conflict",
            )

            self.assertEqual("ambiguous", resolution["status"])
            self.assertEqual("conflicting_duplicate_rows", resolution["resolution_reason"])

    def test_stages_reviewed_rows_and_blocks_incomplete_old_gold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "microtext" / "annotations" / "source.jsonl",
                [
                    {"candidate_id": "mt_accept", "doc_id": "doc_a", "proposed_text": "P-101", "target_text": "P-101", "category": "equipment_tag"},
                    {"candidate_id": "mt_edit", "doc_id": "doc_b", "proposed_text": "BAD", "target_text": "BAD", "category": "unknown_microtext"},
                    {"candidate_id": "mt_reject", "doc_id": "doc_c", "proposed_text": "noise", "target_text": "noise", "category": "unknown_microtext"},
                    {"candidate_id": "sem_reject", "doc_id": "doc_d", "proposed_text": "PA=", "target_text": "PA=", "category": "instrument_tag"},
                ],
            )
            write_jsonl(
                root / "visualdiff" / "annotations" / "source.jsonl",
                [
                    {"pair_id": "vd_edit", "project_id": "p", "description": "CHANGE_DESC_GT_TODO", "change_type": "schematic_change_candidate"},
                    {"pair_id": "vd_reject", "project_id": "p", "description": "CHANGE_DESC_GT_TODO", "change_type": "schematic_change_candidate"},
                ],
            )
            workbook = root / "return.xlsx"
            write_xlsx(
                workbook,
                {
                    "OLD GOLD CHECK": [
                        ["id", "task", "answer_correct", "corrected_answer", "bbox_correct", "corrected_evidence_json", "accept_reject", "ambiguity", "rights_concern", "notes"],
                        ["old_ok", "microtext", "yes", "", "yes", "", "accept", "no", "no", ""],
                        ["old_reject", "visualdiff", "no", "correct text", "yes", "", "reject", "no", "no", "bad answer"],
                        ["old_incomplete", "visualdiff", "no", "", "yes", "", "accept", "no", "no", ""],
                    ],
                    "NEW MICROTEXT": [
                        ["candidate_id", "doc_id", "category", "proposed_text", "review_status", "corrected_text", "corrected_category", "review_notes"],
                        ["mt_accept", "doc_a", "equipment_tag", "P-101", "accepted", "", "", ""],
                        ["mt_edit", "doc_b", "unknown_microtext", "BAD", "edited", "PT-101", "instrument_tag", "fixed"],
                        ["mt_reject", "doc_c", "unknown_microtext", "noise", "rejected", "", "", "not useful"],
                    ],
                    "NEW VISUALDIFF": [
                        ["pair_id", "project_id", "change_type", "current_description", "human_status", "human_description", "human_notes"],
                        ["vd_edit", "p", "schematic_change_candidate", "CHANGE_DESC_GT_TODO", "edit", "Pin labels changed.", "visible"],
                        ["vd_reject", "p", "schematic_change_candidate", "CHANGE_DESC_GT_TODO", "reject_unclear", "", "identical"],
                    ],
                    "SEMANTIC REDO": [
                        ["candidate_id", "doc_id", "proposed_text", "proposed_category", "review_status", "corrected_text", "corrected_category", "review_notes"],
                        ["sem_reject", "doc_d", "PA=", "instrument_tag", "rejected", "", "", "fragment"],
                    ],
                },
            )
            output = root / "processed"

            report = process_workbook(root=root, workbook=workbook, output_dir=output, date_label="test")

            self.assertFalse(report["complete"])
            self.assertFalse(report["safe_to_merge_gold"])
            self.assertEqual(1, report["totals"]["incomplete_old_gold_rows"])
            self.assertEqual(2, report["totals"]["new_microtext_mergeable_rows"])
            self.assertEqual(1, report["totals"]["new_visualdiff_mergeable_rows"])
            self.assertEqual(3, report["totals"]["rejected_or_hold_rows"])

            microtext_rows = [
                json.loads(line)
                for line in (output / "new_microtext_reviewed.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["mt_accept", "mt_edit"], [row["candidate_id"] for row in microtext_rows])
            self.assertEqual("PT-101", microtext_rows[1]["corrected_text"])
            self.assertEqual("instrument_tag", microtext_rows[1]["category"])

            visualdiff_rows = [
                json.loads(line)
                for line in (output / "new_visualdiff_reviewed.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["vd_edit"], [row["pair_id"] for row in visualdiff_rows])
            self.assertEqual("Pin labels changed.", visualdiff_rows[0]["description"])
            self.assertEqual("edit", visualdiff_rows[0]["review_status"])
            self.assertEqual("edit", visualdiff_rows[0]["human_review_status"])

            old_gold_queue = (output / "old_gold_adjudication_queue.csv").read_text(encoding="utf-8")
            self.assertIn("old_reject", old_gold_queue)
            self.assertIn("old_incomplete", old_gold_queue)
            self.assertTrue((output / "processing_summary.md").exists())

    def test_category_only_microtext_edit_preserves_proposed_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "microtext" / "annotations" / "source.jsonl",
                [
                    {
                        "candidate_id": "mt_category_only",
                        "doc_id": "doc_a",
                        "proposed_text": "PT-101",
                        "target_text": "PT-101",
                        "category": "unknown_microtext",
                    }
                ],
            )
            workbook = root / "return.xlsx"
            write_xlsx(
                workbook,
                {
                    "OLD GOLD CHECK": [
                        ["id", "task", "answer_correct", "corrected_answer", "bbox_correct", "corrected_evidence_json", "accept_reject", "ambiguity", "rights_concern", "notes"],
                    ],
                    "NEW MICROTEXT": [
                        ["candidate_id", "doc_id", "category", "proposed_text", "review_status", "corrected_text", "corrected_category", "review_notes"],
                        ["mt_category_only", "doc_a", "unknown_microtext", "PT-101", "edited", "", "instrument_tag", "category corrected"],
                    ],
                    "NEW VISUALDIFF": [
                        ["pair_id", "project_id", "change_type", "current_description", "human_status", "human_description", "human_notes"],
                    ],
                    "SEMANTIC REDO": [
                        ["candidate_id", "doc_id", "proposed_text", "proposed_category", "review_status", "corrected_text", "corrected_category", "review_notes"],
                    ],
                },
            )
            output = root / "processed"

            report = process_workbook(
                root=root,
                workbook=workbook,
                output_dir=output,
                date_label="test",
            )

            self.assertEqual(1, report["totals"]["new_microtext_mergeable_rows"])
            self.assertEqual(0, report["totals"].get("microtext_edited_missing_correction", 0))
            rows = [
                json.loads(line)
                for line in (output / "new_microtext_reviewed.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual("instrument_tag", rows[0]["category"])
            self.assertEqual("PT-101", rows[0]["target_text"])
            self.assertEqual("PT-101", rows[0]["proposed_text"])
            self.assertEqual("", rows[0]["corrected_text"])


if __name__ == "__main__":
    unittest.main()
