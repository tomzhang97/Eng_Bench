import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_epsdu_procurement_candidates as module


def write_png_header(path: Path, width: int = 2550, height: int = 3300) -> None:
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
    page: int = 47,
    confidence: float = 0.99,
    top: int = 500,
    category: str = "unknown_microtext",
) -> dict:
    return {
        "doc_id": module.EXPECTED_DOC_ID,
        "candidate_id": candidate_id,
        "category": category,
        "ocr_confidence": confidence,
        "proposed_text": text,
        "bbox": [500, top, 900, top + 60],
        "page_index": page,
        "image_path": f"pages/page_{page:03d}.png",
    }


class FilterNasaEpsduProcurementCandidatesTests(unittest.TestCase):
    def test_classify_keeps_complete_engineering_labels_only(self) -> None:
        self.assertEqual(
            ("equipment_tag", "recognized_equipment_phrase"),
            module.classify("Waste Gas Induction Blower", "equipment_tag"),
        )
        self.assertEqual(
            ("equipment_tag", "equipment_number"),
            module.classify("427-02", "unknown_microtext"),
        )
        self.assertEqual(
            ("process_label", "process_label_phrase"),
            module.classify("Field Instrumentation", "unknown_microtext"),
        )
        self.assertEqual((None, "purchase_order_number"), module.classify("50001", "unknown_microtext"))
        self.assertEqual((None, "generic_non_atomic_label"), module.classify("Valves", "equipment_tag"))

    def test_shortlist_enforces_confidence_geometry_and_source_uniqueness(self) -> None:
        rows = [
            row("blower", "Waste Gas Induction Blower", category="equipment_tag"),
            row("number", "427-02", top=650, confidence=1.0),
            row("process", "Field Instrumentation", top=800),
            row("duplicate", "Field Instrumentation", top=950),
            row("po", "50001", top=1100, confidence=1.0),
            row("generic", "Valves", top=1250, category="equipment_tag"),
            row("low", "Quench Condenser", top=1400, confidence=0.90, category="equipment_tag"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_047.png")
            selected, held, report = module.shortlist(rows, root=root)

        self.assertEqual(["blower", "number", "process"], [item["candidate_id"] for item in selected])
        self.assertEqual(
            ["equipment_tag", "equipment_tag", "process_label"],
            [item["category"] for item in selected],
        )
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        reasons = {item["candidate_id"]: item["machine_hold_reason"] for item in held}
        self.assertEqual("duplicate_normalized_text_in_source", reasons["duplicate"])
        self.assertEqual("purchase_order_number", reasons["po"])
        self.assertEqual("generic_non_atomic_label", reasons["generic"])
        self.assertEqual("below_confidence_floor", reasons["low"])
        self.assertEqual(3, report["selected_unique_normalized_text"])

    def test_outputs_remain_hash_bound_and_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_047.png")
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(row("one", "Quench Condenser", category="equipment_tag")) + "\n",
                encoding="utf-8",
            )
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
