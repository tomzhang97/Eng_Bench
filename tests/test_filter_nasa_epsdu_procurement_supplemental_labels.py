import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_epsdu_procurement_supplemental_labels as module


def write_png_header(path: Path, width: int = 1200, height: int = 1800) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def row(candidate_id: str, text: str, bbox: list[int], *, confidence: float = 0.99) -> dict:
    return {
        "doc_id": module.EXPECTED_DOC_ID,
        "version_id": "test",
        "candidate_id": candidate_id,
        "category": "unknown_microtext",
        "ocr_confidence": confidence,
        "proposed_text": text,
        "bbox": bbox,
        "page_index": 57,
        "image_path": "pages/page_057.png",
    }


TEST_SPECS = (
    module.spec(
        "Pressure Lubricators",
        "equipment_tag",
        module.fragment("pressure", 57, "Pressure"),
        module.fragment("lubricators", 57, "Lubricators"),
    ),
    module.spec(
        "Level Transmitters & Switches",
        "instrument_tag",
        module.fragment("transmitters", 57, "Lever Transmitters"),
        module.fragment("switches", 57, "& Switches"),
    ),
)


class FilterNasaEpsduProcurementSupplementalLabelsTests(unittest.TestCase):
    def test_curate_assembles_and_corrects_exact_source_fragments(self) -> None:
        rows = [
            row("pressure", "Pressure", [100, 100, 300, 150], confidence=0.999),
            row("lubricators", "Lubricators", [100, 140, 350, 200], confidence=0.998),
            row("transmitters", "Lever Transmitters", [100, 300, 450, 360], confidence=0.997),
            row("switches", "& Switches", [100, 350, 300, 410], confidence=0.996),
            row("prose", "Procurement status", [500, 500, 800, 560], confidence=0.999),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_057.png")
            selected, held, report = module.curate(rows, root=root, specs=TEST_SPECS)

        self.assertEqual(
            ["Pressure Lubricators", "Level Transmitters & Switches"],
            [item["target_text"] for item in selected],
        )
        self.assertTrue(all(item["machine_text_assembled"] for item in selected))
        self.assertTrue(all(item["split"] == "train" for item in selected))
        self.assertFalse(selected[0]["machine_text_corrected"])
        self.assertTrue(selected[1]["machine_text_corrected"])
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        self.assertEqual("not_an_approved_epsdu_supplemental_label", held[0]["machine_hold_reason"])
        self.assertEqual([], report["missing_specs"])

    def test_curate_fails_closed_on_text_or_confidence_drift(self) -> None:
        rows = [
            row("pressure", "Pressures", [100, 100, 300, 150], confidence=0.999),
            row("lubricators", "Lubricators", [100, 140, 350, 200], confidence=0.95),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_057.png")
            selected, _held, report = module.curate(rows, root=root, specs=TEST_SPECS[:1])

        self.assertEqual([], selected)
        self.assertEqual(1, len(report["missing_specs"]))
        self.assertIn("text_mismatch:pressure", report["missing_specs"][0]["issues"])
        self.assertTrue(any(issue.startswith("invalid:lubricators") for issue in report["missing_specs"][0]["issues"]))

    def test_direct_script_execution_writes_hash_bound_outputs(self) -> None:
        rows = [
            row("pressure", "Pressure", [100, 100, 300, 150]),
            row("lubricators", "Lubricators", [100, 140, 350, 200]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_057.png")
            input_path = root / "input.jsonl"
            input_path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
            selected_path = root / "selected.jsonl"
            held_path = root / "held.jsonl"
            report_path = root / "report.json"
            script = (
                "from pathlib import Path; import json; "
                "from tools import filter_nasa_epsdu_procurement_supplemental_labels as m; "
                f"root=Path({str(root)!r}); rows=m.read_jsonl(Path({str(input_path)!r})); "
                "selected,held,report=m.curate(rows,root=root,specs=m.TEST_SPECS if False else ("
                "m.spec('Pressure Lubricators','equipment_tag',m.fragment('pressure',57,'Pressure'),"
                "m.fragment('lubricators',57,'Lubricators')),)); "
                f"m.write_jsonl(Path({str(selected_path)!r}),selected); "
                f"m.write_jsonl(Path({str(held_path)!r}),held); "
                f"Path({str(report_path)!r}).write_text(json.dumps(report),encoding='utf-8')"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(1, len(module.read_jsonl(selected_path)))
            self.assertEqual(64, len(module.sha256(selected_path)))
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
