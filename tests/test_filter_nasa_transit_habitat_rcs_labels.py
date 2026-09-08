import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_transit_habitat_rcs_labels as module


def write_png_header(path: Path, width: int = 2550, height: int = 3300) -> None:
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
        "page_index": 20,
        "image_path": "pages/page_020.png",
    }


TEST_SPECS = (
    module.spec(
        "Pressurant Tank",
        "equipment_tag",
        20,
        (680, 1820, 900, 1940),
        "Pressurant",
        "Tank",
    ),
    module.spec(
        "Drawing Connection",
        "equipment_tag",
        20,
        (2040, 1885, 2280, 2010),
        "Dawing",
        "Connection",
    ),
)


class FilterNasaTransitHabitatRcsLabelsTests(unittest.TestCase):
    def test_curate_assembles_complete_labels_and_holds_duplicates(self) -> None:
        rows = [
            row("pressurant", "Pressurant", [702, 1841, 847, 1897], confidence=0.999),
            row("tank", "Tank", [703, 1872, 792, 1927], confidence=0.998),
            row("drawing", "Dawing", [2084, 1909, 2200, 1972], confidence=0.997),
            row("connection", "Connection", [2087, 1946, 2238, 1997], confidence=0.996),
            row("connection_dup", "Connection", [2088, 1946, 2238, 1997], confidence=0.980),
            row("prose", "paragraph", [100, 100, 300, 150], confidence=0.999),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            selected, held, report = module.curate(rows, root=root, specs=TEST_SPECS)

        self.assertEqual(["Pressurant Tank", "Drawing Connection"], [r["target_text"] for r in selected])
        self.assertEqual([2, 2], [len(r["source_candidate_ids"]) for r in selected])
        self.assertTrue(all(r["safe_to_merge_gold"] is False for r in selected))
        drawing = next(r for r in selected if r["target_text"] == "Drawing Connection")
        self.assertTrue(drawing["machine_text_assembled"])
        self.assertTrue(drawing["machine_text_corrected"])
        reasons = {r["candidate_id"]: r["machine_hold_reason"] for r in held}
        self.assertEqual("duplicate_fragment_candidate", reasons["connection_dup"])
        self.assertEqual("not_a_complete_supported_rcs_non_tag_label", reasons["prose"])
        self.assertEqual([], report["missing_specs"])

    def test_curate_fails_closed_when_a_required_fragment_is_missing(self) -> None:
        rows = [row("pressurant", "Pressurant", [702, 1841, 847, 1897])]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            selected, held, report = module.curate(rows, root=root, specs=TEST_SPECS[:1])

        self.assertEqual([], selected)
        self.assertEqual(1, len(held))
        self.assertEqual(["Tank"], report["missing_specs"][0]["missing_fragments"])
        self.assertFalse(report["safe_to_merge_gold"])

    def test_direct_script_execution_writes_hash_bound_outputs(self) -> None:
        rows = [
            row("pressurant", "Pressurant", [702, 1841, 847, 1897]),
            row("tank", "Tank", [703, 1872, 792, 1927]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            input_path = root / "input.jsonl"
            input_path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
            selected_path = root / "selected.jsonl"
            held_path = root / "held.jsonl"
            report_path = root / "report.json"
            one_spec_script = (
                "from pathlib import Path; import json; "
                "from tools import filter_nasa_transit_habitat_rcs_labels as m; "
                f"root=Path({str(root)!r}); "
                f"rows=m.read_jsonl(Path({str(input_path)!r})); "
                "selected,held,report=m.curate(rows,root=root,specs=m.DEFAULT_SPECS[:1]); "
                f"m.write_jsonl(Path({str(selected_path)!r}),selected); "
                f"m.write_jsonl(Path({str(held_path)!r}),held); "
                f"Path({str(report_path)!r}).write_text(json.dumps(report),encoding='utf-8')"
            )
            completed = subprocess.run(
                [sys.executable, "-c", one_spec_script],
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
