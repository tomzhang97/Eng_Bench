import json
import tempfile
import unittest
from pathlib import Path

from tools.build_candidate_evidence_hold_report import activate, build_report
from tools.candidate_evidence_holds import CURRENT_HOLDS, evidence_hold_ids
from tools.ingest_auditor_return_batch import parser as audit_parser


class BuildCandidateEvidenceHoldReportTests(unittest.TestCase):
    def root_fixture(self, root: Path) -> None:
        for name in audit_parser.ACTIVE_GOLD_PATHS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def test_build_and_activate_generic_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.root_fixture(root)
            source = root / "holds.jsonl"
            source.write_text(json.dumps({
                "pair_id": "v",
                "task_type": "visualdiff",
                "hold_reason": "description_mismatches_visible_evidence",
                "safe_to_merge_gold": False,
            }) + "\n")
            evidence = root / "evidence.png"
            evidence.write_bytes(b"image")
            output = root / "derived/quality/holds"
            report = build_report(root, source, output, [evidence])
            self.assertEqual(1, report["hold_count"])
            self.assertFalse(report["active_gold_modified"])
            activate(root, output / "report.json")
            self.assertEqual({"v"}, evidence_hold_ids(root))
            self.assertEqual(2, json.loads((root / CURRENT_HOLDS).read_text())["schema_version"])

    def test_invalid_or_duplicate_rows_fail_closed(self):
        cases = [
            [{"hold_reason": "missing identity", "safe_to_merge_gold": False}],
            [{"candidate_id": "c", "safe_to_merge_gold": False}],
            [
                {"candidate_id": "c", "hold_reason": "one", "safe_to_merge_gold": False},
                {"candidate_id": "c", "hold_reason": "two", "safe_to_merge_gold": False},
            ],
        ]
        for index, rows in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.root_fixture(root)
                source = root / "holds.jsonl"
                source.write_text("".join(json.dumps(row) + "\n" for row in rows))
                with self.assertRaises(ValueError):
                    build_report(root, source, root / "derived/quality/holds", [])


if __name__ == "__main__":
    unittest.main()
