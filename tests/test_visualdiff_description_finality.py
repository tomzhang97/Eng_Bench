import json
import tempfile
import unittest
from pathlib import Path

from tools.visualdiff_description_finality import (
    description_release_issue, machine_known_description_issue,
    tentative_description_details, visualdiff_description_finality,
)
from tools.audit_visualdiff_description_finality import ACTIVE_PATHS, box, build_audit, probe_target, triage
from tools.visualdiff_merge import normalize_reviewed_pair


class DescriptionFinalityTest(unittest.TestCase):
    def test_known_templates_are_held(self):
        for text, kind in (
            ("Localized text may have been added: 'U3'.", "text_added"),
            ('Localized text may have been removed: "U3".', "text_removed"),
            ("Localized text may have changed from '3.3V' to 'V_BATT'.", "text_changed"),
            ("A localized graphic or schematic-symbol difference may be present.", "graphic_uncertain"),
        ):
            self.assertEqual(kind, tentative_description_details(text)["kind"])

    def test_source_text_with_may_is_not_rejected(self):
        self.assertIsNone(tentative_description_details("The note changed to 'The beam may be moved'."))
        self.assertIsNone(tentative_description_details("The text 'Localized text may have been added' was removed."))
        self.assertIsNone(tentative_description_details("Localized text was added: 'U3'."))
        self.assertEqual("D'Arcy", tentative_description_details("Localized text may have been added: 'D'Arcy'.")["target"])

    def test_changed_template_keeps_old_and_new_probe_targets_separate(self):
        details = tentative_description_details("Localized text may have changed from 'SCLK' to 'SCK'.")
        self.assertEqual("SCLK", details["target_old"])
        self.assertEqual("SCK", details["target_new"])

    def test_finality_counts_templates_not_certifications(self):
        result = visualdiff_description_finality([
            {"pair_id": "a", "split": "test", "change_desc_gt": "Localized text may have been added: 'U3'."},
            {"pair_id": "b", "split": "dev", "change_desc_gt": "R1 changed from 10K to 20K."},
        ])
        self.assertFalse(result["passes"])
        self.assertEqual(1, result["current"])
        self.assertEqual({"test": 1}, result["by_split"])

    def test_release_finality_counts_objective_description_debt(self):
        rows = [
            {"pair_id": "todo", "split": "train", "change_desc_gt": "CHANGE_DESC_GT_TODO"},
            {"pair_id": "cjk", "split": "test", "change_desc_gt": "红框内文字被删除。", "desc_source": "human"},
            {
                "pair_id": "machine",
                "split": "test",
                "change_desc_gt": "Highlighted graphic/symbol appearance changed from A to B.",
                "desc_source": "codex_assisted_visual_review",
            },
            {
                "pair_id": "human",
                "split": "test",
                "change_desc_gt": "Highlighted graphic/symbol appearance changed subtly from A to B.",
                "desc_source": "human_validated_codex_assisted",
            },
        ]
        result = visualdiff_description_finality(rows)
        self.assertEqual(1, result["current"])
        self.assertEqual(3, result["nonfinal_rows"])
        self.assertEqual(3, result["machine_known_nonrelease_rows"])
        self.assertEqual(
            {"non_english": 1, "placeholder": 1, "unvalidated_machine_visual": 1},
            result["by_reason"],
        )
        self.assertIsNone(description_release_issue(rows[-1]))

    def test_text_only_machine_debt_classifier_is_fail_closed(self):
        self.assertEqual("blank", machine_known_description_issue(""))
        self.assertEqual("placeholder", machine_known_description_issue("TODO"))
        self.assertEqual("non_english", machine_known_description_issue("工程标签被删除。"))
        self.assertEqual(
            "generic_machine_description",
            machine_known_description_issue(
                "Highlighted graphic/symbol appearance changed from A to B."
            ),
        )

    def test_direct_merge_rejects_accepted_tentative_description(self):
        result, errors = normalize_reviewed_pair({
            "pair_id": "a", "human_review_status": "accepted",
            "human_description": "Localized text may have been added: 'U3'.",
        }, {}, {}, {})
        self.assertIsNone(result)
        self.assertIn("tentative_visualdiff_description", errors)

    def test_text_probe_requires_locality_and_correct_page(self):
        spans = [
            {"text": "GND", "page": 0, "bbox_px": [500, 500, 540, 520]},
            {"text": "GND", "page": 1, "bbox_px": [10, 10, 50, 30]},
        ]
        result = probe_target(spans, "GND", 0, [10, 10, 50, 30])
        self.assertEqual(1, result["page_matches"])
        self.assertEqual(0, result["nearby_matches"])
        self.assertIsNone(result["best"])

    def test_shifted_label_and_clipped_crop_are_only_repair_leads(self):
        old = probe_target([{"text": "U3", "page": 0, "bbox_px": [100, 10, 140, 40]}], "U3", 0, [10, 15, 50, 35])
        new = probe_target([{"text": "U3", "page": 0, "bbox_px": [10, 10, 50, 40]}], "U3", 0, [10, 15, 50, 35])
        self.assertEqual(0, old["best"]["original_crop_span_coverage"])
        self.assertAlmostEqual(2 / 3, new["best"]["original_crop_span_coverage"])
        self.assertEqual("same_text_near_both_revisions_requires_alignment", triage(old, new))

    def test_multiple_local_matches_are_ambiguous(self):
        result = probe_target([
            {"text": "GND", "page": 0, "bbox_px": [10, 10, 50, 30]},
            {"text": "GND", "page": 0, "bbox_px": [100, 10, 140, 30]},
        ], "GND", 0, [10, 10, 50, 30])
        self.assertEqual("ambiguous_repeated_text_requires_correspondence", triage(result, result))

    def test_malformed_boxes_and_missing_textlayer_fail_closed(self):
        self.assertIsNone(box([0, 0, float("nan"), 1]))
        self.assertIsNone(box([0, 0, 0, 1]))
        self.assertEqual("missing_or_invalid_textlayer_evidence", triage({"status": "missing_textlayer"}, {}))

    def test_audit_preserves_gold_and_rejects_output_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ACTIVE_PATHS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            pair = {"pair_id": "a", "project_id": "family", "split": "test",
                    "change_desc_gt": "Localized text may have been added: 'U3'.",
                    "image_old": "old.png", "image_new": "new.png",
                    "bbox_old": [0, 0, 10, 10], "bbox_new": [0, 0, 10, 10],
                    "page_index_old": 0, "page_index_new": 0}
            active = root / ACTIVE_PATHS[1]
            active.write_text(json.dumps(pair) + "\n", encoding="utf-8")
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            before = active.read_bytes()
            report = build_audit(root, root / "derived/audit", [])
            self.assertEqual("OPEN", report["status"])
            self.assertEqual(1, report["tentative_rows"])
            self.assertFalse(report["safe_to_merge_gold"])
            self.assertFalse(report["active_gold_modified"])
            self.assertEqual(before, active.read_bytes())
            self.assertEqual(1, report["machine_triage_counts"]["missing_or_invalid_textlayer_evidence"])
            with self.assertRaises(ValueError):
                build_audit(root, root / "derived/audit", [])
            with self.assertRaises(ValueError):
                build_audit(root, root / "not-derived", [])


if __name__ == "__main__":
    unittest.main()
