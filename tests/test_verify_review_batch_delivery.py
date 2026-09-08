from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from io import BytesIO, StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "tools" / "verify_review_batch_delivery.py"
    spec = importlib.util.spec_from_file_location("verify_review_batch_delivery", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def minimal_xlsx(candidate_ids: list[str], identity_field: str = "candidate_id") -> bytes:
    rows = [["review_index", identity_field]] + [[str(index), candidate_id] for index, candidate_id in enumerate(candidate_ids, 1)]
    sheet_rows = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for column, value in zip(("A", "B"), row):
            cells.append(f'<c r="{column}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Checklist" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>',
        )
    return output.getvalue()


def minimal_provenance_xlsx(pair_ids: list[str], mandatory_rewrite_rows: int = 0) -> bytes:
    sheet_rows = [
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Eng_Bench Gold v2.0</t></is></c></row>',
        '<row r="6"><c r="A6" t="inlineStr"><is><t>#</t></is></c>'
        '<c r="J6" t="inlineStr"><is><t>Pair ID</t></is></c></row>',
    ]
    for index, pair_id in enumerate(pair_ids, 1):
        excel_row = index + 6
        formula = (
            f'<c r="I{excel_row}" t="inlineStr"><f>IF(E{excel_row}&lt;&gt;2,"必须选2并重写","完成")</f>'
            '<is><t>未完成</t></is></c>'
            if index <= mandatory_rewrite_rows
            else ""
        )
        sheet_rows.append(
            f'<row r="{excel_row}"><c r="A{excel_row}"><v>{index}</v></c>'
            f'{formula}<c r="J{excel_row}" t="inlineStr"><is><t>{pair_id}</t></is></c></row>'
        )
    anchors = "".join(
        '<xdr:oneCellAnchor>'
        f'<xdr:from><xdr:col>1</xdr:col><xdr:row>{index + 5}</xdr:row></xdr:from>'
        '<xdr:ext cx="100" cy="100"/><xdr:pic/><xdr:clientData/>'
        '</xdr:oneCellAnchor>'
        for index in range(1, len(pair_ids) + 1)
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Review" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>',
        )
        zf.writestr(
            "xl/drawings/drawing1.xml",
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">'
            f"{anchors}</xdr:wsDr>",
        )
        for index in range(1, len(pair_ids) + 1):
            zf.writestr(f"xl/media/image{index}.png", b"png")
    return output.getvalue()


def build_delivery(
    path: Path,
    xlsx_ids: list[str],
    *,
    nested_zip: bool = False,
    identity_field: str = "candidate_id",
) -> None:
    root = "batch"
    pack = "pack_a"
    candidate_ids = ["c1", "c2"]
    manifest_output = StringIO()
    writer = csv.DictWriter(manifest_output, fieldnames=["pack_name", "rows", "checklist"])
    writer.writeheader()
    writer.writerow({"pack_name": pack, "rows": 2, "checklist": "checklist.csv"})
    checklist_output = StringIO()
    writer = csv.DictWriter(checklist_output, fieldnames=[identity_field, "crop_path"])
    writer.writeheader()
    for candidate_id in candidate_ids:
        writer.writerow({identity_field: candidate_id, "crop_path": f"crops/{candidate_id}.png"})
    jsonl = "".join(
        json.dumps({identity_field: candidate_id, "crop_path": f"crops/{candidate_id}.png"}) + "\n"
        for candidate_id in candidate_ids
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for required in (
            "README.md",
            "HUMAN_REVIEW_STEPS.md",
            "next_review_batch_build_report.json",
            "next_review_batch_build_report.md",
            "INTERN_INSTRUCTIONS_ZH.md",
            "RETURN_ONLY_THIS_XLSX.txt",
        ):
            zf.writestr(f"{root}/{required}", "ok")
        zf.writestr(f"{root}/NEXT_REVIEW_BATCH_MANIFEST.csv", manifest_output.getvalue())
        prefix = f"{root}/review_packs/{pack}"
        zf.writestr(f"{prefix}/manifest.jsonl", jsonl)
        zf.writestr(f"{prefix}/checklist.csv", checklist_output.getvalue())
        zf.writestr(f"{prefix}/checklist.xlsx", minimal_xlsx(xlsx_ids, identity_field))
        for candidate_id in candidate_ids:
            zf.writestr(f"{prefix}/crops/{candidate_id}.png", b"png")
        if nested_zip:
            zf.writestr(f"{root}/old.zip", b"zip")


def build_provenance_delivery(
    path: Path,
    *,
    return_count: int = 1,
    mandatory_rewrite_rows: int = 0,
    workbook_report_rewrite_rows: int | None = None,
) -> None:
    root = "provenance"
    pack = "visualdiff_2"
    pair_ids = ["p1", "p2"]
    manifest_output = StringIO()
    writer = csv.DictWriter(
        manifest_output,
        fieldnames=["pack_name", "task", "rows", "checklist", "workbook", "safe_to_merge_gold"],
    )
    writer.writeheader()
    writer.writerow(
        {
            "pack_name": pack,
            "task": "visualdiff",
            "rows": 2,
            "checklist": "visualdiff_validation_checklist.csv",
            "workbook": "02_VisualDiff_2.xlsx",
            "safe_to_merge_gold": "false",
        }
    )
    checklist_output = StringIO()
    writer = csv.DictWriter(
        checklist_output,
        fieldnames=["pair_id", "panel_path", "description_rewrite_required", "description_task"],
    )
    writer.writeheader()
    for index, pair_id in enumerate(pair_ids, 1):
        required = index <= mandatory_rewrite_rows
        writer.writerow(
            {
                "pair_id": pair_id,
                "panel_path": f"panels/{pair_id}.png",
                "description_rewrite_required": str(required),
                "description_task": "rewrite" if required else "",
            }
        )
    reported_rewrite_rows = (
        mandatory_rewrite_rows
        if workbook_report_rewrite_rows is None
        else workbook_report_rewrite_rows
    )
    files: dict[str, bytes] = {
        "README.md": b"read steps first",
        "HUMAN_REVIEW_STEPS.md": (
            "02_VisualDiff_2.xlsx\n"
            f"只交回上述 {return_count} 个填写后的 XLSX\n"
        ).encode("utf-8"),
        "next_review_batch_build_report.json": json.dumps(
            {"promotion_policy": {"safe_to_merge_gold": False}}
        ).encode(),
        "next_review_batch_build_report.md": b"report",
        "NEXT_REVIEW_BATCH_MANIFEST.csv": manifest_output.getvalue().encode(),
        "workbook_build_payload.json": json.dumps({"visualdiff": {"rows": 2}}).encode(),
        "WORKBOOK_BUILD_REPORT.json": json.dumps(
            {
                "total_rows": 2,
                "total_embedded_images": 2,
                "workbooks": [
                    {
                        "workbook": "02_VisualDiff_2.xlsx",
                        "rows": 2,
                        "embedded_images": 2,
                        "description_rewrite_required": reported_rewrite_rows,
                    }
                ],
            }
        ).encode(),
        "FINALIZATION_REPORT.json": json.dumps({"safe_to_merge_gold": False}).encode(),
        "02_VisualDiff_2.xlsx": minimal_provenance_xlsx(pair_ids, mandatory_rewrite_rows),
        f"review_packs/{pack}/visualdiff_validation_checklist.csv": checklist_output.getvalue().encode(),
        f"review_packs/{pack}/manifest.jsonl": "".join(
            json.dumps({"pair_id": pair_id, "panel_path": f"panels/{pair_id}.png"}) + "\n"
            for pair_id in pair_ids
        ).encode(),
        f"review_packs/{pack}/panels/p1.png": b"png",
        f"review_packs/{pack}/panels/p2.png": b"png",
    }
    inventory_output = StringIO()
    writer = csv.DictWriter(inventory_output, fieldnames=["relative_path", "size_bytes", "sha256"])
    writer.writeheader()
    sums = []
    for name, data in sorted(files.items()):
        digest = hashlib.sha256(data).hexdigest()
        writer.writerow({"relative_path": name, "size_bytes": len(data), "sha256": digest})
        sums.append(f"{digest}  {name}\n")
    files["FILE_INVENTORY.csv"] = inventory_output.getvalue().encode()
    files["SHA256SUMS.txt"] = "".join(sums).encode("ascii")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(f"{root}/{name}", data)


class VerifyReviewBatchDeliveryTests(unittest.TestCase):
    def test_valid_delivery_checks_xlsx_candidate_order(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.zip"
            build_delivery(path, ["c1", "c2"])
            report = module.verify_delivery(path)
            self.assertTrue(report["valid"], report["issues"])
            self.assertTrue(report["embedded_workbooks"][0]["candidate_order_match"])

    def test_rejects_mismatched_workbook_and_nested_zip(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.zip"
            build_delivery(path, ["c2", "c1"], nested_zip=True)
            report = module.verify_delivery(path)
            self.assertFalse(report["valid"])
            self.assertTrue(any("nested ZIP" in issue for issue in report["issues"]))
            self.assertTrue(any("candidate_id order" in issue for issue in report["issues"]))

    def test_valid_visualdiff_delivery_checks_pair_id_order(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "batch.zip"
            build_delivery(path, ["c1", "c2"], identity_field="pair_id")
            report = module.verify_delivery(path)
            workbook = report["embedded_workbooks"][0]
            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual(workbook["identity_field"], "pair_id")
            self.assertTrue(workbook["candidate_order_match"])

    def test_valid_provenance_delivery_checks_top_level_workbook_and_inventory(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "provenance.zip"
            build_provenance_delivery(path)
            report = module.verify_delivery(path)
            workbook = report["embedded_workbooks"][0]
            self.assertTrue(report["valid"], report["issues"])
            self.assertTrue(report["inventory"]["valid"])
            self.assertEqual(workbook["workbook_rows"], 2)
            self.assertEqual(workbook["media_count"], 2)
            self.assertEqual(workbook["picture_anchors"], 2)
            self.assertEqual(workbook["unique_anchor_rows"], 2)

    def test_provenance_delivery_rejects_stale_workbook_return_count(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "provenance.zip"
            build_provenance_delivery(path, return_count=2)
            report = module.verify_delivery(path)
            self.assertFalse(report["valid"])
            self.assertTrue(any("incorrect workbook return count" in issue for issue in report["issues"]))

    def test_provenance_delivery_verifies_mandatory_rewrite_controls(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "provenance.zip"
            build_provenance_delivery(path, mandatory_rewrite_rows=1)
            report = module.verify_delivery(path)
            workbook = report["embedded_workbooks"][0]
            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual(workbook["mandatory_rewrite_rows"], 1)
            self.assertEqual(workbook["mandatory_rewrite_formula_count"], 1)

    def test_provenance_delivery_rejects_rewrite_report_mismatch(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "provenance.zip"
            build_provenance_delivery(
                path,
                mandatory_rewrite_rows=1,
                workbook_report_rewrite_rows=0,
            )
            report = module.verify_delivery(path)
            self.assertFalse(report["valid"])
            self.assertTrue(
                any("workbook build report rewrite count mismatch" in issue for issue in report["issues"])
            )


if __name__ == "__main__":
    unittest.main()
