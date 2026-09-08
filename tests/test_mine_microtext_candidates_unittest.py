import unittest
import json
import tempfile
from pathlib import Path

from PIL import Image

from tools import mine_microtext_candidates


class MineMicrotextCandidatesUnittest(unittest.TestCase):
    def test_component_value_profile_accepts_full_span_electrical_values(self) -> None:
        rows = [
            {"page": 0, "text": value, "bbox_px": [0, index * 20, 60, index * 20 + 10]}
            for index, value in enumerate(
                ["0.1uF", "10\u00b5F", "2.7nH", "330R", "2.2k", "4K7"]
            )
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="schematic_doc",
            version_id="v1",
        )

        self.assertEqual(
            [(row["category"], row["target_text"]) for row in candidates],
            [("component_value", row["text"]) for row in rows],
        )

    def test_component_value_profile_rejects_dimensions_and_prose_fragments(self) -> None:
        rows = [
            {"page": 0, "text": "1 m", "bbox_px": [0, 0, 60, 10]},
            {"page": 0, "text": "20mm", "bbox_px": [0, 20, 60, 30]},
            {"page": 0, "text": "use 10k resistor", "bbox_px": [0, 40, 120, 50]},
            {"page": 0, "text": "9774065151R", "bbox_px": [0, 60, 120, 70]},
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="mixed_doc",
            version_id="v1",
        )

        self.assertFalse(any(row["category"] == "component_value" for row in candidates))

    def test_rescales_metadata_bboxes_to_current_page_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer_dir = root / "derived" / "textlayer"
            page_dir = root / "derived" / "pages_300dpi" / "demo_doc"
            textlayer_dir.mkdir(parents=True)
            page_dir.mkdir(parents=True)
            Image.new("RGB", (200, 300), "white").save(page_dir / "page_000.png")
            row = {
                "page": 0,
                "text": "PIT-101",
                "bbox_px": [10, 10, 20, 20],
                "image_width_px": 100,
                "image_height_px": 100,
                "bbox_coordinate_space": "rendered_image_px",
            }
            (textlayer_dir / "demo_doc.jsonl").write_text(
                json.dumps(row) + "\n",
                encoding="utf-8",
            )

            candidates = mine_microtext_candidates.mine_textlayers(
                root,
                max_per_doc_category=10,
                doc_ids={"demo_doc"},
                categories={"instrument_tag"},
                exact_span_only=True,
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["bbox"], [20, 30, 40, 60])

    def test_page_and_answer_caps_preserve_diversity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer_dir = root / "derived" / "textlayer"
            textlayer_dir.mkdir(parents=True)
            rows = [
                {"page": 0, "text": "GND", "bbox_px": [0, 0, 20, 10]},
                {"page": 0, "text": "GND", "bbox_px": [0, 20, 20, 30]},
                {"page": 0, "text": "PA5", "bbox_px": [30, 0, 50, 10]},
                {"page": 1, "text": "PB6", "bbox_px": [0, 0, 20, 10]},
                {"page": 1, "text": "PC7", "bbox_px": [30, 0, 50, 10]},
            ]
            (textlayer_dir / "demo_doc.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            candidates = mine_microtext_candidates.mine_textlayers(
                root,
                max_per_doc_category=10,
                doc_ids={"demo_doc"},
                categories={"pin_label"},
                exact_span_only=True,
                max_per_doc_page_category=2,
                max_per_answer=1,
            )

            self.assertEqual(
                [(row["page_index"], row["target_text"]) for row in candidates],
                [(0, "GND"), (0, "PA5"), (1, "PB6"), (1, "PC7")],
            )

    def test_zero_doc_category_cap_disables_the_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer_dir = root / "derived" / "textlayer"
            textlayer_dir.mkdir(parents=True)
            rows = [
                {"page": 0, "text": "GPIO1", "bbox_px": [0, 0, 30, 10]},
                {"page": 0, "text": "GPIO2", "bbox_px": [0, 20, 30, 30]},
            ]
            (textlayer_dir / "demo_doc.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            candidates = mine_microtext_candidates.mine_textlayers(
                root,
                max_per_doc_category=0,
                doc_ids={"demo_doc"},
                categories={"pin_label"},
                exact_span_only=True,
            )

            self.assertEqual(
                [row["target_text"] for row in candidates],
                ["GPIO1", "GPIO2"],
            )

    def test_exact_span_only_excludes_tokens_mined_from_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            textlayer_dir = root / "derived" / "textlayer"
            textlayer_dir.mkdir(parents=True)
            rows = [
                {"page": 0, "text": "CN7", "bbox_px": [0, 0, 30, 10]},
                {
                    "page": 0,
                    "text": "The signal is connected to CN7.",
                    "bbox_px": [0, 20, 180, 30],
                },
            ]
            (textlayer_dir / "demo_doc.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            candidates = mine_microtext_candidates.mine_textlayers(
                root,
                max_per_doc_category=10,
                doc_ids={"demo_doc"},
                categories={"pin_label"},
                exact_span_only=True,
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["target_text"], "CN7")
            self.assertEqual(candidates[0]["raw_text"], "CN7")

    def test_pin_label_miner_rejects_lowercase_words_inside_natural_language(self) -> None:
        rows = [
            {
                "page": 0,
                "text": "mise en sacs",
                "bbox_px": [0, 0, 120, 20],
            },
            {
                "page": 0,
                "text": "GPIO12",
                "bbox_px": [0, 30, 60, 50],
            },
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="demo_doc",
            version_id="unknown",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "pin_label")
        self.assertEqual(candidates[0]["target_text"], "GPIO12")
        self.assertEqual(
            mine_microtext_candidates.QUESTION_BY_CATEGORY["pin_label"],
            "What pin or component label is shown in this marked region?",
        )

    def test_instrument_tag_miner_rejects_month_date_fragments(self) -> None:
        rows = [
            {
                "page": 0,
                "text": "06-NOV-2008 14:59",
                "bbox_px": [0, 0, 120, 20],
            },
            {
                "page": 0,
                "text": "PIT-101",
                "bbox_px": [0, 30, 60, 50],
            },
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="demo_doc",
            version_id="unknown",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "instrument_tag")
        self.assertEqual(candidates[0]["target_text"], "PIT-101")

    def test_instrument_tag_miner_accepts_full_span_pid_abbreviations(self) -> None:
        tokens = [
            "AIT",
            "AAH",
            "AE",
            "FCV",
            "PCV",
            "PS",
            "TE",
            "TS",
            "TA",
            "TAH",
            "TSH",
            "LSH",
            "RHI",
        ]
        rows = [
            {"page": 0, "text": token, "bbox_px": [0, index * 20, 50, index * 20 + 12]}
            for index, token in enumerate(tokens)
        ]
        rows.append(
            {
                "page": 0,
                "text": "The PCV controls pressure.",
                "bbox_px": [0, 300, 180, 312],
            }
        )

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="pid_doc",
            version_id="v1",
        )

        instrument_tags = [
            row["target_text"] for row in candidates if row["category"] == "instrument_tag"
        ]
        self.assertEqual(instrument_tags, tokens)

    def test_tolerance_profile_accepts_leading_dot_and_ascii_plus_minus(self) -> None:
        rows = [
            {"page": 0, "text": "±.005", "bbox_px": [0, 0, 50, 20]},
            {"page": 0, "text": "+.004/-.002", "bbox_px": [0, 30, 90, 50]},
            {"page": 0, "text": "+ .004 / - .002", "bbox_px": [0, 60, 110, 80]},
            {"page": 0, "text": "+/- 0.1", "bbox_px": [0, 90, 70, 110]},
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="tolerance_doc",
            version_id="v1",
        )

        self.assertEqual(
            [(row["category"], row["target_text"]) for row in candidates],
            [("tolerance_value", row["text"]) for row in rows],
        )

    def test_process_value_profile_accepts_instrument_operating_ranges(self) -> None:
        rows = [
            {"page": 0, "text": "0 to 500 psia", "bbox_px": [0, 0, 90, 20]},
            {"page": 0, "text": "0 to 10 psid", "bbox_px": [0, 30, 90, 50]},
            {"page": 0, "text": "0 to 3,000 psig", "bbox_px": [0, 60, 100, 80]},
            {"page": 0, "text": "0 to 302.4 g/s", "bbox_px": [0, 90, 100, 110]},
            {"page": 0, "text": "0 to 30.15 slpm", "bbox_px": [0, 120, 110, 140]},
            {"page": 0, "text": "0 to 100%", "bbox_px": [0, 150, 80, 170]},
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="instrument_table",
            version_id="v1",
        )

        self.assertEqual(
            [(row["category"], row["target_text"]) for row in candidates],
            [("process_value", row["text"]) for row in rows],
        )

    def test_process_value_profile_extracts_decimal_comma_assignments(self) -> None:
        rows = [
            {"page": 0, "text": "Q = 5 m3/h", "bbox_px": [0, 0, 120, 20]},
            {"page": 0, "text": "dp = 2,5 bar", "bbox_px": [0, 30, 140, 50]},
            {"page": 0, "text": "V = 2,4 m3", "bbox_px": [0, 60, 120, 80]},
        ]

        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="process_assignments",
            version_id="v1",
            include_all_profile_matches=True,
        )

        self.assertEqual(
            [(row["category"], row["target_text"]) for row in candidates],
            [
                ("process_value", "5 m3/h"),
                ("process_value", "2,5 bar"),
                ("process_value", "2,4 m3"),
            ],
        )
        self.assertTrue(all(row["bbox"][0] > 0 for row in candidates))

    def test_process_label_full_span_is_opt_in_and_rejects_clipped_text(self) -> None:
        rows = [
            {"page": 0, "text": "Neutralisation", "bbox_px": [0, 0, 120, 20]},
            {"page": 0, "text": "Methanol-", "bbox_px": [0, 30, 80, 50]},
            {
                "page": 0,
                "text": "This is a long explanatory sentence that is not a diagram label",
                "bbox_px": [0, 60, 300, 80],
            },
        ]
        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="process_doc",
            version_id="unknown",
            full_span_categories={"process_label"},
        )
        process_rows = [row for row in candidates if row["category"] == "process_label"]
        self.assertEqual([row["target_text"] for row in process_rows], ["Neutralisation"])

    def test_process_label_repairs_simple_utf8_latin1_mojibake(self) -> None:
        rows = [{"page": 0, "text": "Ã–le/Fette", "bbox_px": [0, 0, 120, 20]}]
        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="process_doc",
            version_id="unknown",
            repair_mojibake=True,
            full_span_categories={"process_label"},
        )
        process_rows = [row for row in candidates if row["category"] == "process_label"]
        self.assertEqual(process_rows[0]["target_text"], "Öle/Fette")
        self.assertEqual(process_rows[0]["source_raw_text"], "Ã–le/Fette")

    def test_process_label_merges_hyphen_and_connective_lines(self) -> None:
        rows = [
            {"page": 0, "text": "Methanol-", "bbox_px": [10, 10, 90, 30]},
            {"page": 0, "text": "abtrennung", "bbox_px": [10, 28, 100, 48]},
            {"page": 0, "text": "Neutralisation", "bbox_px": [200, 10, 310, 30]},
            {"page": 0, "text": "und Wäsche", "bbox_px": [200, 28, 290, 48]},
        ]
        candidates = mine_microtext_candidates.mine_rows(
            rows,
            doc_id="process_doc",
            version_id="unknown",
            full_span_categories={"process_label"},
        )
        process_rows = [row for row in candidates if row["category"] == "process_label"]
        self.assertEqual(
            [row["target_text"] for row in process_rows],
            ["Methanolabtrennung", "Neutralisation und Wäsche"],
        )
        self.assertEqual(process_rows[0]["bbox"], [10, 10, 100, 48])
        self.assertEqual(process_rows[0]["source_text_parts"], ["Methanol-", "abtrennung"])


if __name__ == "__main__":
    unittest.main()
