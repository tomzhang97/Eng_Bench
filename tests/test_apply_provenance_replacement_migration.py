from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.apply_provenance_replacement_migration import (
    load_preview_report,
    staged_split_bytes,
)
from tools.preview_reviewed_gold_promotion import file_sha256


class ApplyProvenanceReplacementMigrationTests(unittest.TestCase):
    def make_split_files(self, root: Path) -> None:
        for task in ("microtext", "visualdiff"):
            for split in ("train", "dev", "test"):
                path = root / "splits" / f"{task}_{split}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

    def test_split_staging_retires_only_disappearing_unit_and_adds_new_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_split_files(root)
            dev = root / "splits/microtext_dev.txt"
            dev.write_bytes(b"# frozen\r\nblocked_doc\r\nreserved_doc\r\n")
            active_items = [
                {"item_id": "old", "doc_id": "blocked_doc", "split": "dev"},
                {"item_id": "keep", "doc_id": "keep_doc", "split": "train"},
            ]
            (root / "splits/microtext_train.txt").write_text("keep_doc\n", encoding="utf-8")
            combined_items = [
                {"item_id": "keep", "doc_id": "keep_doc", "split": "train"},
                {"item_id": "new", "doc_id": "new_doc", "split": "dev"},
            ]

            staged, delta = staged_split_bytes(
                root,
                active_items,
                [],
                combined_items,
                [],
            )

            self.assertEqual(
                b"# frozen\r\nreserved_doc\r\nnew_doc\r\n",
                staged["splits/microtext_dev.txt"],
            )
            self.assertEqual(["blocked_doc"], delta["retired_units"]["microtext"])
            self.assertEqual(["new_doc"], delta["added_units"]["microtext"])

    def test_report_hash_must_match_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "derived/quality/preview.json"
            report.parent.mkdir(parents=True)
            report.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "preview_report_sha256_mismatch"):
                load_preview_report(root, report, "0" * 64)

    def test_nonpassing_preview_gate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "derived/quality/preview.json"
            report.parent.mkdir(parents=True)
            payload = {
                "mode": "read_only_atomic_migration_preview",
                "ready_for_atomic_apply": True,
                "active_gold_modified": False,
                "gates": {"strict_unified_validation": False},
                "active_file_hashes_before": {},
                "active_file_hashes_after": {},
                "counts": {"affected_rows": 1, "inserted_rows": 1},
            }
            report.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "preview_report_has_nonpassing_gate"):
                load_preview_report(root, report, file_sha256(report))


if __name__ == "__main__":
    unittest.main()
