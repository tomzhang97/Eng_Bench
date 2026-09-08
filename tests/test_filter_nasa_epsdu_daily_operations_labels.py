import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_epsdu_daily_operations_labels as module


def write_png_header(path: Path, width: int = 3300, height: int = 2688) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def row(
    candidate_id: str,
    text: str,
    *,
    page: int = 20,
    confidence: float = 0.95,
    top: int = 500,
) -> dict:
    return {
        "doc_id": module.EXPECTED_DOC_ID,
        "candidate_id": candidate_id,
        "category": "unknown_microtext",
        "ocr_confidence": confidence,
        "proposed_text": text,
        "bbox": [500, top, 900, top + 60],
        "page_index": page,
        "image_path": f"pages/page_{page:03d}.png",
    }


class FilterNasaEpsduDailyOperationsLabelsTests(unittest.TestCase):
    def test_shortlist_corrects_allowlisted_labels_and_holds_placeholders(self) -> None:
        rows = [
            row("utilities", "UTILITIES USAGE"),
            row("vaporizer", "VAPOR!ZOR", top=650),
            row("placeholder", "1000.0", top=800, confidence=1.0),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            selected, held, report = module.shortlist(rows, root=root)

        self.assertEqual(["Utilities Usage", "Vaporizer"], [r["proposed_text"] for r in selected])
        self.assertEqual(["process_label", "equipment_tag"], [r["category"] for r in selected])
        self.assertEqual(
            "unsupported_or_placeholder_detection",
            {r["candidate_id"]: r["machine_hold_reason"] for r in held}["placeholder"],
        )
        self.assertEqual(0, report["placeholder_values_retained"])
        self.assertTrue(all(r["safe_to_merge_gold"] is False for r in selected))

    def test_duplicate_canonical_label_uses_highest_confidence_region(self) -> None:
        rows = [
            row("lower", "RECYCLE(LP/HR)", page=19, confidence=0.90),
            row("higher", "RECYCLE(LA/HR)", page=19, confidence=0.99, top=700),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_019.png")
            selected, held, report = module.shortlist(rows, root=root)

        self.assertEqual(["higher"], [r["candidate_id"] for r in selected])
        self.assertEqual("Recycle (lb/hr)", selected[0]["proposed_text"])
        self.assertEqual(
            "duplicate_canonical_label_in_source",
            {r["candidate_id"]: r["machine_hold_reason"] for r in held}["lower"],
        )
        self.assertEqual(1, report["selected_unique_canonical_labels"])

    def test_source_page_confidence_and_geometry_are_enforced(self) -> None:
        rows = [
            row("good", "COMB AIR", page=22),
            row("low", "ARGON", page=22, confidence=0.50, top=700),
            row("wrong-page", "NITROGEN", page=30, top=900),
            {**row("bad-box", "C WATER", page=20, top=1100), "bbox": [10, 10, 5, 20]},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            write_png_header(root / "pages/page_022.png")
            selected, held, _ = module.shortlist(rows, root=root)

        self.assertEqual(["good"], [r["candidate_id"] for r in selected])
        reasons = {r["candidate_id"]: r["machine_hold_reason"] for r in held}
        self.assertEqual("below_confidence_floor", reasons["low"])
        self.assertEqual("outside_selected_operations_pages", reasons["wrong-page"])
        self.assertEqual("invalid_bbox", reasons["bad-box"])

    def test_outputs_remain_hash_bound_and_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_020.png")
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(row("one", "ATOMIC MASS FLOW")) + "\n",
                encoding="utf-8",
            )
            selected, held, report = module.shortlist(module.read_jsonl(input_path), root=root)
            selected_path = root / "selected.jsonl"
            held_path = root / "held.jsonl"
            module.write_jsonl(selected_path, selected)
            module.write_jsonl(held_path, held)
            digest = module.sha256(selected_path)

        self.assertEqual(1, report["selected_rows"])
        self.assertFalse(report["safe_to_merge_gold"])
        self.assertEqual(64, len(digest))


if __name__ == "__main__":
    unittest.main()
