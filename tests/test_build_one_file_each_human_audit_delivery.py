from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from tools import build_one_file_each_human_audit_delivery as delivery


class OneFileEachHumanAuditDeliveryTests(unittest.TestCase):
    def test_expected_names_are_one_readme_and_eleven_workbooks(self) -> None:
        names = delivery.expected_names()
        self.assertEqual(len(names), 12)
        self.assertIn("00_READ_ME_FIRST_CN.txt", names)
        self.assertIn("PRIMARY_REVIEW_498.xlsx", names)
        self.assertIn("AUDITOR_10_REVIEW_12.xlsx", names)

    def test_owner_guide_is_one_file_per_person_and_complete(self) -> None:
        guide = delivery.owner_guide()
        self.assertIn("每人只发一个 XLSX", guide)
        self.assertIn("Wave39 DOE 的全部 95 条", guide)
        self.assertIn("无需改文字或写变化描述", guide)
        self.assertIn("所有 618 张证据图片已经嵌入工作簿", guide)

    def test_output_guard_requires_child_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery.ensure_within(root / "child", root)
            with self.assertRaises(ValueError):
                delivery.ensure_within(root, root)
            with self.assertRaises(ValueError):
                delivery.ensure_within(root.parent / "outside", root)

    def test_zip_path_safety(self) -> None:
        self.assertFalse(delivery.unsafe_zip_name("AUDITOR_01_REVIEW_12.xlsx"))
        self.assertTrue(delivery.unsafe_zip_name("../file.xlsx"))
        self.assertTrue(delivery.unsafe_zip_name("/absolute/file.xlsx"))

    def test_repaired_content_types_are_excel_compatible(self) -> None:
        source = b'''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml" />
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
</Types>'''
        repaired = delivery.repaired_content_types(source)
        root = ET.fromstring(repaired)
        namespace = {"c": delivery.CONTENT_TYPES_NS}
        defaults = {
            node.get("Extension"): node.get("ContentType")
            for node in root.findall("c:Default", namespace)
        }
        overrides = {
            node.get("PartName"): node.get("ContentType")
            for node in root.findall("c:Override", namespace)
        }
        self.assertEqual(defaults["xml"], "application/xml")
        self.assertEqual(
            overrides["/xl/workbook.xml"],
            delivery.WORKBOOK_CONTENT_TYPE,
        )

    def test_copy_workbook_repairs_only_content_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.xlsx"
            destination = root / "destination.xlsx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="bad" /></Types>',
                )
                archive.writestr(
                    "xl/_rels/workbook.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" Target="/xl/worksheets/sheet1.xml" /></Relationships>',
                )
                archive.writestr("xl/tables/table1.xml", b"invalid-table")
                archive.writestr("xl/media/image.png", b"image-bytes")
            delivery.copy_excel_compatible_workbook(source, destination)
            with zipfile.ZipFile(destination, "r") as archive:
                self.assertEqual(archive.read("xl/media/image.png"), b"image-bytes")
                self.assertNotIn("xl/tables/table1.xml", archive.namelist())
                content_types = archive.read("[Content_Types].xml")
                relationships = archive.read("xl/_rels/workbook.xml.rels")
            self.assertIn(b"application/xml", content_types)
            self.assertIn(b"/xl/workbook.xml", content_types)
            self.assertIn(b'Target="worksheets/sheet1.xml"', relationships)

    def test_relationship_repair_uses_part_relative_targets(self) -> None:
        data = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="r1" Type="drawing" Target="/xl/drawings/drawing1.xml" />
<Relationship Id="r2" Type="hyperlink" Target="https://example.com" TargetMode="External" />
</Relationships>'''
        repaired = delivery.repaired_relationships(
            data,
            "xl/worksheets/_rels/sheet2.xml.rels",
        )
        root = ET.fromstring(repaired)
        namespace = {"r": delivery.RELATIONSHIPS_NS}
        targets = {
            node.get("Id"): node.get("Target")
            for node in root.findall("r:Relationship", namespace)
        }
        self.assertEqual(targets["r1"], "../drawings/drawing1.xml")
        self.assertEqual(targets["r2"], "https://example.com")

    def test_referenced_media_counts_duplicate_shape_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "images.xlsx"
            drawing = '''<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><xdr:twoCellAnchor><xdr:pic><xdr:blipFill><a:blip r:embed="rId1" /></xdr:blipFill></xdr:pic></xdr:twoCellAnchor><xdr:twoCellAnchor><xdr:pic><xdr:blipFill><a:blip r:embed="rId1" /></xdr:blipFill></xdr:pic></xdr:twoCellAnchor></xdr:wsDr>'''
            relationships = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="image" Target="../media/image1.png" /></Relationships>'''
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/drawings/drawing1.xml", drawing)
                archive.writestr(
                    "xl/drawings/_rels/drawing1.xml.rels",
                    relationships,
                )
                archive.writestr("xl/media/image1.png", b"same-image")
            hashes = delivery.referenced_media_hashes(workbook)
            self.assertEqual(sum(hashes.values()), 2)
            self.assertEqual(len(hashes), 1)

    def test_picture_anchor_report_requires_column_b_and_one_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "anchors.xlsx"
            drawing = '''<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"><xdr:twoCellAnchor><xdr:from><xdr:col>1</xdr:col><xdr:colOff>10</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>10</xdr:rowOff></xdr:from><xdr:to><xdr:col>1</xdr:col><xdr:colOff>20</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>20</xdr:rowOff></xdr:to><xdr:pic /></xdr:twoCellAnchor></xdr:wsDr>'''
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/drawings/drawing1.xml", drawing)
            count, issues = delivery.picture_anchor_report(workbook)
            self.assertEqual(count, 1)
            self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
