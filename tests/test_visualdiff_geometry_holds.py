import json
import tempfile
import unittest
from pathlib import Path

from tools.visualdiff_geometry_holds import ACTIVE_PATHS, POINTER, current_hold_ids, file_hash


class VisualDiffGeometryHoldTest(unittest.TestCase):
    def test_missing_pointer_means_no_holds(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(set(), current_hold_ids(Path(temp)))

    def test_loads_hash_bound_current_holds_and_rejects_stale_active(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in ACTIVE_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            out = root / "derived/quality/hold"
            out.mkdir(parents=True)
            ledger = out / "holds.jsonl"
            ledger.write_text(json.dumps({"pair_id": "row"}) + "\n", encoding="utf-8")
            report = {
                "status": "HOLD_ACTIVE", "hold_rows": 1,
                "hold_ledger": ledger.relative_to(root).as_posix(),
                "hold_ledger_sha256": file_hash(ledger),
                "active_hashes": {path: file_hash(root / path) for path in ACTIVE_PATHS},
                "active_gold_modified": False,
            }
            report_path = out / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            pointer_path = root / POINTER
            pointer_path.parent.mkdir(parents=True, exist_ok=True)
            pointer_path.write_text(json.dumps({
                "report_path": report_path.relative_to(root).as_posix(),
                "report_sha256": file_hash(report_path),
            }), encoding="utf-8")
            self.assertEqual({"row"}, current_hold_ids(root))
            (root / ACTIVE_PATHS[0]).write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "active_hashes_are_stale"):
                current_hold_ids(root)


if __name__ == "__main__":
    unittest.main()
