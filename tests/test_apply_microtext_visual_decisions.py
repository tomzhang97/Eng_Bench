import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "apply_microtext_visual_decisions.py"
SPEC = importlib.util.spec_from_file_location("apply_microtext_visual_decisions", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ApplyMicrotextVisualDecisionsTests(unittest.TestCase):
    def test_parse_row_spec(self):
        self.assertEqual(MODULE.parse_row_spec("1,3-5,8"), [1, 3, 4, 5, 8])

    def test_applies_holds_and_category_corrections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            rows = [
                {"candidate_id": "a", "doc_id": "d", "category": "process_label", "proposed_text": "A"},
                {"candidate_id": "b", "doc_id": "d", "category": "process_label", "proposed_text": "35 C"},
                {"candidate_id": "c", "doc_id": "d", "category": "process_label", "proposed_text": "C"},
            ]
            input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "input_sha256": digest,
                        "hold_groups": [{"rows": "1,3", "reason": "bad_crop"}],
                        "corrections": {"2": {"category": "process_value", "reason": "temperature"}},
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, audit = MODULE.build_report(input_path, decisions_path)

            self.assertEqual([row["candidate_id"] for row in kept], ["b"])
            self.assertEqual(kept[0]["category"], "process_value")
            self.assertEqual(kept[0]["question_text"], "What process value is shown in this region?")
            self.assertEqual(kept[0]["review_status"], "needs_review")
            self.assertFalse(kept[0]["safe_to_merge_gold"])
            self.assertEqual(len(held), 2)
            self.assertTrue(all(row["review_status"] == "machine_held" for row in held))
            self.assertTrue(all(row["safe_to_merge_gold"] is False for row in held))
            self.assertEqual(report["totals"]["corrected_rows"], 1)
            self.assertEqual([row["decision"] for row in audit], ["hold", "keep", "hold"])

    def test_pin_label_correction_updates_question_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "pin",
                        "doc_id": "board",
                        "category": "process_label",
                        "proposed_text": "GPIO18",
                        "question_text": "What process step or stream label is shown in this region?",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "corrections": {
                            "1": {
                                "category": "pin_label",
                                "reason": "pcb_pin_or_net_label",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, _ = MODULE.build_report(input_path, decisions_path)

            self.assertEqual(held, [])
            self.assertEqual(kept[0]["category"], "pin_label")
            self.assertEqual(
                kept[0]["question_text"],
                "What pin or component label is shown in this marked region?",
            )
            self.assertEqual(report["totals"]["corrected_rows"], 1)

    def test_component_value_correction_updates_question_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "value",
                        "doc_id": "board",
                        "category": "dimension_value",
                        "proposed_text": "100K",
                        "question_text": "What dimension value is shown in this region?",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "corrections": {
                            "1": {
                                "category": "component_value",
                                "reason": "electrical_component_value",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            kept, held, _, _ = MODULE.build_report(input_path, decisions_path)

            self.assertEqual(held, [])
            self.assertEqual(kept[0]["category"], "component_value")
            self.assertEqual(
                kept[0]["question_text"],
                "What component value is shown in this region?",
            )

    def test_applies_text_only_correction_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "room",
                        "doc_id": "drawing",
                        "category": "room_label",
                        "proposed_text": "Milk·Cooler Room",
                        "target_text": "Milk·Cooler Room",
                        "raw_text": "Milk·Cooler Room",
                        "text_context": "Milk·Cooler Room",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "corrections": {
                            "1": {
                                "proposed_text": "Milk Cooler Room",
                                "reason": "ocr_punctuation_normalization",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, audit = MODULE.build_report(input_path, decisions_path)

            self.assertEqual(held, [])
            self.assertEqual(kept[0]["proposed_text"], "Milk Cooler Room")
            self.assertEqual(kept[0]["target_text"], "Milk Cooler Room")
            self.assertEqual(kept[0]["raw_text"], "Milk Cooler Room")
            self.assertEqual(kept[0]["text_context"], "Milk Cooler Room")
            self.assertEqual(kept[0]["source_raw_text"], "Milk·Cooler Room")
            self.assertEqual(kept[0]["machine_text_corrected_from"], "Milk·Cooler Room")
            self.assertEqual(report["totals"]["text_corrected_rows"], 1)
            self.assertEqual(report["totals"]["category_corrected_rows"], 0)
            self.assertEqual(audit[0]["original_proposed_text"], "Milk·Cooler Room")
            self.assertEqual(audit[0]["final_proposed_text"], "Milk Cooler Room")

    def test_applies_validated_bbox_correction_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "rotated-dimension",
                        "doc_id": "drawing",
                        "category": "dimension_value",
                        "proposed_text": "30\"",
                        "bbox": [10, 20, 30, 40],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "corrections": {
                            "1": {
                                "bbox": [9.2, 18.8, 31.1, 61.6],
                                "reason": "rotated_text_full_label_bbox",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, audit = MODULE.build_report(input_path, decisions_path)

            self.assertEqual(held, [])
            self.assertEqual(kept[0]["bbox"], [9, 19, 31, 62])
            self.assertEqual(kept[0]["machine_bbox_corrected_from"], [10, 20, 30, 40])
            self.assertEqual(report["totals"]["bbox_corrected_rows"], 1)
            self.assertEqual(audit[0]["original_bbox"], "[10,20,30,40]")
            self.assertEqual(audit[0]["final_bbox"], "[9,19,31,62]")

    def test_rejects_invalid_bbox_correction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps({"candidate_id": "bad", "bbox": [0, 0, 10, 10]}) + "\n",
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps({"corrections": {"1": {"bbox": [10, 0, 5, 10]}}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "positive xyxy"):
                MODULE.build_report(input_path, decisions_path)

    def test_rejects_empty_correction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(json.dumps({"candidate_id": "a"}) + "\n", encoding="utf-8")
            decisions_path = root / "decisions.json"
            decisions_path.write_text(json.dumps({"corrections": {"1": {}}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "lacks category, proposed_text, or bbox"):
                MODULE.build_report(input_path, decisions_path)

    def test_applies_correction_group_row_spec(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "candidate_id": f"pin-{number}",
                            "doc_id": "board",
                            "category": "process_label",
                            "proposed_text": f"D{number}",
                        }
                    )
                    + "\n"
                    for number in range(1, 4)
                ),
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "correction_groups": [
                            {
                                "rows": "1,3",
                                "category": "pin_label",
                                "reason": "pcb_pin_or_net_label",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, _ = MODULE.build_report(input_path, decisions_path)

            self.assertEqual(held, [])
            self.assertEqual(
                [row["category"] for row in kept],
                ["pin_label", "process_label", "pin_label"],
            )
            self.assertEqual(report["totals"]["corrected_rows"], 2)

    def test_correction_group_applies_shared_text_and_category(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "candidate_id": f"tank-{number}",
                            "doc_id": "drawing",
                            "category": "unknown_microtext",
                            "proposed_text": "TANK",
                            "target_text": "TANK",
                        }
                    )
                    + "\n"
                    for number in range(1, 3)
                ),
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "correction_groups": [
                            {
                                "rows": "1-2",
                                "category": "equipment_tag",
                                "proposed_text": "SURGE TANK",
                                "reason": "full_compound_label_visible",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, audit = MODULE.build_report(input_path, decisions_path)

            self.assertEqual(held, [])
            self.assertEqual([row["category"] for row in kept], ["equipment_tag"] * 2)
            self.assertEqual([row["proposed_text"] for row in kept], ["SURGE TANK"] * 2)
            self.assertEqual([row["target_text"] for row in kept], ["SURGE TANK"] * 2)
            self.assertEqual(report["totals"]["text_corrected_rows"], 2)
            self.assertEqual(audit[0]["final_proposed_text"], "SURGE TANK")

    def test_rejects_overlapping_hold_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(json.dumps({"candidate_id": "a"}) + "\n", encoding="utf-8")
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "hold_groups": [
                            {"rows": "1", "reason": "one"},
                            {"rows": "1", "reason": "two"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "multiple hold groups"):
                MODULE.build_report(input_path, decisions_path)

    def test_rejects_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(json.dumps({"candidate_id": "a"}) + "\n", encoding="utf-8")
            decisions_path = root / "decisions.json"
            decisions_path.write_text(json.dumps({"input_sha256": "0" * 64}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                MODULE.build_report(input_path, decisions_path)

    def test_default_hold_keeps_only_explicit_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "candidate_id": f"row-{number}",
                            "doc_id": "drawing",
                            "category": "unknown_microtext",
                            "proposed_text": f"TEXT {number}",
                        }
                    )
                    + "\n"
                    for number in range(1, 5)
                ),
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "default_decision": "hold",
                        "default_hold_reason": "not_high_confidence",
                        "keep_groups": [
                            {"rows": "2,4", "reason": "readable_engineering_label"}
                        ],
                        "corrections": {
                            "2": {
                                "category": "room_label",
                                "proposed_text": "ROOM 2",
                                "reason": "visual_text_and_category_correction",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            kept, held, report, audit = MODULE.build_report(input_path, decisions_path)

            self.assertEqual([row["candidate_id"] for row in kept], ["row-2", "row-4"])
            self.assertEqual(kept[0]["category"], "room_label")
            self.assertEqual(kept[0]["proposed_text"], "ROOM 2")
            self.assertEqual([row["candidate_id"] for row in held], ["row-1", "row-3"])
            self.assertEqual(report["default_decision"], "hold")
            self.assertEqual(report["totals"]["explicitly_kept_rows"], 2)
            self.assertEqual(report["held_reasons"], {"not_high_confidence": 2})
            self.assertEqual([row["decision"] for row in audit], ["hold", "keep", "hold", "keep"])
            self.assertEqual(audit[0]["reason"], "not_high_confidence")

    def test_default_hold_rejects_correction_without_explicit_keep(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(
                json.dumps({"candidate_id": "a", "category": "unknown_microtext"}) + "\n",
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "default_decision": "hold",
                        "corrections": {"1": {"category": "room_label"}},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "must also appear in keep_groups"):
                MODULE.build_report(input_path, decisions_path)

    def test_rejects_overlapping_keep_and_hold_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(json.dumps({"candidate_id": "a"}) + "\n", encoding="utf-8")
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps(
                    {
                        "keep_groups": [{"rows": "1", "reason": "good"}],
                        "hold_groups": [{"rows": "1", "reason": "bad"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "both kept and held"):
                MODULE.build_report(input_path, decisions_path)

    def test_rejects_invalid_default_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.jsonl"
            input_path.write_text(json.dumps({"candidate_id": "a"}) + "\n", encoding="utf-8")
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps({"default_decision": "accept"}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "default_decision"):
                MODULE.build_report(input_path, decisions_path)


if __name__ == "__main__":
    unittest.main()
