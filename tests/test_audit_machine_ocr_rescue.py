import unittest

from tools.audit_machine_ocr_rescue import (
    consensus_result,
    exclude_previously_attempted,
    select_rescue_rows,
)


class MachineOcrRescueTest(unittest.TestCase):
    def test_selects_only_objective_train_rows_with_ocr_only_reasons(self) -> None:
        base = {
            "candidate_id": "c1",
            "task": "microtext",
            "reserved_split": "train",
            "category": "component_value",
            "machine_certification_reasons": ["independent_ocr_text_mismatch"],
        }
        semantic = dict(base, candidate_id="c2", category="instrument_tag")
        evaluation = dict(base, candidate_id="c3", reserved_split="test")
        mixed = dict(
            base,
            candidate_id="c4",
            machine_certification_reasons=[
                "independent_ocr_text_mismatch",
                "source_text_fields_do_not_exactly_agree",
            ],
        )

        selected = select_rescue_rows([base, semantic, evaluation, mixed])

        self.assertEqual(["c1"], [row["candidate_id"] for row in selected])

    def test_selects_microtext_when_task_is_inferred_from_candidate_id(self) -> None:
        row = {
            "candidate_id": "mtcand__schematic__v1__p0000__fp_abc123",
            "reserved_split": "train",
            "category": "pin_label",
            "machine_certification_reasons": ["independent_ocr_confidence_below_threshold"],
        }

        selected = select_rescue_rows([row])

        self.assertEqual([row["candidate_id"]], [item["candidate_id"] for item in selected])

    def test_consensus_requires_two_distinct_exact_high_confidence_views(self) -> None:
        attempts = [
            {"view": "rgb", "ocr_text": "100nF", "ocr_confidence": 0.991},
            {"view": "gray", "ocr_text": "100nF", "ocr_confidence": 0.984},
            {"view": "binary", "ocr_text": "100mF", "ocr_confidence": 0.999},
        ]

        result = consensus_result(
            "100nF", attempts, minimum_confidence=0.98, minimum_exact_views=2
        )

        self.assertIsNotNone(result)
        self.assertEqual("100nF", result["ocr_text"])
        self.assertEqual(0.984, result["ocr_confidence"])

    def test_consensus_fails_with_one_exact_view_or_low_confidence(self) -> None:
        attempts = [
            {"view": "rgb", "ocr_text": "P1", "ocr_confidence": 0.999},
            {"view": "gray", "ocr_text": "P1", "ocr_confidence": 0.979},
            {"view": "binary", "ocr_text": "PI", "ocr_confidence": 0.999},
        ]

        result = consensus_result(
            "P1", attempts, minimum_confidence=0.98, minimum_exact_views=2
        )

        self.assertIsNone(result)

    def test_excludes_rows_already_present_in_prior_attempt_ledgers(self) -> None:
        rows = [
            {"candidate_id": "c1"},
            {"candidate_id": "c2"},
            {"candidate_id": "c3"},
        ]
        prior_attempts = [
            {"candidate_id": "c1"},
            {"candidate_id": "c1"},
            {"candidate_id": "c3"},
        ]

        filtered, attempted_ids = exclude_previously_attempted(rows, prior_attempts)

        self.assertEqual(["c2"], [row["candidate_id"] for row in filtered])
        self.assertEqual(2, attempted_ids)


if __name__ == "__main__":
    unittest.main()
