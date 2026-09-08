import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_lunar_separation_supplemental_labels as module


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
        "page_index": 202,
        "image_path": "pages/page_202.png",
    }


TEST_SPECS = (
    module.spec(
        "PRODUCT STORAGE",
        "equipment_tag",
        module.fragment("product", 202, "/PRODUCT"),
        module.fragment("storage", 202, "STORAGE"),
    ),
    module.spec(
        "FLASH EVAP",
        "process_label",
        module.fragment("flash", 202, "FLASH"),
        module.fragment("evap", 202, "EVAP"),
    ),
)


class FilterNasaLunarSeparationSupplementalLabelsTests(unittest.TestCase):
    def test_curate_assembles_and_corrects_exact_fragments_in_test_split(self) -> None:
        rows = [
            row("product", "/PRODUCT", [100, 100, 300, 150], confidence=0.91),
            row("storage", "STORAGE", [100, 140, 300, 190], confidence=0.92),
            row("flash", "FLASH", [500, 300, 620, 350], confidence=0.94),
            row("evap", "EVAP", [500, 340, 620, 390], confidence=0.90),
            row("prose", "NASA header", [500, 500, 800, 560], confidence=0.99),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_202.png")
            selected, held, report = module.curate(rows, root=root, specs=TEST_SPECS)

        self.assertEqual(["PRODUCT STORAGE", "FLASH EVAP"], [item["target_text"] for item in selected])
        self.assertTrue(selected[0]["machine_text_assembled"])
        self.assertTrue(selected[0]["machine_text_corrected"])
        self.assertTrue(all(item["split"] == "test" for item in selected))
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        self.assertEqual("not_an_approved_lunar_supplemental_label", held[0]["machine_hold_reason"])
        self.assertEqual([], report["missing_specs"])

    def test_curate_fails_closed_on_prior_fragment_overlap(self) -> None:
        rows = [
            row("product", "/PRODUCT", [100, 100, 300, 150]),
            row("storage", "STORAGE", [100, 140, 300, 190]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_202.png")
            selected, _held, report = module.curate(
                rows,
                root=root,
                specs=TEST_SPECS[:1],
                excluded_candidate_ids={"storage"},
            )

        self.assertEqual([], selected)
        self.assertTrue(
            any("excluded_by_prior_selected_cohort" in issue for issue in report["missing_specs"][0]["issues"])
        )

    def test_curate_fails_closed_on_text_or_confidence_drift(self) -> None:
        rows = [
            row("product", "PRODUCT", [100, 100, 300, 150]),
            row("storage", "STORAGE", [100, 140, 300, 190], confidence=0.80),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_202.png")
            selected, _held, report = module.curate(rows, root=root, specs=TEST_SPECS[:1])

        self.assertEqual([], selected)
        issues = report["missing_specs"][0]["issues"]
        self.assertIn("text_mismatch:product", issues)
        self.assertTrue(any(issue.startswith("invalid:storage:below_confidence_floor") for issue in issues))

    def test_excluded_source_candidate_ids_reads_assembled_rows(self) -> None:
        rows = [
            {"candidate_id": "assembled", "source_candidate_ids": ["frag_a", "frag_b"]},
            {"candidate_id": "single"},
        ]
        self.assertEqual(
            {"assembled", "frag_a", "frag_b", "single"},
            module.excluded_source_candidate_ids(rows),
        )


if __name__ == "__main__":
    unittest.main()
