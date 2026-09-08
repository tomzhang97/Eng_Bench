import json
import tempfile
import unittest
from pathlib import Path

from tools.mine_microtext_candidates import (
    manifest_source_candidate_ids,
    manifest_version_ids,
    mine_rows,
    repair_common_mojibake,
    usable_process_label,
)


def row(text: str, x0: int) -> dict:
    return {
        "page": 0,
        "text": text,
        "bbox_px": [x0, 10, x0 + 200, 60],
    }


class MineMicrotextCandidateIdTest(unittest.TestCase):
    def test_process_label_filter_keeps_labels_and_rejects_document_prose(self) -> None:
        accepted = ["Amine treating", "Gas flare", "Reflux", "Water", "To water treatment", "CH4"]
        rejected = [
            "Page 10",
            "Figure 6 Example P&ID",
            "Not to Scale",
            "City of San Diego",
            "STANDARD DRAWING",
            "adjustable from SCADA.",
            "and/or",
            "F = flow",
            "gpm",
        ]

        for value in accepted:
            with self.subTest(value=value):
                self.assertTrue(usable_process_label(value))
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(usable_process_label(value))

    def test_resistor_reference_designator_is_a_pin_label(self) -> None:
        candidates = mine_rows(
            [row("R131", 100)],
            doc_id="pcb_sheet",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "pin_label")

    def test_polarized_component_label_keeps_suffix(self) -> None:
        candidates = mine_rows(
            [row("C3+", 100), row("C2-", 300)],
            doc_id="pcb_sheet",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(
            [item["target_text"] for item in candidates],
            ["C3+", "C2-"],
        )
        self.assertTrue(all(item["category"] == "pin_label" for item in candidates))

    def test_svg_text_markup_is_not_mined_as_a_dimension(self) -> None:
        candidates = mine_rows(
            [row('<tspan x="0" y="-1.587500">GPIO14</tspan>', 100)],
            doc_id="schematic_svg",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(candidates, [])

    def test_hyphenated_room_label_keeps_the_complete_label(self) -> None:
        candidates = mine_rows(
            [row("living-room", 100)],
            doc_id="floor_plan",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "room_label")
        self.assertEqual(candidates[0]["target_text"], "living-room")

    def test_incomplete_compound_room_label_is_rejected(self) -> None:
        candidates = mine_rows(
            [row("store-closet,", 100), row("kitchen.", 300)],
            doc_id="floor_plan",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual([item["target_text"] for item in candidates], ["kitchen"])

    def test_uppercase_resistor_value_is_not_a_metre_dimension(self) -> None:
        candidates = mine_rows(
            [row("1M", 100), row("1 m", 300)],
            doc_id="schematic_sheet",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(
            [(item["target_text"], item["category"]) for item in candidates],
            [("1M", "component_value"), ("1 m", "dimension_value")],
        )

    def test_rgb_format_name_is_not_an_instrument_tag(self) -> None:
        candidates = mine_rows(
            [row("RP2040 can process RGB-565 data", 100)],
            doc_id="datasheet",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertFalse(any(item["category"] == "instrument_tag" for item in candidates))

    def test_pipe_line_tag_keeps_alpha_system_prefix(self) -> None:
        candidates = mine_rows(
            [row("SSW-106-01-G-S", 100)],
            doc_id="schematic_sheet",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "pipe_line_tag")
        self.assertEqual(candidates[0]["target_text"], "SSW-106-01-G-S")

    def test_fingerprint_ids_do_not_shift_when_an_unrelated_row_is_inserted(self) -> None:
        original = mine_rows(
            [row("FT-101", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )
        expanded = mine_rows(
            [row("7 bar", 300), row("FT-101", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )

        original_id = original[0]["candidate_id"]
        expanded_id = next(item["candidate_id"] for item in expanded if item["target_text"] == "FT-101")

        self.assertEqual(original_id, expanded_id)
        self.assertIn("__fp_", original_id)

    def test_fingerprint_ids_change_with_candidate_content_or_region(self) -> None:
        first = mine_rows(
            [row("FT-101", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )[0]
        moved = mine_rows(
            [row("FT-101", 200)],
            doc_id="pid_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )[0]
        changed = mine_rows(
            [row("FT-102", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )[0]

        self.assertNotEqual(first["candidate_id"], moved["candidate_id"])
        self.assertNotEqual(first["candidate_id"], changed["candidate_id"])

    def test_sequential_ids_remain_the_backward_compatible_default(self) -> None:
        candidate = mine_rows([row("FT-101", 100)], doc_id="pid_sheet", version_id="v1")[0]

        self.assertTrue(candidate["candidate_id"].endswith("__000000"))

    def test_manifest_source_candidate_id_is_available_for_nonprefixed_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "type": "doc",
                    "doc_id": "wikimedia_process_diagram",
                    "source_candidate_id": "pid_031",
                },
                {"type": "split", "name": "test"},
            ]
            (root / "manifest.jsonl").write_text(
                "".join(json.dumps(item) + "\n" for item in rows),
                encoding="utf-8",
            )

            self.assertEqual(
                manifest_source_candidate_ids(root),
                {"wikimedia_process_diagram": "pid_031"},
            )

    def test_manifest_version_id_is_normalized_for_registered_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "type": "doc",
                    "doc_id": "registered_schematic",
                    "version": {"version_id": "Opta Schematic 2024-01-30"},
                },
                {"type": "split", "name": "test"},
            ]
            (root / "manifest.jsonl").write_text(
                "".join(json.dumps(item) + "\n" for item in rows),
                encoding="utf-8",
            )

            self.assertEqual(
                manifest_version_ids(root),
                {"registered_schematic": "opta_schematic_2024_01_30"},
            )

    def test_manifest_version_keys_match_review_queue_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "type": "doc",
                    "doc_id": "historical_book",
                    "version": {"publication_year": "1898"},
                },
                {
                    "type": "doc",
                    "doc_id": "federal_standard",
                    "version": {"standard": "W617-83", "edition": "FP-24"},
                },
                {
                    "type": "doc",
                    "doc_id": "legacy_revision",
                    "version": {"rev": "CAT31315198"},
                },
                {
                    "type": "doc",
                    "doc_id": "commons_asset",
                    "version": {"commons_sha1": "6762199027e728e538ce1a8c6881c1e09ea3208a"},
                },
            ]
            (root / "manifest.jsonl").write_text(
                "".join(json.dumps(item) + "\n" for item in rows),
                encoding="utf-8",
            )

            self.assertEqual(
                manifest_version_ids(root),
                {
                    "commons_asset": "6762199027e728e538ce1a8c6881c1e09ea3208a",
                    "federal_standard": "fp_24",
                    "historical_book": "1898",
                    "legacy_revision": "cat31315198",
                },
            )

    def test_opt_in_multi_match_recovers_temperature_and_pressure(self) -> None:
        candidates = mine_rows(
            [row("Agitation, 50 Â°C, 7 bar", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
            include_all_profile_matches=True,
            repair_mojibake=True,
        )

        self.assertEqual([item["target_text"] for item in candidates], ["50 °C", "7 bar"])
        self.assertTrue(all(item["source_raw_text"] == "Agitation, 50 Â°C, 7 bar" for item in candidates))

    def test_default_single_match_behavior_still_prefers_existing_pressure_profile(self) -> None:
        candidates = mine_rows(
            [row("Agitation, 50 Â°C, 7 bar", 100)],
            doc_id="pid_sheet",
            version_id="v1",
        )

        self.assertEqual([item["target_text"] for item in candidates], ["7 bar"])

    def test_mojibake_repair_is_noop_for_clean_text(self) -> None:
        self.assertEqual(repair_common_mojibake("Séchage à 100 °C"), "Séchage à 100 °C")
        self.assertEqual(repair_common_mojibake("SÃ©chage Ã  100 Â°C"), "Séchage à 100 °C")

    def test_opt_in_match_padding_expands_substring_crop(self) -> None:
        narrow = mine_rows(
            [row("Agitation, 50 °C", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            include_all_profile_matches=True,
            match_bbox_pad_px=8,
        )[0]
        padded = mine_rows(
            [row("Agitation, 50 °C", 100)],
            doc_id="pid_sheet",
            version_id="v1",
            include_all_profile_matches=True,
            match_bbox_pad_px=48,
        )[0]

        self.assertLess(padded["bbox"][0], narrow["bbox"][0])
        self.assertGreater(padded["bbox"][2], narrow["bbox"][2])

    def test_opt_in_full_span_padding_expands_exact_crop(self) -> None:
        legacy = mine_rows(
            [row("GND", 100)],
            doc_id="schematic",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )[0]
        padded = mine_rows(
            [row("GND", 100)],
            doc_id="schematic",
            version_id="v1",
            candidate_id_mode="fingerprint",
            full_span_bbox_pad_px=12,
        )[0]

        self.assertEqual(legacy["bbox"], [100, 10, 300, 60])
        self.assertEqual(padded["bbox"], [88, 0, 312, 72])
        self.assertNotEqual(padded["candidate_id"], legacy["candidate_id"])
        self.assertEqual(padded["pre_padding_candidate_id"], legacy["candidate_id"])

    def test_full_span_padding_can_be_anisotropic(self) -> None:
        padded = mine_rows(
            [row("GND", 100)],
            doc_id="schematic",
            version_id="v1",
            candidate_id_mode="fingerprint",
            full_span_bbox_pad_x_px=8,
            full_span_bbox_pad_y_px=2,
        )[0]

        self.assertEqual(padded["bbox"], [92, 8, 308, 62])

    def test_dimension_profile_accepts_curly_engineering_prime(self) -> None:
        candidates = mine_rows(
            [row("20’ MIN.", 100)],
            doc_id="civil_sheet",
            version_id="v1",
            candidate_id_mode="fingerprint",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "dimension_value")
        self.assertEqual(candidates[0]["target_text"], "20’")
