import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import build_source_inventory


class BuildSourceInventoryUnittest(unittest.TestCase):
    def test_merge_inventory_rows_preserves_existing_and_appends_new_paths(self) -> None:
        existing = [
            {
                "path": "meta/standards/existing_reference.pdf",
                "task": "reference",
                "doc_id": "existing_reference",
                "domain": "unknown",
                "source_url": "",
                "public_status": "unknown",
                "textlayer_status": "missing",
                "render_status": "missing",
                "notes": "curated row",
            }
        ]
        rebuilt = [
            {
                "path": "meta/standards/existing_reference.pdf",
                "task": "reference",
                "doc_id": "existing_reference",
                "domain": "unknown",
                "source_url": "",
                "public_status": "unknown",
                "textlayer_status": "present",
                "render_status": "present",
                "notes": "rebuilt row",
            },
            {
                "path": "microtext/docs/source_intake/new_sheet.jpg",
                "task": "microtext",
                "doc_id": "new_sheet",
                "domain": "architectural",
                "source_url": "",
                "public_status": "unknown",
                "textlayer_status": "missing",
                "render_status": "present",
                "notes": "not in manifest",
            },
        ]

        merged = build_source_inventory.merge_inventory_rows(existing, rebuilt)

        self.assertEqual([row["path"] for row in merged], [
            "meta/standards/existing_reference.pdf",
            "microtext/docs/source_intake/new_sheet.jpg",
        ])
        self.assertEqual(merged[0]["notes"], "curated row")

    def test_merge_inventory_rows_deduplicates_existing_doc_without_path(self) -> None:
        existing = [{"doc_id": "shared_doc", "source_path": "", "review_rows": "10"}]
        rebuilt = [
            {
                "path": "microtext/docs/shared_doc.pdf",
                "doc_id": "shared_doc",
                "task": "microtext",
            }
        ]

        merged = build_source_inventory.merge_inventory_rows(existing, rebuilt)

        self.assertEqual(merged, existing)

    def test_merge_inventory_rows_can_refresh_operational_fields_only(self) -> None:
        existing = [
            {
                "path": "visualdiff/docs/revision.pdf",
                "doc_id": "revision",
                "render_status": "missing",
                "textlayer_status": "missing",
                "public_status": "unknown",
                "notes": "curated note",
                "review_rows": "17",
            }
        ]
        rebuilt = [
            {
                "path": "visualdiff/docs/revision.pdf",
                "doc_id": "revision",
                "task": "visualdiff",
                "render_status": "present",
                "textlayer_status": "present",
                "public_status": "cern_ohl_1_2_open_hardware",
                "notes": "rebuilt note",
            }
        ]

        merged = build_source_inventory.merge_inventory_rows(
            existing, rebuilt, refresh_existing=True
        )

        self.assertEqual(merged[0]["render_status"], "present")
        self.assertEqual(merged[0]["textlayer_status"], "present")
        self.assertEqual(merged[0]["public_status"], "cern_ohl_1_2_open_hardware")
        self.assertEqual(merged[0]["notes"], "curated note")
        self.assertEqual(merged[0]["review_rows"], "17")

    def test_merge_inventory_rows_can_limit_refresh_to_selected_doc_ids(self) -> None:
        existing = [
            {"doc_id": "selected", "render_status": "missing"},
            {"doc_id": "untouched", "render_status": "missing"},
        ]
        rebuilt = [
            {"doc_id": "selected", "render_status": "present"},
            {"doc_id": "untouched", "render_status": "present"},
        ]

        merged = build_source_inventory.merge_inventory_rows(
            existing,
            rebuilt,
            refresh_existing=True,
            refresh_doc_ids={"selected"},
        )

        self.assertEqual(merged[0]["render_status"], "present")
        self.assertEqual(merged[1]["render_status"], "missing")

    def test_merge_inventory_rows_prunes_stale_ntrs_citation_receipts(self) -> None:
        existing = [
            {
                "path": "microtext/docs/source_intake/NTRS_CITATION_20205006355.json",
                "doc_id": "ntrs_citation_20205006355",
                "notes": "not in manifest",
            },
            {
                "path": "microtext/docs/nasa_report_NTRS.json",
                "doc_id": "nasa_report_ntrs",
                "notes": "not in manifest",
            },
            {
                "path": "microtext/docs/source_intake/source_bundle.json",
                "doc_id": "source_bundle",
            },
        ]

        merged = build_source_inventory.merge_inventory_rows(existing, [])

        self.assertEqual([row["doc_id"] for row in merged], ["source_bundle"])

    def test_write_inventory_preserves_extended_existing_fields(self) -> None:
        rows = [
            {
                "doc_id": "extended_doc",
                "domain": "architectural",
                "review_rows": "116",
                "open_review_rows": "116",
            }
        ]
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "nested" / "inventory.csv"
            build_source_inventory.write_inventory(output, rows)
            with output.open(newline="", encoding="utf-8") as handle:
                written = list(csv.DictReader(handle))

        self.assertEqual(written[0]["review_rows"], "116")
        self.assertEqual(written[0]["open_review_rows"], "116")
        self.assertIn("path", written[0])

    def test_manifest_paths_preserve_original_case(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_pdf = root / "visualdiff" / "docs" / "CaseSensitivePanel.PDF"
            source_pdf.parent.mkdir(parents=True)
            source_pdf.write_bytes(b"fake pdf bytes")
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "path": "visualdiff/docs/CaseSensitivePanel.PDF",
                        "task": "visualdiff",
                        "doc_id": "case_sensitive_panel",
                        "domain": "mechanical_cad",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            rows = build_source_inventory.build_inventory(root)

        self.assertEqual(rows[0]["path"], "visualdiff/docs/CaseSensitivePanel.PDF")
        self.assertEqual(rows[0]["doc_id"], "case_sensitive_panel")

    def test_source_images_under_docs_are_inventoried_without_generated_pages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_image = root / "microtext" / "docs" / "source_intake" / "arch_sheet.jpg"
            source_image.parent.mkdir(parents=True)
            source_image.write_bytes(b"fake jpg bytes")
            source_pdf = root / "visualdiff" / "docs" / "source_intake" / "board.pdf"
            source_pdf.parent.mkdir(parents=True)
            source_pdf.write_bytes(b"fake pdf bytes")
            source_svg = root / "microtext" / "docs" / "diagram.svg"
            source_svg.write_text("<svg />", encoding="utf-8")
            source_sch = root / "visualdiff" / "docs" / "board.sch"
            source_sch.write_text("schematic", encoding="utf-8")
            source_dxf = root / "visualdiff" / "docs" / "panel.dxf"
            source_dxf.write_text("dxf", encoding="utf-8")
            source_bundle = root / "microtext" / "docs" / "source_bundle.json"
            source_bundle.write_text("{}", encoding="utf-8")
            generated_page = root / "derived" / "pages_300dpi" / "arch_sheet" / "page_0001.png"
            generated_page.parent.mkdir(parents=True)
            generated_page.write_bytes(b"fake png bytes")
            derived_pdf = root / "derived" / "human_adjudication" / "preview" / "copied.pdf"
            derived_pdf.parent.mkdir(parents=True)
            derived_pdf.write_bytes(b"fake copied pdf bytes")
            worktree_pdf = root / ".claude" / "worktrees" / "demo" / "microtext" / "docs" / "copied.pdf"
            worktree_pdf.parent.mkdir(parents=True)
            worktree_pdf.write_bytes(b"fake worktree pdf bytes")

            rows = build_source_inventory.build_inventory(root)

        by_path = {row["path"]: row for row in rows}
        self.assertIn("microtext/docs/source_intake/arch_sheet.jpg", by_path)
        self.assertIn("visualdiff/docs/source_intake/board.pdf", by_path)
        self.assertIn("microtext/docs/diagram.svg", by_path)
        self.assertIn("visualdiff/docs/board.sch", by_path)
        self.assertIn("visualdiff/docs/panel.dxf", by_path)
        self.assertIn("microtext/docs/source_bundle.json", by_path)
        self.assertNotIn("derived/pages_300dpi/arch_sheet/page_0001.png", by_path)
        self.assertNotIn("derived/human_adjudication/preview/copied.pdf", by_path)
        self.assertNotIn(".claude/worktrees/demo/microtext/docs/copied.pdf", by_path)
        self.assertEqual(by_path["microtext/docs/source_intake/arch_sheet.jpg"]["task"], "microtext")
        self.assertEqual(by_path["microtext/docs/source_intake/arch_sheet.jpg"]["doc_id"], "arch_sheet")

    def test_source_iteration_is_scoped_to_authoritative_doc_roots(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            microtext_source = root / "microtext" / "docs" / "sheet.pdf"
            visualdiff_source = root / "visualdiff" / "docs" / "board.pdf"
            archive_copy = root / "derived" / "human_adjudication" / "sheet.pdf"
            for path in (microtext_source, visualdiff_source, archive_copy):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"pdf")

            paths = build_source_inventory.iter_source_files(root)

        self.assertEqual(
            [path.relative_to(root).as_posix() for path in paths],
            ["microtext/docs/sheet.pdf", "visualdiff/docs/board.pdf"],
        )

    def test_manifest_cad_archive_is_inventoried_without_extracted_members_or_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            intake = root / "microtext" / "docs" / "source_intake"
            archive = intake / "official_drawings.zip"
            selected = intake / "selected_dwg" / "page_000.dwg"
            summary = intake / "INTAKE_SUMMARY.json"
            selected.parent.mkdir(parents=True)
            archive.write_bytes(b"archive")
            selected.write_bytes(b"dwg")
            summary.write_text("{}", encoding="utf-8")
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "path": "microtext/docs/source_intake/official_drawings.zip",
                        "task": "microtext",
                        "doc_id": "official_drawings",
                        "domain": "pid",
                        "public_status": "public_domain_us_federal_candidate",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            rows = build_source_inventory.build_inventory(root)

        self.assertEqual([row["path"] for row in rows], [
            "microtext/docs/source_intake/official_drawings.zip",
        ])
        self.assertEqual(rows[0]["doc_id"], "official_drawings")
        self.assertEqual(rows[0]["render_status"], "missing")

    def test_ntrs_citation_receipt_is_not_treated_as_a_source_document(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            intake = root / "microtext" / "docs" / "source_intake"
            payload = intake / "nasa_report.pdf"
            citation = intake / "NTRS_CITATION_20205006355.json"
            pinned_receipt = intake / "nasa_report_NTRS.json"
            intake.mkdir(parents=True)
            payload.write_bytes(b"pdf")
            citation.write_text("{}", encoding="utf-8")
            pinned_receipt.write_text("{}", encoding="utf-8")

            rows = build_source_inventory.build_inventory(root)

        self.assertEqual([row["path"] for row in rows], [
            "microtext/docs/source_intake/nasa_report.pdf",
        ])


if __name__ == "__main__":
    unittest.main()
