import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_cartridge_tolerance_candidates as module


def write_png_header(path: Path, width: int = 1000, height: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


def row(
    candidate_id: str,
    *,
    category: str = "tolerance_value",
    confidence: float = 0.99,
    text: str = "\u00b10.1",
    bbox: list[int] | None = None,
) -> dict:
    return {
        "doc_id": module.EXPECTED_DOC_ID,
        "candidate_id": candidate_id,
        "category": category,
        "ocr_confidence": confidence,
        "proposed_text": text,
        "bbox": bbox or [450, 100, 550, 150],
        "page_index": 21,
        "image_path": "pages/page_021.png",
    }


class FilterNasaCartridgeToleranceCandidatesTests(unittest.TestCase):
    def test_strict_filter_selects_only_confident_tolerance_columns(self) -> None:
        rows = [
            row("left"),
            row("right", bbox=[760, 200, 840, 250]),
            row("category", category="dimension_value"),
            row("confidence", confidence=0.979),
            row("middle", bbox=[600, 300, 660, 350]),
            row("token", text="0.1"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_021.png")
            selected, held, report = module.shortlist(
                rows,
                root=root,
                min_confidence=0.98,
            )

        self.assertEqual(["left", "right"], [item["candidate_id"] for item in selected])
        self.assertTrue(all(item["safe_to_merge_gold"] is False for item in selected))
        self.assertTrue(
            all(item["machine_qa_status"].endswith("pending_visual_qa") for item in selected)
        )
        reasons = {item["candidate_id"]: item["machine_hold_reason"] for item in held}
        self.assertEqual("non_tolerance_category", reasons["category"])
        self.assertEqual("below_confidence_floor", reasons["confidence"])
        self.assertEqual("outside_tolerance_columns", reasons["middle"])
        self.assertEqual("invalid_tolerance_token", reasons["token"])
        self.assertEqual(2, report["selected_rows"])
        self.assertEqual(4, report["held_rows"])

    def test_cli_outputs_are_hash_bound_and_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_021.png")
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(row("candidate"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            selected_path = root / "selected.jsonl"
            held_path = root / "held.jsonl"
            report_path = root / "report.json"
            selected, held, report = module.shortlist(
                module.read_jsonl(input_path),
                root=root,
                min_confidence=0.98,
            )
            module.write_jsonl(selected_path, selected)
            module.write_jsonl(held_path, held)
            report.update(
                {
                    "input_sha256": module.sha256(input_path),
                    "selected_sha256": module.sha256(selected_path),
                    "held_sha256": module.sha256(held_path),
                }
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")

            loaded = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(1, loaded["selected_rows"])
        self.assertFalse(loaded["safe_to_merge_gold"])
        self.assertEqual(64, len(loaded["selected_sha256"]))


if __name__ == "__main__":
    unittest.main()
