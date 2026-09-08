import unittest

from tools.prefill_agreement_reviewer import prefill_rows


FIELDS = {
    "doc_id": "doc_a",
    "source_doc_ids": "doc_a",
    "source_url": "https://example.com/doc_a.pdf",
    "source_status": "release_safe",
    "split": "dev",
    "task": "microtext",
    "stratum": "dev|microtext|equipment_tag",
    "category": "equipment_tag",
    "question": "What tag is shown?",
    "reference_answer": "P-101",
    "reference_evidence_json": '[{"bbox":[1,2,3,4],"image_index":0}]',
}


def reference(identifier: str) -> dict[str, str]:
    return {"id": identifier, **FIELDS}


def template(identifier: str) -> dict[str, str]:
    return {
        **reference(identifier),
        "answer_correct": "",
        "corrected_answer": "",
        "bbox_correct": "",
        "corrected_evidence_json": "",
        "accept_reject": "",
        "ambiguity": "",
        "rights_concern": "",
        "notes": "",
    }


def completed(identifier: str) -> dict[str, str]:
    return {
        "id": identifier,
        "answer_correct": "yes",
        "corrected_answer": "",
        "bbox_correct": "yes",
        "corrected_evidence_json": "",
        "accept_reject": "accept",
        "ambiguity": "no",
        "rights_concern": "no",
        "notes": "",
    }


class PrefillAgreementReviewerTest(unittest.TestCase):
    def test_prefills_only_strictly_complete_rows_and_lists_remaining_work(self) -> None:
        references = [reference("done"), reference("incomplete"), reference("fresh")]
        templates = [template("done"), template("incomplete"), template("fresh")]
        incomplete = completed("incomplete")
        incomplete.update(
            {
                "answer_correct": "no",
                "corrected_answer": "",
                "notes": "answer seems wrong",
            }
        )

        output, coverage, remaining, summary = prefill_rows(
            references,
            templates,
            [completed("done"), incomplete, completed("returned_only")],
        )

        self.assertEqual(
            ["prefilled_complete", "needs_correction", "fresh_review"],
            [row["prefill_status"] for row in output],
        )
        self.assertEqual(["incomplete", "fresh"], [row["id"] for row in remaining])
        self.assertEqual(4, len(coverage))
        self.assertEqual(1, summary["counts"]["prefilled_complete_rows"])
        self.assertEqual(1, summary["counts"]["needs_correction_rows"])
        self.assertEqual(1, summary["counts"]["fresh_review_rows"])
        self.assertEqual(2, summary["counts"]["remaining_human_rows"])
        self.assertEqual(1, summary["counts"]["returned_not_in_agreement_sample"])
        self.assertTrue(summary["safe_to_use_as_single_reviewer_prefill"])
        self.assertFalse(summary["safe_to_merge_gold"])

    def test_conflicting_template_order_is_structural_error(self) -> None:
        references = [reference("a"), reference("b")]
        templates = [template("b"), template("a")]

        _, _, _, summary = prefill_rows(references, templates, [])

        self.assertFalse(summary["safe_to_use_as_single_reviewer_prefill"])
        self.assertIn("template IDs/order differ from sample reference", summary["structural_errors"])


if __name__ == "__main__":
    unittest.main()
