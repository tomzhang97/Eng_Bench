import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.partition_visualdiff_family_priority import partition_family_priority


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


class PartitionVisualDiffFamilyPriorityTest(unittest.TestCase):
    def test_partitions_all_rows_and_defers_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "queue.jsonl"
            rows = [
                {"pair_id": "vdiff__alpha__v1__to__v2__001", "visualdiff_family_id": "vdiff__alpha__v1__to__v2"},
                {"pair_id": "vdiff__alpha__v1__to__v2__002", "visualdiff_family_id": "vdiff__alpha__v1__to__v2"},
                {"pair_id": "vdiff__beta__v1__to__v2__001", "visualdiff_family_id": "vdiff__beta__v1__to__v2"},
                {"pair_id": "vdiff__beta__v1__to__v2__002", "visualdiff_family_id": "vdiff__beta__v1__to__v2"},
            ]
            write_jsonl(input_path, rows)
            evidence = root / "sheet.jpg"
            evidence.write_bytes(b"evidence")
            ledger_path = root / "ledger.json"
            ledger = {
                "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                "target_primary_families": 1,
                "require_machine_proposals": True,
                "active_gold_families_before": 12,
                "target_family_gate": 30,
                "selections": [
                    {
                        "family_id": "vdiff__alpha__v1__to__v2",
                        "primary_pair_id": "vdiff__alpha__v1__to__v2__002",
                        "backup_pair_ids": ["vdiff__alpha__v1__to__v2__001"],
                        "evidence_path": "sheet.jpg",
                        "selection_basis": "The second row has the clearer localized delta.",
                        "proposed_change_type": "value",
                        "proposed_description": "The highlighted value changed from 10 to 12.",
                    }
                ],
                "excluded_families": [
                    {
                        "family_id": "vdiff__beta__v1__to__v2",
                        "reason": "Reserve family outside the current shortest path.",
                    }
                ],
            }
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

            primary, backup, excluded, report = partition_family_priority(
                root, input_path, ledger_path, date_label="test"
            )

            self.assertTrue(report["valid"], report["issues"])
            self.assertEqual([row["pair_id"] for row in primary], ["vdiff__alpha__v1__to__v2__002"])
            self.assertEqual(backup[0]["priority_activation_status"], "review_only_if_primary_rejected")
            self.assertEqual(primary[0]["change_type"], "value")
            self.assertEqual(primary[0]["description_source"], "machine_visual_qa_ledger_v1")
            self.assertEqual(report["primary_rows_with_machine_proposals"], 1)
            self.assertEqual(len(excluded), 2)
            self.assertEqual(report["immediate_review_actions_avoided_vs_two_per_family"], 1)
            self.assertFalse(report["active_gold_modified"])

    def test_rejects_input_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "queue.jsonl"
            write_jsonl(
                input_path,
                [{"pair_id": "vdiff__alpha__v1__to__v2__001", "visualdiff_family_id": "vdiff__alpha__v1__to__v2"}],
            )
            evidence = root / "sheet.jpg"
            evidence.write_bytes(b"evidence")
            ledger_path = root / "ledger.json"
            ledger_path.write_text(
                json.dumps(
                    {
                        "input_sha256": "0" * 64,
                        "target_primary_families": 1,
                        "selections": [
                            {
                                "family_id": "vdiff__alpha__v1__to__v2",
                                "primary_pair_id": "vdiff__alpha__v1__to__v2__001",
                                "backup_pair_ids": [],
                                "evidence_path": "sheet.jpg",
                                "selection_basis": "Clear delta.",
                            }
                        ],
                        "excluded_families": [],
                    }
                ),
                encoding="utf-8",
            )

            _primary, _backup, _excluded, report = partition_family_priority(
                root, input_path, ledger_path, date_label="test"
            )

            self.assertFalse(report["valid"])
            self.assertTrue(any("input hash mismatch" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
