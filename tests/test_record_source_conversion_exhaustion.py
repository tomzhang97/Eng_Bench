from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.record_source_conversion_exhaustion import build_update, write_csv


class RecordSourceConversionExhaustionTest(unittest.TestCase):
    def test_upserts_only_after_zero_novelty_and_snapshots_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            novelty = root / "novelty.json"
            ledger = root / "SOURCE_CONVERSION_EXHAUSTION.csv"
            snapshot = root / "snapshot.csv"
            plan.write_text(
                json.dumps({"selected_sources": [{"doc_id": "b"}, {"doc_id": "a"}]}),
                encoding="utf-8",
            )
            novelty.write_text(json.dumps({"totals": {"net_new_rows": 0}}), encoding="utf-8")
            write_csv(
                ledger,
                [
                    {
                        "doc_id": "a",
                        "status": "old",
                        "audit_date": "old",
                        "method": "old",
                        "evidence_path": "old",
                        "notes": "old",
                    }
                ],
            )

            rows, report = build_update(
                root,
                plan_path=plan,
                novelty_report_path=novelty,
                ledger_path=ledger,
                snapshot_path=snapshot,
                status="machine_exhausted_after_reviewed_pass",
                audit_date="2026-08-22",
                method="exact_and_bounded_remine_with_reservation_aware_novelty_audit",
                notes="No physically novel rows remained.",
            )

            self.assertTrue(snapshot.is_file())
            self.assertEqual([row["doc_id"] for row in rows], ["a", "b"])
            self.assertEqual(report["ledger"]["inserted_documents"], 1)
            self.assertEqual(report["ledger"]["replaced_documents"], 1)
            self.assertFalse(report["active_gold_modified"])

    def test_refuses_nonzero_novelty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            novelty = root / "novelty.json"
            plan.write_text(json.dumps({"selected_sources": [{"doc_id": "a"}]}), encoding="utf-8")
            novelty.write_text(json.dumps({"totals": {"net_new_rows": 1}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not exhausted"):
                build_update(
                    root,
                    plan_path=plan,
                    novelty_report_path=novelty,
                    ledger_path=root / "ledger.csv",
                    snapshot_path=root / "snapshot.csv",
                    status="machine_exhausted_after_reviewed_pass",
                    audit_date="2026-08-22",
                    method="test",
                    notes="test",
                )

    def test_accepts_action_plan_and_filter_kept_rows_metric(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            novelty = root / "novelty.json"
            plan.write_text(
                json.dumps({"selected_actions": [{"doc_id": "action-doc"}]}),
                encoding="utf-8",
            )
            novelty.write_text(
                json.dumps({"totals": {"kept_rows": 0}}), encoding="utf-8"
            )

            rows, report = build_update(
                root,
                plan_path=plan,
                novelty_report_path=novelty,
                ledger_path=root / "ledger.csv",
                snapshot_path=root / "snapshot.csv",
                status="machine_exhausted_no_candidate",
                audit_date="2026-08-29",
                method="test",
                notes="test",
            )

            self.assertEqual([row["doc_id"] for row in rows], ["action-doc"])
            self.assertEqual(report["inputs"]["novelty_metric"], "totals.kept_rows")

    def test_refuses_nonzero_filter_kept_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            novelty = root / "novelty.json"
            plan.write_text(
                json.dumps({"selected_actions": [{"doc_id": "action-doc"}]}),
                encoding="utf-8",
            )
            novelty.write_text(
                json.dumps({"totals": {"kept_rows": 1}}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "not exhausted"):
                build_update(
                    root,
                    plan_path=plan,
                    novelty_report_path=novelty,
                    ledger_path=root / "ledger.csv",
                    snapshot_path=root / "snapshot.csv",
                    status="machine_exhausted_no_candidate",
                    audit_date="2026-08-29",
                    method="test",
                    notes="test",
                )

    def test_refuses_novelty_report_without_supported_metric(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "plan.json"
            novelty = root / "novelty.json"
            plan.write_text(
                json.dumps({"selected_actions": [{"doc_id": "action-doc"}]}),
                encoding="utf-8",
            )
            novelty.write_text(json.dumps({"totals": {}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must expose"):
                build_update(
                    root,
                    plan_path=plan,
                    novelty_report_path=novelty,
                    ledger_path=root / "ledger.csv",
                    snapshot_path=root / "snapshot.csv",
                    status="machine_exhausted_no_candidate",
                    audit_date="2026-08-29",
                    method="test",
                    notes="test",
                )


if __name__ == "__main__":
    unittest.main()
