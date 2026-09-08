import unittest

from tools.prefilter_microtext_region_candidates import build_prefilter


class PrefilterMicrotextRegionCandidatesTest(unittest.TestCase):
    def test_selects_allowlisted_rows_and_holds_the_rest(self) -> None:
        rows = [
            {"candidate_id": "a", "doc_id": "doc_a", "category": "unknown_microtext"},
            {"candidate_id": "b", "doc_id": "doc_b", "category": "unknown_microtext"},
        ]
        allowlist = {
            "a": {
                "candidate_id": "a",
                "proposed_text": "KITCHEN",
                "category": "room_label",
                "notes": "clear crop",
            }
        }
        selected, held, report = build_prefilter(rows, allowlist)
        self.assertEqual(1, report["selected_rows"])
        self.assertEqual("needs_review", selected[0]["review_status"])
        self.assertEqual("KITCHEN", selected[0]["proposed_text"])
        self.assertEqual("machine_held", held[0]["review_status"])

    def test_rejects_unknown_allowlist_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing from candidates"):
            build_prefilter([], {"missing": {"proposed_text": "X", "category": "room_label"}})


if __name__ == "__main__":
    unittest.main()
