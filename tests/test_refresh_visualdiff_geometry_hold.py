import json
import tempfile
import unittest
from pathlib import Path

from tools import refresh_visualdiff_geometry_hold as refresh
from tools import visualdiff_geometry_holds as holds


class RefreshVisualDiffGeometryHoldTests(unittest.TestCase):
    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def build_fixture(self, root: Path, *, overlap: bool = False) -> tuple[Path, str]:
        pair_id = "vdiff__fixture__old__to__new__held"
        correction_id = pair_id if overlap else "vdiff__fixture__old__to__new__localized"
        pair_path = root / "visualdiff/annotations/visualdiff_pairs.jsonl"
        question_path = root / "visualdiff/annotations/visualdiff_questions.jsonl"
        unified_path = root / "eng_bench.jsonl"
        self.write_jsonl(pair_path, [{"pair_id": pair_id, "bbox_old": [1, 2, 3, 4], "bbox_new": [5, 6, 7, 8]}])
        self.write_jsonl(question_path, [{"pair_id": pair_id}])
        self.write_jsonl(unified_path, [{"id": "q_held"}])
        current = {path: holds.file_hash(root / path) for path in holds.ACTIVE_PATHS}

        ledger_path = root / "derived/quality/prior/active_visualdiff_geometry_holds.jsonl"
        self.write_jsonl(ledger_path, [{
            "pair_id": pair_id,
            "bbox_old_after": [1, 2, 3, 4],
            "bbox_new": [5, 6, 7, 8],
            "safe_to_merge_gold": False,
        }])
        prior_report_path = root / "derived/quality/prior/report.json"
        self.write_json(prior_report_path, {
            "status": "HOLD_ACTIVE",
            "active_gold_modified": False,
            "project_id": "fixture",
            "reason": "fixture_hold",
            "hold_rows": 1,
            "hold_ledger": ledger_path.relative_to(root).as_posix(),
            "hold_ledger_sha256": holds.file_hash(ledger_path),
            "active_hashes": current,
            "next_action": "review",
        })
        self.write_json(root / holds.POINTER, {
            "report_path": prior_report_path.relative_to(root).as_posix(),
            "report_sha256": holds.file_hash(prior_report_path),
        })
        corrections_path = root / "derived/quality/transaction/corrections.jsonl"
        self.write_jsonl(corrections_path, [{"pair_id": correction_id}])
        transaction_path = root / "derived/quality/transaction/report.json"
        self.write_json(transaction_path, {
            "applied": True,
            "rolled_back": False,
            "before_hashes": current,
            "after_hashes": current,
            "corrections_artifact": corrections_path.relative_to(root).as_posix(),
            "corrections_artifact_sha256": holds.file_hash(corrections_path),
        })
        return transaction_path, holds.file_hash(transaction_path)

    def test_re_pins_disjoint_description_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transaction, digest = self.build_fixture(root)
            report = refresh.refresh(root, transaction, digest, root / "derived/quality/refreshed")
            self.assertEqual(1, report["geometry_rows_verified"])
            self.assertEqual(1, len(holds.current_hold_ids(root)))

    def test_rejects_transaction_that_touches_held_row(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transaction, digest = self.build_fixture(root, overlap=True)
            with self.assertRaisesRegex(ValueError, "overlaps active geometry holds"):
                refresh.refresh(root, transaction, digest, root / "derived/quality/refreshed")


if __name__ == "__main__":
    unittest.main()
