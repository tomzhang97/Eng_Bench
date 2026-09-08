import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.reconcile_visualdiff_human_decisions import reconcile
from tools.visualdiff_merge import description, normalized_change_type


class ReconcileVisualDiffHumanDecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_path = self.root / "input.jsonl"
        self.rows = [
            {
                "pair_id": "p1",
                "human_review_status": "edit",
                "human_description": "标签由 A 改为 B。",
                "engineering_review_basis": "标签由 A 改为 B。",
                "change_type": "text_change_candidate",
            },
            {
                "pair_id": "p2",
                "human_review_status": "edit",
                "human_description": "方向错误。",
                "change_type": "schematic_change_candidate",
            },
        ]
        self.input_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ledger(self) -> dict:
        digest = hashlib.sha256(self.input_path.read_bytes()).hexdigest()
        return {
            "input_sha256": digest,
            "decisions": [
                {
                    "pair_id": "p1",
                    "decision": "retain_localized",
                    "localized_english_description": "The label changes from A to B.",
                    "confirmed_change_type": ["text"],
                    "evidence_sheet": "sheet.jpg#1",
                },
                {
                    "pair_id": "p2",
                    "decision": "hold_conflict",
                    "hold_reason": "semantic_direction_conflict",
                    "evidence_sheet": "sheet.jpg#2",
                },
            ],
        }

    def test_reconciles_and_preserves_original_human_semantics(self) -> None:
        ready, held, report = reconcile(self.input_path, self.rows, self.ledger())

        self.assertTrue(report["valid"])
        self.assertEqual(len(ready), 1)
        self.assertEqual(len(held), 1)
        self.assertEqual(ready[0]["original_human_description"], "标签由 A 改为 B。")
        self.assertEqual(description(ready[0]), "The label changes from A to B.")
        self.assertEqual(normalized_change_type(ready[0]), ["text"])
        self.assertEqual(held[0]["promotion_hold_reason"], "semantic_direction_conflict")
        self.assertFalse(ready[0]["safe_to_merge_gold"])

    def test_can_bind_an_explicit_human_semantic_source_field(self) -> None:
        ledger = self.ledger()
        ledger["decisions"][0]["source_human_field"] = "engineering_review_basis"

        ready, _held, _report = reconcile(self.input_path, self.rows, ledger)

        self.assertEqual(ready[0]["original_human_description"], "标签由 A 改为 B。")
        self.assertEqual(
            ready[0]["machine_localization_source_human_field"],
            "engineering_review_basis",
        )

    def test_rejects_blank_explicit_human_semantic_source_field(self) -> None:
        ledger = self.ledger()
        ledger["decisions"][0]["source_human_field"] = "engineering_review_basis"
        rows = [dict(row) for row in self.rows]
        rows[0]["engineering_review_basis"] = ""

        with self.assertRaisesRegex(ValueError, "source_human_field is blank"):
            reconcile(self.input_path, rows, ledger)

    def test_rejects_stale_ledger_hash(self) -> None:
        ledger = self.ledger()
        ledger["input_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "input SHA-256 mismatch"):
            reconcile(self.input_path, self.rows, ledger)

    def test_normalizes_current_primary_review_aliases(self) -> None:
        self.assertEqual(normalized_change_type({"change_type": "text_change"}), ["text"])
        self.assertEqual(normalized_change_type({"change_type": "geometry_change"}), ["layout"])
        self.assertEqual(
            normalized_change_type({"change_type": "symbol_component_change"}),
            ["symbol"],
        )


if __name__ == "__main__":
    unittest.main()
