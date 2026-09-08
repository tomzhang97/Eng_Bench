from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from tools.extract_machine_calibration_workbook import extract


FIELDS = [
    "sample_index",
    "candidate_id",
    "doc_id",
    "version_id",
    "page_index",
    "bbox",
    "category",
    "proposed_text",
    "ocr_text",
    "ocr_confidence",
    "crop_path",
    "context_path",
    "reviewer_decision",
    "corrected_text",
    "corrected_category",
    "reviewer_notes",
    "crop_sha256",
    "context_sha256",
    "crop_width",
    "crop_height",
    "context_width",
    "context_height",
]


def column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def cell(reference: str, value: str) -> str:
    if value.replace(".", "", 1).isdigit():
        return f'<c r="{reference}"><v>{value}</v></c>'
    return (
        f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    )


def worksheet(rows: dict[int, dict[int, str]]) -> str:
    body = []
    for row_number, values in sorted(rows.items()):
        cells = "".join(
            cell(f"{column_name(column)}{row_number}", value)
            for column, value in sorted(values.items())
            if value != ""
        )
        body.append(f'<row r="{row_number}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def build_xlsx(
    path: Path,
    expected: list[dict[str, str]],
    decisions: list[str],
    image_payloads: list[bytes] | None = None,
) -> None:
    image_payloads = image_payloads or [
        f"image-{index}".encode("ascii") for index in range(1, len(expected) + 1)
    ]
    review_rows: dict[int, dict[int, str]] = {}
    control_rows: dict[int, dict[int, str]] = {
        1: {index + 1: field for index, field in enumerate(FIELDS)}
    }
    for index, row in enumerate(expected):
        review_rows[index + 6] = {
            1: row["sample_index"],
            3: row["proposed_text"],
            4: row["category"],
            5: decisions[index],
        }
        control_rows[index + 2] = {
            column: row[field] for column, field in enumerate(FIELDS, 1)
        }
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="审核2条" sheetId="1" r:id="rId1"/>'
        '<sheet name="MachineControl" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '</Relationships>'
    )
    sheet_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rDrawing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
        '</Relationships>'
    )
    anchors = []
    drawing_relationships = []
    for index in range(len(expected)):
        relation_id = f"rImage{index + 1}"
        anchors.append(
            '<xdr:oneCellAnchor>'
            f'<xdr:from><xdr:col>1</xdr:col><xdr:row>{index + 5}</xdr:row></xdr:from>'
            '<xdr:ext cx="1" cy="1"/>'
            '<xdr:pic><xdr:blipFill>'
            f'<a:blip r:embed="{relation_id}"/>'
            '</xdr:blipFill></xdr:pic><xdr:clientData/>'
            '</xdr:oneCellAnchor>'
        )
        drawing_relationships.append(
            f'<Relationship Id="{relation_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="/xl/media/image{index + 1}.png"/>'
        )
    drawing = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        + "".join(anchors)
        + '</xdr:wsDr>'
    )
    drawing_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(drawing_relationships)
        + '</Relationships>'
    )
    review_xml = worksheet(review_rows).replace(
        '</worksheet>', '<drawing r:id="rDrawing" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", review_xml)
        archive.writestr("xl/worksheets/sheet2.xml", worksheet(control_rows))
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", sheet_rels)
        archive.writestr("xl/drawings/drawing1.xml", drawing)
        archive.writestr("xl/drawings/_rels/drawing1.xml.rels", drawing_rels)
        for index, payload in enumerate(image_payloads, 1):
            archive.writestr(f"xl/media/image{index}.png", payload)


class ExtractMachineCalibrationWorkbookTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, list[dict[str, str]]]:
        expected = []
        for index in range(1, 3):
            expected.append(
                {
                    "sample_index": str(index),
                    "candidate_id": f"candidate-{index}",
                    "doc_id": "doc",
                    "version_id": "v1",
                    "page_index": "0",
                    "bbox": "[1, 2, 3, 4]",
                    "category": "component_value",
                    "proposed_text": f"{index}k",
                    "ocr_text": f"{index}k",
                    "ocr_confidence": "0.99",
                    "crop_path": f"crops/{index}.png",
                    "context_path": f"contexts/{index}.png",
                    "reviewer_decision": "",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                    "crop_sha256": hashlib.sha256(
                        f"image-{index}".encode("ascii")
                    ).hexdigest(),
                    "context_sha256": "b" * 64,
                    "crop_width": "100",
                    "crop_height": "50",
                    "context_width": "200",
                    "context_height": "150",
                }
            )
        checklist = root / "calibration_checklist.csv"
        with checklist.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(expected)
        workbook = root / "return.xlsx"
        build_xlsx(workbook, expected, ["correct", "correct"])
        return workbook, checklist, expected

    def test_extracts_complete_hash_bound_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook, checklist, _ = self.fixture(root)
            output = root / "completed.csv"
            report = extract(
                workbook_path=workbook,
                expected_path=checklist,
                output_path=output,
                report_path=root / "report.json",
            )
            self.assertTrue(report["valid"], report["issues"])
            self.assertTrue(output.is_file())
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(["correct", "correct"], [row["reviewer_decision"] for row in rows])
            self.assertEqual(2, report["decision_counts"]["correct"])

    def test_fails_closed_on_missing_decision_and_tampered_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook, checklist, expected = self.fixture(root)
            expected[0]["category"] = "dimension_value"
            build_xlsx(workbook, expected, ["", "correct"])
            output = root / "completed.csv"
            report = extract(
                workbook_path=workbook,
                expected_path=checklist,
                output_path=output,
                report_path=root / "report.json",
            )
            self.assertFalse(report["valid"])
            self.assertFalse(output.exists())
            self.assertIn("immutable_mismatch:1:category", report["issues"])
            self.assertIn("review_reference_mismatch:1:category", report["issues"])
            self.assertIn("invalid_or_missing_decision:1", report["issues"])

    def test_fails_closed_on_tampered_embedded_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook, checklist, expected = self.fixture(root)
            build_xlsx(
                workbook,
                expected,
                ["correct", "correct"],
                image_payloads=[b"tampered-image", b"image-2"],
            )
            output = root / "completed.csv"
            report = extract(
                workbook_path=workbook,
                expected_path=checklist,
                output_path=output,
                report_path=root / "report.json",
            )
            self.assertFalse(report["valid"])
            self.assertFalse(output.exists())
            self.assertIn("review_crop_hash_mismatch:1", report["issues"])


if __name__ == "__main__":
    unittest.main()
