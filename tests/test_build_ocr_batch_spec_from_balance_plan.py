import unittest

from tools.build_ocr_batch_spec_from_balance_plan import build_spec


class BuildOcrBatchSpecFromBalancePlanTests(unittest.TestCase):
    def test_selects_only_ocr_rows_and_maps_domain_profiles(self) -> None:
        plan = {
            "date_label": "wave-x",
            "selected_sources": [
                {
                    "doc_id": "arch",
                    "domain": "civil_architectural",
                    "conversion_mode": "ocr_or_manual_region_recovery",
                },
                {
                    "doc_id": "pid",
                    "domain": "pid",
                    "conversion_mode": "ocr_or_manual_region_recovery",
                },
                {
                    "doc_id": "text",
                    "domain": "mechanical",
                    "conversion_mode": "textlayer_regex_remine",
                },
            ],
        }
        spec = build_spec(plan, version_id="balance_wave_x")
        self.assertEqual(["arch", "pid"], [row["doc_id"] for row in spec["documents"]])
        self.assertEqual(["architectural_room_labels"], spec["documents"][0]["profiles"])
        self.assertEqual(["pid_labels"], spec["documents"][1]["profiles"])
        self.assertFalse(spec["summary"]["safe_to_merge_gold"])

    def test_rejects_plan_without_ocr_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "no selected OCR"):
            build_spec({"selected_sources": []}, version_id="v")


if __name__ == "__main__":
    unittest.main()
