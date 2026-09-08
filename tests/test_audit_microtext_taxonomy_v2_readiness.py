from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import audit_microtext_taxonomy_v2_readiness as audit


def row(identity: str, category: str, value: str, split: str = "test") -> dict:
    return {
        "candidate_id": identity,
        "category": category,
        "proposed_text": value,
        "doc_id": "doc-a",
        "reserved_split": split,
        "safe_to_merge_gold": False,
    }


class MicrotextTaxonomyV2ReadinessTests(unittest.TestCase):
    def test_taxonomy_text_rules_keep_exact_engineering_labels(self) -> None:
        self.assertTrue(audit.taxonomy_text_valid("component_value", "10uF"))
        self.assertTrue(audit.taxonomy_text_valid("component_value", "4K7"))
        self.assertFalse(audit.taxonomy_text_valid("component_value", "10 mm"))
        self.assertTrue(audit.taxonomy_text_valid("process_label", "Steam Condensate"))
        self.assertFalse(audit.taxonomy_text_valid("process_label", "This illustrates the system"))

    def test_audit_separates_qualified_and_held_rows(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "manifest.jsonl").write_text("", encoding="utf-8")
            (root / "SOURCE_INVENTORY.csv").write_text("doc_id\n", encoding="utf-8")
            rows = [
                row("good-component", "component_value", "10uF"),
                row("good-process", "process_label", "Steam Condensate"),
                row("bad-component", "component_value", "10 mm"),
                row("no-split", "process_label", "Cooling Water", split=""),
            ]
            with patch.object(audit, "evidence_materializable", return_value=True), patch.object(
                audit, "source_audit", return_value=[]
            ):
                qualified, held, report = audit.audit_rows(root, rows)

        self.assertEqual(len(qualified), 2)
        self.assertEqual(len(held), 2)
        self.assertEqual(report["hold_reason_counts"]["taxonomy_text_rule_failed"], 1)
        self.assertEqual(report["hold_reason_counts"]["missing_or_invalid_reserved_split"], 1)
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in qualified + held))


if __name__ == "__main__":
    unittest.main()
