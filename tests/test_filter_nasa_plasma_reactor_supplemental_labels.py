import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_plasma_reactor_supplemental_labels as module


def write_png_header(path: Path, width: int = 2400, height: int = 3200) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def row(candidate_id: str, text: str, bbox: list[int], *, page: int = 82) -> dict:
    return {
        "doc_id": module.EXPECTED_DOC_ID,
        "source_candidate_id": module.EXPECTED_SOURCE_CANDIDATE_ID,
        "source": module.EXPECTED_SOURCE,
        "version_id": "unknown",
        "candidate_id": candidate_id,
        "category": "unknown_microtext",
        "proposed_text": text,
        "raw_text": text,
        "bbox": bbox,
        "page_index": page,
        "image_path": module._expected_image_path(page),
    }


TEST_SPECS = (
    module.spec(
        "Reactor heater power supply",
        "equipment_tag",
        module.fragment("power", 82, "Reactor heater power", [100, 100, 400, 150]),
        module.fragment("supply", 82, "supply", [200, 140, 300, 190]),
    ),
    module.spec(
        "0 to 75 psia",
        "process_value",
        module.fragment("range", 82, "0 to 75 psia", [500, 300, 700, 350]),
    ),
)


class FilterNasaPlasmaReactorSupplementalLabelsTests(unittest.TestCase):
    def test_curate_selects_exact_textlayer_fragments_in_train_split(self) -> None:
        rows = [
            row("power", "Reactor heater power", [100, 100, 400, 150]),
            row("supply", "supply", [200, 140, 300, 190]),
            row("range", "0 to 75 psia", [500, 300, 700, 350]),
            row("header", "Manufacturer", [500, 500, 800, 560]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / module._expected_image_path(82))
            selected, held, report = module.curate(rows, root=root, specs=TEST_SPECS)

        self.assertEqual(["Reactor heater power supply", "0 to 75 psia"], [item["target_text"] for item in selected])
        self.assertTrue(selected[0]["machine_text_assembled"])
        self.assertTrue(all(item["split"] == "train" for item in selected))
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        self.assertEqual("not_an_approved_plasma_supplemental_label", held[0]["machine_hold_reason"])
        self.assertEqual([], report["missing_specs"])

    def test_curate_fails_closed_on_prior_fragment_overlap(self) -> None:
        rows = [
            row("power", "Reactor heater power", [100, 100, 400, 150]),
            row("supply", "supply", [200, 140, 300, 190]),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / module._expected_image_path(82))
            selected, _held, report = module.curate(
                rows,
                root=root,
                specs=TEST_SPECS[:1],
                excluded_candidate_ids={"supply"},
            )

        self.assertEqual([], selected)
        self.assertTrue(any("excluded_by_prior_selected_cohort" in issue for issue in report["missing_specs"][0]["issues"]))

    def test_curate_fails_closed_on_text_bbox_or_source_drift(self) -> None:
        rows = [
            row("power", "Reactor heater power", [100, 100, 401, 150]),
            row("supply", "SUPPLY", [200, 140, 300, 190]),
        ]
        rows[0]["source"] = "ocr_candidate"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / module._expected_image_path(82))
            selected, _held, report = module.curate(rows, root=root, specs=TEST_SPECS[:1])

        self.assertEqual([], selected)
        issues = report["missing_specs"][0]["issues"]
        self.assertTrue(any("unexpected_candidate_source" in issue for issue in issues))
        self.assertIn("proposed_text_mismatch:supply", issues)
        self.assertIn("raw_text_mismatch:supply", issues)

    def test_curate_fails_closed_on_image_path_drift(self) -> None:
        rows = [row("range", "0 to 75 psia", [500, 300, 700, 350])]
        rows[0]["image_path"] = "derived/pages_300dpi/other/page_082.png"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / module._expected_image_path(82))
            selected, _held, report = module.curate(rows, root=root, specs=TEST_SPECS[1:])

        self.assertEqual([], selected)
        self.assertTrue(any("unexpected_page_image_path" in issue for issue in report["missing_specs"][0]["issues"]))

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
