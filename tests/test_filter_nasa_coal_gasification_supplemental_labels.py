import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_coal_gasification_supplemental_labels as module


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
        "page_index": 117,
        "image_path": "pages/page_117.png",
    }


TEST_SPECS = (
    module.spec(
        "ACID GAS FEED FROM ACID GAS REMOVAL",
        "process_label",
        module.fragment("feed", 117, "ACID GAS FEED"),
        module.fragment("from", 117, "FROM ACID"),
        module.fragment("removal", 117, "GAS REMOVAL"),
    ),
    module.spec("5-E-4", "equipment_tag", module.fragment("tag", 117, "5-E-4")),
)


class FilterNasaCoalGasificationSupplementalLabelsTests(unittest.TestCase):
    def test_curate_assembles_exact_source_fragments_in_test_split(self) -> None:
        rows = [
            row("feed", "ACID GAS FEED", [100, 100, 300, 150], confidence=0.91),
            row("from", "FROM ACID", [100, 140, 300, 190], confidence=0.92),
            row("removal", "GAS REMOVAL", [100, 180, 350, 230], confidence=0.93),
            row("tag", "5-E-4", [500, 300, 620, 350], confidence=0.94),
            row("prose", "Procurement status", [500, 500, 800, 560], confidence=0.99),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_117.png")
            selected, held, report = module.curate(rows, root=root, specs=TEST_SPECS)

        self.assertEqual(
            ["ACID GAS FEED FROM ACID GAS REMOVAL", "5-E-4"],
            [item["target_text"] for item in selected],
        )
        self.assertTrue(selected[0]["machine_text_assembled"])
        self.assertTrue(all(item["split"] == "test" for item in selected))
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        self.assertEqual("not_an_approved_coal_supplemental_label", held[0]["machine_hold_reason"])
        self.assertEqual([], report["missing_specs"])

    def test_curate_fails_closed_on_prior_cohort_overlap(self) -> None:
        rows = [
            row("feed", "ACID GAS FEED", [100, 100, 300, 150]),
            row("from", "FROM ACID", [100, 140, 300, 190]),
            row("removal", "GAS REMOVAL", [100, 180, 350, 230]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_117.png")
            selected, _held, report = module.curate(
                rows,
                root=root,
                specs=TEST_SPECS[:1],
                excluded_candidate_ids={"from"},
            )

        self.assertEqual([], selected)
        self.assertIn("excluded_by_prior_selected_cohort", report["missing_specs"][0]["issues"][0])

    def test_curate_fails_closed_on_text_or_confidence_drift(self) -> None:
        rows = [
            row("feed", "ACID GAS FEEDS", [100, 100, 300, 150]),
            row("from", "FROM ACID", [100, 140, 300, 190], confidence=0.80),
            row("removal", "GAS REMOVAL", [100, 180, 350, 230]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_117.png")
            selected, _held, report = module.curate(rows, root=root, specs=TEST_SPECS[:1])

        self.assertEqual([], selected)
        issues = report["missing_specs"][0]["issues"]
        self.assertIn("text_mismatch:feed", issues)
        self.assertTrue(any(issue.startswith("invalid:from:below_confidence_floor") for issue in issues))

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
