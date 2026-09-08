import csv
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

from tools.build_manual_region_mining_packet import build_packet


class BuildManualRegionMiningPacketTest(unittest.TestCase):
    def make_page(self, root: Path, doc_id: str, text: str) -> None:
        page_dir = root / "derived" / "pages_300dpi" / doc_id
        page_dir.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (420, 260), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 50, 370, 210), outline="black")
        draw.text((80, 100), text, fill="black")
        image.save(page_dir / "page_000.png")

    def write_readiness(self, root: Path) -> Path:
        readiness = root / "derived" / "quality" / "source_conversion_readiness_test.json"
        readiness.parent.mkdir(parents=True, exist_ok=True)
        readiness.write_text(
            json.dumps(
                {
                    "local_sources": [
                        {
                            "doc_id": "pid_ready",
                            "task": "microtext",
                            "domain": "pid",
                            "next_step": "extract_textlayer_or_ocr",
                            "source_path": "microtext/docs/pid_ready.pdf",
                            "rendered_pages": 1,
                            "textlayer_spans": 0,
                            "priority_score": 50,
                            "public_status": "public_candidate",
                        },
                        {
                            "doc_id": "cad_ready",
                            "task": "microtext",
                            "domain": "mechanical_cad",
                            "next_step": "ocr_or_manual_region_proposal",
                            "source_path": "",
                            "rendered_pages": 1,
                            "textlayer_spans": 0,
                            "priority_score": 40,
                            "public_status": "gpl_3_0_open_source_candidate",
                        },
                        {
                            "doc_id": "rights_blocked",
                            "task": "microtext",
                            "domain": "pid",
                            "next_step": "extract_textlayer_or_ocr",
                            "source_path": "microtext/docs/blocked.pdf",
                            "rendered_pages": 1,
                            "textlayer_spans": 0,
                            "priority_score": 999,
                            "public_status": "rights_uncertain",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return readiness

    def test_builds_flat_human_packet_from_rendered_machine_actionable_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_page(root, "pid_ready", "P-101")
            self.make_page(root, "cad_ready", "M3 TAP")
            readiness = self.write_readiness(root)
            packet_dir = root / "derived" / "human_adjudication" / "packet"
            zip_path = root / "derived" / "human_adjudication" / "packet.zip"

            report = build_packet(
                root=root,
                readiness_json=readiness,
                output_dir=packet_dir,
                zip_output=zip_path,
                date_label="test",
                max_docs=5,
                pages_per_doc=1,
                regions_per_page=3,
            )

            self.assertEqual(2, report["totals"]["selected_docs"])
            self.assertEqual(2, report["totals"]["selected_pages"])
            self.assertEqual(6, report["totals"]["checklist_rows"])
            self.assertEqual(1, report["totals"]["rights_blocked_docs"])
            self.assertTrue((packet_dir / "INTERN_INSTRUCTIONS_ZH.md").exists())
            self.assertTrue((packet_dir / "README.md").exists())
            self.assertTrue((packet_dir / "index.html").exists())
            self.assertTrue((packet_dir / "packet_manifest.json").exists())
            self.assertTrue((packet_dir / "pages" / "pid_ready_p0000.png").exists())

            instructions = (packet_dir / "INTERN_INSTRUCTIONS_ZH.md").read_text(encoding="utf-8")
            self.assertIn("bbox_xyxy", instructions)
            self.assertIn("不要修改文件名", instructions)

            with (packet_dir / "manual_region_mining_checklist.csv").open(
                newline="", encoding="utf-8"
            ) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(6, len(rows))
            self.assertEqual(
                {
                    "row_id",
                    "doc_id",
                    "domain",
                    "page_index",
                    "region_slot",
                    "page_image",
                    "source_path",
                    "category",
                    "transcribed_text",
                    "bbox_xyxy",
                    "status",
                    "notes",
                },
                set(rows[0].keys()),
            )
            self.assertEqual("pid_ready", rows[0]["doc_id"])
            self.assertEqual("pages/pid_ready_p0000.png", rows[0]["page_image"])

            self.assertTrue(zip_path.exists())
            with zipfile.ZipFile(zip_path) as zf:
                self.assertIsNone(zf.testzip())
                names = zf.namelist()
            self.assertIn("INTERN_INSTRUCTIONS_ZH.md", names)
            self.assertFalse(any(name.lower().endswith(".zip") for name in names))

    def test_cli_runs_when_invoked_as_script_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_page(root, "pid_ready", "P-101")
            readiness = self.write_readiness(root)
            script = Path.cwd() / "tools" / "build_manual_region_mining_packet.py"
            packet_dir = root / "packet"
            zip_path = root / "packet.zip"

            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--root",
                    str(root),
                    "--readiness-json",
                    str(readiness),
                    "--output-dir",
                    str(packet_dir),
                    "--zip-output",
                    str(zip_path),
                    "--date-label",
                    "test",
                    "--max-docs",
                    "1",
                    "--regions-per-page",
                    "1",
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
            )

            self.assertEqual("", result.stderr)
            self.assertEqual(0, result.returncode)
            self.assertTrue((packet_dir / "manual_region_mining_checklist.csv").exists())


if __name__ == "__main__":
    unittest.main()
