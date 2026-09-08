import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_local_source_conversion_actions import build_report, write_outputs


class BuildLocalSourceConversionActionsTest(unittest.TestCase):
    def write_readiness(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "totals": {
                        "date_label": "test",
                        "local_by_next_step": {
                            "extract_textlayer_or_ocr": 2,
                            "ocr_or_manual_region_proposal": 1,
                            "await_human_return": 1,
                        },
                    },
                    "local_sources": [
                        {
                            "doc_id": "pid_001",
                            "task": "microtext",
                            "domain": "pid",
                            "next_step": "extract_textlayer_or_ocr",
                            "source_path": "microtext/docs/pid_001.pdf",
                            "rendered_pages": 2,
                            "textlayer_spans": 0,
                            "priority_score": 20,
                            "public_status": "cc_by_sa_4_0_candidate",
                        },
                        {
                            "doc_id": "pid_rendered_no_pdf",
                            "task": "microtext",
                            "domain": "pid",
                            "next_step": "extract_textlayer_or_ocr",
                            "source_path": "",
                            "rendered_pages": 2,
                            "textlayer_spans": 0,
                            "priority_score": 10,
                            "public_status": "cc_by_sa_4_0_candidate",
                        },
                        {
                            "doc_id": "civil_001",
                            "task": "microtext",
                            "domain": "civil",
                            "next_step": "ocr_or_manual_region_proposal",
                            "source_path": "",
                            "rendered_pages": 3,
                            "textlayer_spans": 0,
                            "priority_score": 30,
                            "public_status": "public_domain_candidate",
                        },
                        {
                            "doc_id": "pcb_wait",
                            "task": "visualdiff",
                            "domain": "pcb_schematic",
                            "next_step": "await_human_return",
                            "source_path": "visualdiff/docs/pcb_wait.pdf",
                            "rendered_pages": 5,
                            "textlayer_spans": 100,
                            "priority_score": 500,
                            "public_status": "cc_by_sa_4_0_candidate",
                        },
                        {
                            "doc_id": "visualdiff_machine_row",
                            "task": "visualdiff",
                            "domain": "mechanical_cad",
                            "next_step": "extract_textlayer_or_ocr",
                            "source_path": "",
                            "rendered_pages": 1,
                            "textlayer_spans": 0,
                            "priority_score": 1000,
                            "public_status": "gpl_3_0_open_source_candidate",
                        },
                        {
                            "doc_id": "rights_block",
                            "task": "microtext",
                            "domain": "pid",
                            "next_step": "extract_textlayer_or_ocr",
                            "source_path": "microtext/docs/restricted.pdf",
                            "rendered_pages": 1,
                            "textlayer_spans": 0,
                            "priority_score": 100,
                            "public_status": "rights_uncertain",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_selects_machine_actionable_local_sources_and_skips_human_or_rights_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readiness = root / "readiness.json"
            self.write_readiness(readiness)

            report = build_report(root=root, readiness_json=readiness, batch_size=10, date_label="test")

            self.assertEqual(3, report["totals"]["selected_actions"])
            self.assertEqual(5, report["totals"]["machine_candidate_pool"])
            self.assertEqual(3, report["totals"]["machine_actionable_pool"])
            self.assertEqual(1, report["totals"]["rights_blocked_machine_rows"])
            self.assertEqual(1, report["totals"]["task_mismatch_machine_rows"])
            self.assertEqual(1, report["totals"]["human_blocked_rows"])
            selected_ids = [row["doc_id"] for row in report["selected_actions"]]
            self.assertEqual(["pid_001", "civil_001", "pid_rendered_no_pdf"], selected_ids)
            by_id = {row["doc_id"]: row for row in report["selected_actions"]}
            self.assertIn("propose_microtext_regions.py", by_id["civil_001"]["recommended_command"])
            self.assertIn("02_extract_textlayer.py", by_id["pid_001"]["recommended_command"])
            self.assertIn("--pdf_relpath microtext/docs/pid_001.pdf", by_id["pid_001"]["recommended_command"])
            self.assertIn("propose_microtext_regions.py", by_id["pid_rendered_no_pdf"]["recommended_command"])

    def test_excludes_doc_ids_from_reviewed_hold_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readiness = root / "readiness.json"
            self.write_readiness(readiness)
            hold_path = root / "holds.jsonl"
            hold_path.write_text(
                json.dumps({"doc_id": "pid_001", "hold_reasons": ["duplicate_page"]}) + "\n",
                encoding="utf-8",
            )

            report = build_report(
                root=root,
                readiness_json=readiness,
                batch_size=10,
                date_label="test",
                exclude_jsonl=[hold_path],
            )

            selected_ids = [row["doc_id"] for row in report["selected_actions"]]
            self.assertNotIn("pid_001", selected_ids)
            self.assertEqual(1, report["totals"]["explicit_hold_machine_rows"])

    def test_writes_json_markdown_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readiness = root / "readiness.json"
            self.write_readiness(readiness)
            report = build_report(root=root, readiness_json=readiness, batch_size=1, date_label="test")
            output_json = root / "out.json"
            output_md = root / "out.md"
            output_csv = root / "out.csv"

            write_outputs(report, output_json=output_json, output_md=output_md, output_csv=output_csv)

            self.assertTrue(output_json.exists())
            self.assertIn("Local Source Conversion Actions", output_md.read_text(encoding="utf-8"))
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(1, len(rows))
            self.assertEqual("pid_001", rows[0]["doc_id"])


if __name__ == "__main__":
    unittest.main()
