import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_transit_habitat_rcs_tags as module


def write_png_header(path: Path, width: int = 2550, height: int = 3300) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def row(candidate_id: str, text: str, *, page: int = 20, confidence: float = 0.99, top: int = 500) -> dict:
    return {
        "doc_id": module.EXPECTED_DOC_ID,
        "candidate_id": candidate_id,
        "category": "unknown_microtext",
        "ocr_confidence": confidence,
        "proposed_text": text,
        "bbox": [500, top, 610, top + 50],
        "page_index": page,
        "image_path": f"pages/page_{page:03d}.png",
    }


class FilterNasaTransitHabitatRcsTagsTests(unittest.TestCase):
    def test_normalized_token_accepts_only_known_engineering_sequences(self) -> None:
        self.assertEqual(("OIV102", "OIV102", False), module.normalized_token("OIV102"))
        self.assertEqual(("0IV112", "OIV112", True), module.normalized_token("0IV112"))
        self.assertEqual(("QTP114", "OTP114", True), module.normalized_token("QTP114"))
        self.assertIsNone(module.normalized_token("OHV115"))
        self.assertIsNone(module.normalized_token("paragraph"))

    def test_shortlist_filters_geometry_confidence_and_source_duplicates(self) -> None:
        rows = [
            row("exact", "OIV102", confidence=0.98),
            row("same_text", "OIV102", confidence=0.96, top=620),
            row("corrected", "0IV112", confidence=0.95, top=700),
            row("low_corrected", "QTP114", confidence=0.87, top=800),
            row("odd", "OHV115", top=900),
            row("prose", "OIV104", top=2500),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            selected, held, report = module.shortlist(rows, root=root)

        self.assertEqual(["exact", "corrected"], [item["candidate_id"] for item in selected])
        self.assertEqual(["OIV102", "OIV112"], [item["proposed_text"] for item in selected])
        self.assertTrue(all(item["category"] == "instrument_tag" for item in selected))
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        reasons = {item["candidate_id"]: item["machine_hold_reason"] for item in held}
        self.assertEqual("duplicate_normalized_text_in_source", reasons["same_text"])
        self.assertEqual("below_confidence_floor", reasons["low_corrected"])
        self.assertEqual("not_a_supported_rcs_tag", reasons["odd"])
        self.assertEqual("outside_verified_figure_window", reasons["prose"])
        self.assertEqual(2, report["selected_unique_normalized_tags"])

    def test_cli_style_outputs_remain_hash_bound_and_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            input_path = root / "input.jsonl"
            input_path.write_text(json.dumps(row("one", "OIV102")) + "\n", encoding="utf-8")
            selected, held, report = module.shortlist(module.read_jsonl(input_path), root=root)
            selected_path = root / "selected.jsonl"
            held_path = root / "held.jsonl"
            module.write_jsonl(selected_path, selected)
            module.write_jsonl(held_path, held)
            selected_sha256 = module.sha256(selected_path)

        self.assertEqual(1, report["selected_rows"])
        self.assertFalse(report["safe_to_merge_gold"])
        self.assertEqual(64, len(selected_sha256))


if __name__ == "__main__":
    unittest.main()
