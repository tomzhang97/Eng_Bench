from __future__ import annotations

import unittest

from tools import process_second_pass_return


def expected_rows() -> list[dict[str, str]]:
    return [
        {
            "row_number": "1",
            "review_task": "machine_qa_pending",
            "candidate_id": "cand_001",
            "doc_id": "doc_a",
            "page_index": "0",
            "source_pack": "pack_a",
            "first_review_status": "edited",
            "first_proposed_text": "P-101",
            "first_proposed_category": "equipment_tag",
            "machine_issue": "",
            "bbox": "1,2,3,4",
            "crop_path": "review/crops/r0001.png",
            "page_path": "review/pages/p001.png",
            "second_review_status": "",
            "final_text": "",
            "final_category": "",
            "second_review_notes": "",
        },
        {
            "row_number": "2",
            "review_task": "content_hold",
            "candidate_id": "cand_002",
            "doc_id": "doc_b",
            "page_index": "1",
            "source_pack": "pack_b",
            "first_review_status": "edited",
            "first_proposed_text": "12",
            "first_proposed_category": "dimension_value",
            "machine_issue": "bare_index_number",
            "bbox": "5,6,7,8",
            "crop_path": "review/crops/r0002.png",
            "page_path": "review/pages/p002.png",
            "second_review_status": "",
            "final_text": "",
            "final_category": "",
            "second_review_notes": "",
        },
        {
            "row_number": "3",
            "review_task": "crop_repair",
            "candidate_id": "cand_003",
            "doc_id": "doc_c",
            "page_index": "0",
            "source_pack": "pack_c",
            "first_review_status": "needs_full_page",
            "first_proposed_text": "A5",
            "first_proposed_category": "pin_label",
            "machine_issue": "bbox_expanded",
            "bbox": "9,10,11,12",
            "crop_path": "review/crops/r0003.png",
            "page_path": "review/pages/p003.png",
            "second_review_status": "",
            "final_text": "",
            "final_category": "",
            "second_review_notes": "",
        },
    ]


class ProcessSecondPassReturnTests(unittest.TestCase):
    def test_complete_return_is_sanitized_from_expected_immutable_fields(self) -> None:
        expected = expected_rows()
        returned = [dict(row) for row in expected]
        returned[0]["second_review_status"] = "accepted"
        returned[1].update(
            {
                "second_review_status": "rejected",
                "second_review_notes": "Table index, not a standalone engineering label.",
            }
        )
        returned[2].update(
            {
                "second_review_status": "edited",
                "final_text": "A5",
                "final_category": "pin_label",
            }
        )
        for row in returned:
            row.pop("bbox")

        sanitized, report = process_second_pass_return.validate_return_rows(expected, returned)

        self.assertTrue(report["complete"])
        self.assertEqual(report["status_counts"], {"accepted": 1, "edited": 1, "rejected": 1})
        self.assertEqual(sanitized[0]["first_proposed_text"], "P-101")
        self.assertEqual(sanitized[2]["final_text"], "A5")
        self.assertEqual(report["errors"], [])

    def test_immutable_change_is_reported_and_not_propagated(self) -> None:
        expected = expected_rows()
        returned = [dict(row) for row in expected]
        for row in returned:
            row["second_review_status"] = "accepted"
        returned[0]["first_proposed_text"] = "ALTERED"

        sanitized, report = process_second_pass_return.validate_return_rows(expected, returned)

        self.assertFalse(report["complete"])
        self.assertEqual(sanitized[0]["first_proposed_text"], "P-101")
        self.assertEqual(report["immutable_changes"], 1)
        self.assertTrue(any("immutable field changed" in error for error in report["errors"]))

    def test_incomplete_edit_and_unexplained_rejection_fail(self) -> None:
        expected = expected_rows()
        returned = [dict(row) for row in expected]
        returned[0]["second_review_status"] = "edited"
        returned[0]["final_text"] = "P-102"
        returned[1]["second_review_status"] = "rejected"
        returned[2]["second_review_status"] = "accepted"

        _, report = process_second_pass_return.validate_return_rows(expected, returned)

        self.assertFalse(report["complete"])
        self.assertEqual(report["invalid_rows"], 2)
        self.assertTrue(any("edited requires final_text and final_category" in error for error in report["errors"]))
        self.assertTrue(any("rejected requires second_review_notes" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
