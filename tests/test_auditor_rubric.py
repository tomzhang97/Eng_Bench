import json
import unittest
from pathlib import Path

from tools.auditor_rubric import category_guide


class AuditorRubricTests(unittest.TestCase):
    def test_generated_guide_has_all_category_examples(self):
        text = category_guide()
        path = Path(__file__).resolve().parents[1] / "tools/auditor_microtext_rubric.json"
        rubric = json.loads(path.read_text(encoding="utf-8"))
        for category, meaning in rubric["categories"].items():
            self.assertIn(category + ": " + meaning, text)

    def test_guide_includes_reference_net_and_equipment_names(self):
        text = category_guide()
        for example in ("R101", "3V3", "SUMP PUMP"):
            self.assertIn(example, text)

    def test_clear_missing_target_differs_from_uncertain_evidence(self):
        text = category_guide()
        self.assertIn("明确缺字选2", text)
        self.assertIn("看不清选3", text)


if __name__ == "__main__":
    unittest.main()
