import json
import tempfile
import unittest
from pathlib import Path

from tools import audit_historical_machine_calibration as audit


class HistoricalMachineCalibrationTests(unittest.TestCase):
    def test_review_paths_add_processed_returns_and_exclude_canonical_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            annotations = root / "annotations"
            processed = root / "processed" / "nested"
            annotations.mkdir()
            processed.mkdir(parents=True)
            primary = annotations / "reviewed.jsonl"
            duplicate = processed / "reviewed.jsonl"
            extra = processed / "returned.jsonl"
            canonical = processed / "microtext_items.jsonl"
            for path in (primary, duplicate, extra, canonical):
                path.write_text("{}\n", encoding="utf-8")

            paths = audit.review_jsonl_paths(annotations, [processed, primary])

            self.assertEqual(
                {path.resolve() for path in paths},
                {primary.resolve(), duplicate.resolve(), extra.resolve()},
            )
            self.assertNotIn(canonical.resolve(), {path.resolve() for path in paths})

    def test_collect_human_records_hashes_inputs_and_counts_only_final_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "returned.jsonl"
            rows = [
                {"candidate_id": "one", "review_status": "accepted"},
                {"candidate_id": "two", "review_status": "needs_review"},
                {"candidate_id": "three", "review_status": "rejected"},
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )

            records, inventory, scanned = audit.collect_human_records(root, [path])

            self.assertEqual(scanned, 3)
            self.assertEqual([record[2]["candidate_id"] for record in records], ["one", "three"])
            self.assertEqual(inventory[0]["rows"], 3)
            self.assertEqual(inventory[0]["final_human_rows"], 2)
            self.assertEqual(len(inventory[0]["sha256"]), 64)

    def test_human_outcome_is_fail_closed(self):
        self.assertEqual(
            audit.human_outcome({"review_status": "accepted"}, "R12", "pin_label"),
            "correct",
        )
        self.assertEqual(
            audit.human_outcome(
                {"review_status": "accepted", "corrected_text": "R13"},
                "R12",
                "pin_label",
            ),
            "incorrect",
        )
        self.assertEqual(
            audit.human_outcome({"review_status": "edited"}, "R12", "pin_label"),
            "incorrect",
        )
        self.assertEqual(
            audit.human_outcome({"review_status": "rejected"}, "R12", "pin_label"),
            "incorrect",
        )
        self.assertEqual(
            audit.human_outcome({"review_status": "needs_review"}, "R12", "pin_label"),
            "unclear",
        )

    def test_precision_bound_requires_zero_failures(self):
        self.assertGreaterEqual(audit.precision_lower_bound(300, 300, 0.95), 0.99)
        self.assertEqual(audit.precision_lower_bound(299, 300, 0.95), 0.0)
        self.assertEqual(audit.precision_lower_bound(0, 0, 0.95), 0.0)

    def test_conflicting_duplicate_human_rows_are_excluded(self):
        base = {
            "candidate_id": "mtcand__one",
            "review_status": "accepted",
            "proposed_text": "R12",
            "category": "pin_label",
        }
        duplicate = dict(base)
        selected, conflicts = audit.deduplicate_human_rows(
            [(None, 1, base), (None, 2, duplicate)]
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(conflicts, [])

        changed = dict(base, review_status="rejected")
        selected, conflicts = audit.deduplicate_human_rows(
            [(None, 1, base), (None, 2, changed)]
        )
        self.assertEqual(selected, [])
        self.assertEqual(conflicts, ["mtcand__one"])


if __name__ == "__main__":
    unittest.main()
