import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import evaluation_release_guard as guard
from tools import build_filtered_evaluation_view as filtered
from tools import make_public_private_split as split


def fixture():
    rows = [
        {"id": "q1", "task": "microtext", "split": "test", "answer": "J5",
         "evidence": [{"image_index": 0, "bbox": [0, 0, 5, 5]}],
         "metadata": {"item_id": "i1", "doc_id": "d1", "text_gt": "J5"}},
        {"id": "q2", "task": "visualdiff", "split": "test", "answer": "R1 is added.",
         "metadata": {"pair_id": "v1"}},
    ]
    items = [{"item_id": "i1", "doc_id": "d1", "split": "test", "text_gt": "J5"}]
    pairs = [{"pair_id": "v1", "project_id": "family", "split": "test", "change_desc_gt": "R1 is added."}]
    source = {"documents": [{"doc_id": d, "paper_ready": True} for d in ("d1", "d2", "d3")], "unresolved_rows": []}
    manifest_pairs = {"family": {"from_doc_id": "d2", "to_doc_id": "d3"}}
    return rows, items, pairs, source, manifest_pairs


class ReleaseGuardTests(unittest.TestCase):
    def classify(self, rows=None, holds=None, source=None):
        original, items, pairs, default_source, manifest_pairs = fixture()
        return guard.classify_rows(rows or original, original, items, pairs, holds or [],
                                   source or default_source, {}, manifest_pairs)

    def test_clear_rows(self):
        self.assertTrue(all(r["eligible_for_diagnostic_view"] for r in self.classify()))

    def test_audit_holds_use_canonical_identity(self):
        rows = self.classify(holds=[{"task_type": "microtext", "active_gold_identity": "i1"}])
        self.assertEqual(["unresolved_independent_audit"], rows[0]["reason_codes"])

    def test_unready_source_blocks_visual_revision(self):
        source = fixture()[3]
        source["documents"][1]["paper_ready"] = False
        rows = self.classify(source=source)
        self.assertEqual(["d2"], rows[1]["unready_doc_ids"])
        self.assertIn("source_provenance_not_paper_ready", rows[1]["reason_codes"])

    def test_tentative_annotation_is_blocked(self):
        rows, items, pairs, source, manifest_pairs = fixture()
        pairs[0]["change_desc_gt"] = rows[1]["answer"] = "Localized text may have been added: 'R1'."
        result = guard.classify_rows(rows, rows, items, pairs, [], source, {}, manifest_pairs)
        self.assertEqual(["tentative_visualdiff_description"], result[1]["reason_codes"])

    def test_unvalidated_machine_visual_annotation_is_blocked(self):
        rows, items, pairs, source, manifest_pairs = fixture()
        description = "Highlighted graphic/symbol appearance changed from A to B."
        pairs[0].update(
            change_desc_gt=description,
            desc_source="codex_assisted_visual_review",
        )
        rows[1]["answer"] = description
        result = guard.classify_rows(
            rows, rows, items, pairs, [], source, {}, manifest_pairs
        )
        self.assertEqual(
            ["unvalidated_machine_visual_description"],
            result[1]["reason_codes"],
        )

    def test_input_cannot_hide_held_identity_or_answer(self):
        for field in ("answer", "metadata"):
            rows = fixture()[0]
            rows[0][field] = "different" if field == "answer" else {"item_id": "not-held"}
            with self.assertRaisesRegex(ValueError, "exact current"):
                self.classify(rows=rows)

    def test_annotation_and_unified_mismatch_fails(self):
        rows, items, pairs, source, manifest_pairs = fixture()
        items[0]["text_gt"] = "J6"
        with self.assertRaisesRegex(ValueError, "annotation/unified"):
            guard.classify_rows(rows, rows, items, pairs, [], source, {}, manifest_pairs)

    def test_duplicate_and_empty_id_fails(self):
        for rows in ([{"id": "a"}, {"id": "a"}], [{"id": ""}]):
            with self.assertRaises(ValueError):
                guard.unique_index(rows, "id")

    def test_missing_or_stale_registry_blocks_before_reads(self):
        with patch.object(guard, "release_constraint", return_value={"issues": ["stale"]}):
            with self.assertRaisesRegex(ValueError, "missing or stale"):
                guard.assess(Path("."), [])

    def test_rejected_export_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split.write_jsonl(root / "eng_bench.jsonl", fixture()[0])
            with patch.object(split, "assert_release_eligible", side_effect=ValueError("unresolved")):
                with self.assertRaisesRegex(ValueError, "unresolved"):
                    split.make_split_files(root)
            self.assertFalse((root / "release").exists())

    def test_cli_reports_block_not_traceback(self):
        with patch.object(split, "make_split_files", side_effect=ValueError("stale")):
            with patch("builtins.print") as printer:
                self.assertEqual(1, split.main([]))
            printer.assert_called_with("[BLOCKED] stale")

    def test_filter_retains_original_membership_and_labels_private(self):
        rows = fixture()[0]
        assessments = self.classify(holds=[{"task_type": "microtext", "active_gold_identity": "i1"}])
        result = filtered.filter_frozen(rows, [split.strip_labels(rows[0], "public_test")],
                                        [split.strip_labels(rows[1], "hidden_test")], assessments)
        outputs = result["outputs"]
        self.assertEqual([], outputs["public_test_inputs.jsonl"])
        self.assertEqual("hidden_test", outputs["hidden_test_inputs.jsonl"][0]["challenge_split"])
        self.assertNotIn("answer", outputs["hidden_test_inputs.jsonl"][0])
        self.assertEqual("R1 is added.", outputs["hidden_test_labels_private.jsonl"][0]["answer"])
        self.assertEqual("quarantined", outputs["quarantine_and_unassigned.jsonl"][0]["disposition"])

    def test_filter_fails_if_frozen_evidence_or_membership_changed(self):
        rows = fixture()[0]
        public = [split.strip_labels(rows[0], "public_test")]
        hidden = [split.strip_labels(rows[0], "hidden_test")]
        with self.assertRaisesRegex(ValueError, "duplicate frozen"):
            filtered.filter_frozen(rows, public, hidden, self.classify())
        public[0]["images"] = ["changed.png"]
        with self.assertRaisesRegex(ValueError, "frozen input"):
            filtered.filter_frozen(rows, public, [], self.classify())

    def test_missing_assessment_or_extra_frozen_label_is_error(self):
        rows = fixture()[0]
        public = [split.strip_labels(rows[0], "public_test")]
        with self.assertRaisesRegex(ValueError, "not safety-assessed"):
            filtered.filter_frozen(rows, public, [], [])
        public[0]["answer"] = rows[0]["answer"]
        with self.assertRaisesRegex(ValueError, "frozen input"):
            filtered.filter_frozen(rows, public, [], self.classify())

    def test_new_test_row_is_not_assigned_to_either_challenge(self):
        rows = fixture()[0]
        result = filtered.filter_frozen(rows, [split.strip_labels(rows[0], "public_test")], [], self.classify())
        unassigned = result["outputs"]["quarantine_and_unassigned.jsonl"]
        self.assertEqual("q2", unassigned[0]["id"])
        self.assertEqual("not_in_existing_freeze", unassigned[0]["disposition"])
        self.assertIsNone(unassigned[0]["challenge_split"])


if __name__ == "__main__":
    unittest.main()
