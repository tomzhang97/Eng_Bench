from __future__ import annotations

import unittest

from tools.normalize_microtext_review_questions import normalize_rows


class NormalizeMicrotextReviewQuestionsTests(unittest.TestCase):
    def test_normalizes_generic_questions_without_marking_gold(self) -> None:
        rows, report = normalize_rows(
            [
                {
                    "candidate_id": "a",
                    "category": "instrument_tag",
                    "question_text": "What text is shown?",
                    "safe_to_merge_gold": False,
                },
                {
                    "candidate_id": "b",
                    "category": "process_value",
                    "question_text": "What process value is shown in this region?",
                    "safe_to_merge_gold": False,
                },
            ]
        )
        self.assertEqual(rows[0]["question_text"], "What instrument tag is shown in this region?")
        self.assertEqual(rows[0]["machine_question_normalized_from"], "What text is shown?")
        self.assertNotIn("machine_question_normalized_from", rows[1])
        self.assertEqual(report["changed_questions"], 1)
        self.assertFalse(report["active_gold_modified"])

    def test_fails_closed_on_unknown_category(self) -> None:
        with self.assertRaises(ValueError):
            normalize_rows([{"category": "unknown_microtext"}])


if __name__ == "__main__":
    unittest.main()
