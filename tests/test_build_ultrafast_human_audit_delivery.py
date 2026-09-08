from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import build_ultrafast_human_audit_delivery as delivery


class UltrafastHumanAuditDeliveryTests(unittest.TestCase):
    def test_expected_names_are_flat_and_complete(self) -> None:
        names = delivery.expected_names()
        self.assertEqual(len(names), 12)
        self.assertIn("00_READ_ME_FIRST_CN.txt", names)
        self.assertIn("PRIMARY_REVIEW_498.xlsx", names)
        self.assertIn("AUDITOR_10_REVIEW_12.xlsx", names)

    def test_owner_guide_contains_codes_and_distribution(self) -> None:
        guide = delivery.owner_guide()
        self.assertIn("Gold v2.0 Global", guide)
        self.assertIn("1/2/3/4", guide)
        self.assertIn("1/2/3", guide)
        self.assertIn("618", guide)
        self.assertIn("\u6ca1\u6709\u5b50 ZIP", guide)
        self.assertIn("Ctrl+S", guide)
        self.assertIn("\u4e0d\u7528\u5207\u6362\u4e2d\u82f1\u6587\u8f93\u5165\u6cd5", guide)
        self.assertIn("Wave39 DOE", guide)
        self.assertIn("\u4e0d\u8981\u4fee\u6539\u6587\u5b57\u6216\u7c7b\u522b", guide)

    def test_one_sheet_owner_guide_is_unambiguous_and_self_contained(self) -> None:
        guide = delivery.one_sheet_owner_guide()
        self.assertIn("唯一当前人工审核包", guide)
        self.assertIn("Wave39 DOE", guide)
        self.assertIn("PRIMARY_REVIEW_498.xlsx", guide)
        self.assertIn("AUDITOR_NN_REVIEW_12.xlsx", guide)
        self.assertIn("1/2/3/4", guide)
        self.assertIn("1/2/3", guide)
        self.assertIn("618", guide)
        self.assertIn("没有子 ZIP", guide)
        self.assertIn("B 列图片和 C 列问题", guide)
        self.assertIn("黄色 D7", guide)
        self.assertIn("不要把整个 ZIP 发给审核员", guide)
        self.assertNotIn("通常只按 1", guide)
        self.assertNotIn("D 列机器内容", guide)

    def test_output_guard_and_zip_path_safety(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery.ensure_child(root / "child", root)
            with self.assertRaises(ValueError):
                delivery.ensure_child(root, root)
            with self.assertRaises(ValueError):
                delivery.ensure_child(root.parent / "outside", root)
        self.assertFalse(delivery.unsafe_zip_name("AUDITOR_01_REVIEW_12.xlsx"))
        self.assertTrue(delivery.unsafe_zip_name("../escape.xlsx"))

    def test_one_cell_auditor_picture_layout(self) -> None:
        anchors = []
        for row in range(6, 18):
            anchors.append(
                f"<xdr:oneCellAnchor><xdr:from><xdr:col>1</xdr:col>"
                f"<xdr:colOff>66675</xdr:colOff><xdr:row>{row}</xdr:row>"
                f"<xdr:rowOff>66675</xdr:rowOff></xdr:from>"
                f"<xdr:ext cx='100' cy='100'/><xdr:pic /></xdr:oneCellAnchor>"
            )
        drawing = (
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">'
            + "".join(anchors)
            + "</xdr:wsDr>"
        )
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "auditor.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/drawings/drawing1.xml", drawing)
            count, issues = delivery.validate_picture_layout(workbook, "auditor")
        self.assertEqual(count, 12)
        self.assertEqual(issues, [])

    def test_machine_sheet_can_be_made_very_hidden(self) -> None:
        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheets><sheet name="review" sheetId="1"/>'
            '<sheet name="\u673a\u5668\u6570\u636e_\u52ff\u6539" sheetId="2"/></sheets>'
            '</workbook>'
        )
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "audit.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook_xml)
                archive.writestr("placeholder.txt", "unchanged")
            delivery.set_sheet_state(workbook, delivery.MACHINE_SHEET, "veryHidden")
            self.assertEqual(
                delivery.sheet_states(workbook)[delivery.MACHINE_SHEET],
                "veryHidden",
            )
            with zipfile.ZipFile(workbook, "r") as archive:
                self.assertEqual(archive.read("placeholder.txt"), b"unchanged")

    def test_prefixed_machine_sheet_can_be_made_very_hidden(self) -> None:
        workbook_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<x:workbook xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<x:sheets><x:sheet name="review" sheetId="1"/>'
            '<x:sheet name="\u673a\u5668\u6570\u636e_\u52ff\u6539" sheetId="2"/></x:sheets>'
            '</x:workbook>'
        )
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "audit.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook_xml)
            delivery.set_sheet_state(workbook, delivery.MACHINE_SHEET, "veryHidden")
            self.assertEqual(
                delivery.sheet_states(workbook)[delivery.MACHINE_SHEET],
                "veryHidden",
            )
            with zipfile.ZipFile(workbook, "r") as archive:
                updated = archive.read("xl/workbook.xml")
        self.assertIn(b'<x:sheet name="review"', updated)
        self.assertIn(b'state="veryHidden"', updated)

    def test_machine_sheet_state_noop_preserves_excel_workbook_xml(self) -> None:
        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" '
            'mc:Ignorable="x15">'
            '<sheets><sheet name="机器数据_勿改" sheetId="1" state="veryHidden"/></sheets>'
            '</workbook>'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "audit.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook_xml)
            before = workbook.read_bytes()
            delivery.set_sheet_state(
                workbook, delivery.MACHINE_SHEET, "veryHidden"
            )
            self.assertEqual(workbook.read_bytes(), before)

    def test_sheet_state_rewrite_preserves_ignorable_namespace_declaration(self) -> None:
        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" '
            'mc:Ignorable="x15">'
            '<sheets><sheet name="review" sheetId="1"/>'
            '<sheet name="\u673a\u5668\u6570\u636e_\u52ff\u6539" sheetId="2"/></sheets>'
            '</workbook>'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "audit.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook_xml)
            delivery.set_sheet_state(
                workbook, delivery.MACHINE_SHEET, "veryHidden"
            )
            with zipfile.ZipFile(workbook, "r") as archive:
                updated = archive.read("xl/workbook.xml")
        self.assertIn(b'xmlns:x15=', updated)
        self.assertIn(b'mc:Ignorable="x15"', updated)
        self.assertIn(b'state="veryHidden"', updated)


if __name__ == "__main__":
    unittest.main()
