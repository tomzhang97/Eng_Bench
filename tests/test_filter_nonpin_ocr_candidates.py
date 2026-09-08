from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import filter_nonpin_ocr_candidates as subject


def row(
    candidate_id: str,
    text: str,
    category: str,
    bbox: list[int],
    confidence: float = 0.99,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "doc_id": "doc",
        "page_index": 0,
        "bbox": bbox,
        "proposed_text": text,
        "category": category,
        "ocr_confidence": confidence,
        "safe_to_merge_gold": False,
    }


class FilterNonpinOcrCandidatesTest(unittest.TestCase):
    def test_keeps_recognized_nonpin_and_holds_pin(self) -> None:
        selected, held, report = subject.shortlist(
            [
                row("a", "PT-403", "instrument_tag", [0, 0, 20, 10]),
                row("b", "GPIO17", "pin_label", [30, 0, 50, 10]),
            ],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual([item["candidate_id"] for item in selected], ["a"])
        self.assertEqual(len(held), 1)
        self.assertEqual(report["selected_by_category"], {"instrument_tag": 1})

    def test_rescues_explicit_legacy_tags_and_process_labels(self) -> None:
        selected, _held, _report = subject.shortlist(
            [
                row("a", "SOV17", "unknown_microtext", [0, 0, 20, 10]),
                row("b", "HX2", "unknown_microtext", [30, 0, 50, 10]),
                row("c", "GROUND FILL", "unknown_microtext", [60, 0, 100, 10]),
                row("d", "P = 450", "unknown_microtext", [110, 0, 150, 10]),
            ],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(
            {item["category"] for item in selected},
            {"instrument_tag", "equipment_tag", "process_label", "process_value"},
        )

    def test_uses_engineering_lexicon_to_correct_generic_categories(self) -> None:
        selected, _held, _report = subject.shortlist(
            [
                row("a", "FLOW CONTROL VALVE", "process_label", [0, 0, 100, 10]),
                row("b", "PRESSURE TRANSDUCER", "process_label", [120, 0, 240, 10]),
                row("c", "FC380", "equipment_tag", [260, 0, 320, 10]),
            ],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(
            [item["category"] for item in selected],
            ["equipment_tag", "instrument_tag", "instrument_tag"],
        )

    def test_holds_caption_fragments_ending_in_schematic(self) -> None:
        selected, held, _report = subject.shortlist(
            [row("a", "atier reactor subsystem schematic", "equipment_tag", [0, 0, 200, 10])],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(selected, [])
        self.assertEqual(held[0]["machine_hold_reason"], "text_quality_or_narrative_hold")

    def test_holds_corrupt_low_confidence_and_narrative_text(self) -> None:
        selected, held, report = subject.shortlist(
            [
                row("a", "REC�IVER TANK", "equipment_tag", [0, 0, 20, 10]),
                row("b", "PT-7", "instrument_tag", [30, 0, 50, 10], 0.50),
                row(
                    "c",
                    "The system was used to monitor the pressure in the facility",
                    "unknown_microtext",
                    [60, 0, 200, 10],
                ),
            ],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(selected, [])
        self.assertEqual(len(held), 3)
        self.assertEqual(sum(report["hold_reasons"].values()), 3)

    def test_prefers_longer_overlapping_tile_text(self) -> None:
        selected, held, report = subject.shortlist(
            [
                row("short", "RECEIVER TANK HELIUM B", "equipment_tag", [0, 0, 100, 20]),
                row("full", "RECEIVER TANK HELIUM BOTTLES", "equipment_tag", [0, 0, 120, 20]),
            ],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual([item["candidate_id"] for item in selected], ["full"])
        self.assertEqual(held[0]["machine_hold_reason"], "overlapping_tile_duplicate")
        self.assertEqual(report["input_rows"], 2)

    def test_collapses_overlapping_suffix_and_ocr_confusion_across_categories(self) -> None:
        selected, held, _report = subject.shortlist(
            [
                row("full", "Exhaust valve", "equipment_tag", [0, 0, 120, 20]),
                row("suffix", "haust valve", "equipment_tag", [10, 0, 120, 20]),
                row("fo4", "FO4", "instrument_tag", [200, 0, 240, 20]),
                row("f04", "F04", "equipment_tag", [201, 0, 241, 20]),
            ],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(
            {item["candidate_id"] for item in selected},
            {"full", "f04"},
        )
        self.assertEqual(
            {item["candidate_id"] for item in held},
            {"suffix", "fo4"},
        )

    def test_repeated_answer_cap_is_per_page(self) -> None:
        rows = [row(str(index), "TC", "unknown_microtext", [index * 30, 0, index * 30 + 20, 10]) for index in range(5)]
        selected, held, _report = subject.shortlist(
            rows,
            min_confidence=0.86,
            max_per_answer_page=2,
        )
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(held), 3)
        self.assertTrue(all(item["machine_hold_reason"] == "repeated_answer_cap" for item in held))

    def test_exact_textlayer_uses_distinct_machine_confidence(self) -> None:
        candidate = row("a", "PUMP", "process_label", [0, 0, 40, 20], 0.0)
        candidate.update(
            {
                "source": "textlayer_full_span_candidate",
                "raw_text": "PUMP",
                "target_text": "PUMP",
            }
        )
        selected, held, _report = subject.shortlist(
            [candidate],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(held, [])
        self.assertEqual(selected[0]["category"], "equipment_tag")
        self.assertEqual(selected[0]["machine_evidence_confidence"], 1.0)
        self.assertEqual(
            selected[0]["machine_evidence_confidence_source"],
            "exact_embedded_textlayer",
        )

    def test_holds_broad_textlayer_process_phrase(self) -> None:
        candidate = row("a", "system performance", "process_label", [0, 0, 100, 20], 0.0)
        candidate.update(
            {
                "source": "textlayer_full_span_candidate",
                "raw_text": "system performance",
                "target_text": "system performance",
            }
        )
        selected, held, _report = subject.shortlist(
            [candidate],
            min_confidence=0.86,
            max_per_answer_page=4,
        )
        self.assertEqual(selected, [])
        self.assertEqual(held[0]["machine_hold_reason"], "textlayer_process_label_not_explicit")

    def test_jsonl_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            rows = [row("a", "PT-1", "instrument_tag", [0, 0, 20, 10])]
            subject.write_jsonl(path, rows)
            self.assertEqual(subject.read_jsonl(path), rows)


if __name__ == "__main__":
    unittest.main()
