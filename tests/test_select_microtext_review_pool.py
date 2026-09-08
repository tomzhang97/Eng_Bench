import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "select_microtext_review_pool.py"
SPEC = importlib.util.spec_from_file_location("select_microtext_review_pool", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def row(candidate_id: str, doc_id: str, category: str, answer: str, page: int = 0) -> dict:
    return {
        "candidate_id": candidate_id,
        "doc_id": doc_id,
        "category": category,
        "proposed_text": answer,
        "page_index": page,
        "bbox": [0, 0, 10, 10],
    }


class SelectMicrotextReviewPoolTests(unittest.TestCase):
    def test_enforces_category_and_document_caps(self):
        rows = [row(f"a{i}", "doc_a", "equipment_tag", f"P-{i}") for i in range(5)]
        rows += [row(f"b{i}", "doc_b", "dimension_value", f"{i} mm") for i in range(5)]

        selected, report = MODULE.select_rows(
            rows,
            ["equipment_tag", "dimension_value"],
            target_rows=10,
            max_per_doc=3,
            max_per_doc_category=2,
            max_per_doc_answer=1,
            category_caps={"equipment_tag": 2, "dimension_value": 2},
        )

        self.assertEqual(len(selected), 4)
        self.assertEqual(report["selected_categories"], {"dimension_value": 2, "equipment_tag": 2})
        self.assertTrue(all(value <= 2 for value in report["selected_documents"].values()))

    def test_limits_repeated_answer_per_document(self):
        rows = [row(f"a{i}", "doc", "room_label", "ROOM", i) for i in range(4)]

        selected, _report = MODULE.select_rows(
            rows,
            ["room_label"],
            target_rows=4,
            max_per_doc=4,
            max_per_doc_category=4,
            max_per_doc_answer=2,
        )

        self.assertEqual(len(selected), 2)

    def test_filters_missing_and_low_ocr_confidence_when_requested(self):
        high = row("high", "doc", "pin_label", "GND")
        high["ocr_confidence"] = 0.99
        low = row("low", "doc", "pin_label", "SDA")
        low["ocr_confidence"] = 0.97
        missing = row("missing", "doc", "pin_label", "SCL")

        selected, report = MODULE.select_rows(
            [high, low, missing],
            ["pin_label"],
            target_rows=3,
            max_per_doc=3,
            max_per_doc_category=3,
            max_per_doc_answer=1,
            min_ocr_confidence=0.98,
        )

        self.assertEqual([item["candidate_id"] for item in selected], ["high"])
        self.assertEqual(report["limits"]["minimum_ocr_confidence"], 0.98)
        self.assertEqual(report["limits"]["excluded_below_ocr_confidence"], 2)

    def test_prior_cohort_answers_count_toward_repeat_cap(self):
        candidates = [
            row("new_room_1", "doc", "room_label", "ROOM", 1),
            row("new_room_2", "doc", "room_label", "ROOM", 2),
            row("new_lab", "doc", "room_label", "LAB", 3),
        ]
        baseline = [row("old_room", "doc", "room_label", " room ")]

        selected, report = MODULE.select_rows(
            candidates,
            ["room_label"],
            target_rows=3,
            max_per_doc=3,
            max_per_doc_category=3,
            max_per_doc_answer=2,
            answer_baseline_rows=baseline,
        )

        self.assertEqual(
            [item["candidate_id"] for item in selected],
            ["new_room_1", "new_lab"],
        )
        self.assertEqual(report["limits"]["answer_baseline_rows"], 1)

    def test_prior_cohort_answer_cap_is_per_document(self):
        candidates = [row("new", "doc_b", "pin_label", "GND")]
        baseline = [row("old", "doc_a", "pin_label", "GND")]

        selected, _report = MODULE.select_rows(
            candidates,
            ["pin_label"],
            target_rows=1,
            max_per_doc=1,
            max_per_doc_category=1,
            max_per_doc_answer=1,
            answer_baseline_rows=baseline,
        )

        self.assertEqual([item["candidate_id"] for item in selected], ["new"])

    def test_rows_not_selected_preserves_unselected_input_order(self):
        rows = [
            row("a", "doc", "pin_label", "A"),
            row("b", "doc", "pin_label", "B"),
            row("c", "doc", "pin_label", "C"),
        ]

        remaining = MODULE.rows_not_selected(rows, [rows[1]])

        self.assertEqual([item["candidate_id"] for item in remaining], ["a", "c"])

    def test_round_robin_spreads_category_across_documents(self):
        rows = []
        for doc_id in ("a", "b", "c"):
            rows.extend(row(f"{doc_id}{i}", doc_id, "instrument_tag", f"PI-{i}") for i in range(3))

        selected, report = MODULE.select_rows(
            rows,
            ["instrument_tag"],
            target_rows=3,
            max_per_doc=3,
            max_per_doc_category=3,
            max_per_doc_answer=1,
        )

        self.assertEqual(len(selected), 3)
        self.assertEqual(report["selected_documents"], {"a": 1, "b": 1, "c": 1})

    def test_rejects_duplicate_category_order(self):
        with self.assertRaisesRegex(ValueError, "duplicates"):
            MODULE.select_rows([], ["room_label", "room_label"], 1, 1, 1, 1)

    def test_selected_row_drops_stale_supersession_metadata(self):
        candidate = row("a", "doc", "room_label", "ROOM")
        candidate.update(
            {
                "review_status": "machine_superseded",
                "superseded_by": "old.jsonl",
                "machine_qa_status": "superseded",
                "machine_qa_notes": "Do not review.",
            }
        )

        selected, _report = MODULE.select_rows(
            [candidate],
            ["room_label"],
            target_rows=1,
            max_per_doc=1,
            max_per_doc_category=1,
            max_per_doc_answer=1,
        )

        self.assertEqual(selected[0]["review_status"], "needs_review")
        self.assertNotIn("superseded_by", selected[0])
        self.assertNotIn("machine_qa_status", selected[0])
        self.assertNotIn("machine_qa_notes", selected[0])

    def test_limits_selection_to_allowed_documents(self):
        rows = [
            row("a", "test_doc", "instrument_tag", "PI-101"),
            row("b", "train_doc", "instrument_tag", "PI-102"),
        ]

        selected, report = MODULE.select_rows(
            rows,
            ["instrument_tag"],
            target_rows=2,
            max_per_doc=2,
            max_per_doc_category=2,
            max_per_doc_answer=1,
            allowed_doc_ids={"test_doc"},
        )

        self.assertEqual([item["candidate_id"] for item in selected], ["a"])
        self.assertEqual(report["limits"]["allowed_doc_ids"], 1)

    def test_explicit_document_allowlist_intersects_split_plan_documents(self):
        self.assertEqual(
            MODULE.resolve_allowed_doc_ids(
                ["test_doc", "unassigned_doc", "test_doc"],
                {"test_doc", "other_test_doc"},
            ),
            {"test_doc"},
        )

    def test_explicit_document_allowlist_works_without_split_plan(self):
        self.assertEqual(
            MODULE.resolve_allowed_doc_ids(["doc_b", "", "doc_a"], None),
            {"doc_a", "doc_b"},
        )


if __name__ == "__main__":
    unittest.main()
