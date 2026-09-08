import json
import tempfile
import unittest
from pathlib import Path

from tools.candidate_evidence_holds import CURRENT_HOLDS, digest, evidence_hold_ids, is_evidence_held
from tools.apply_reviewed_gold_promotion import validate_prepared_rows


class CandidateEvidenceHoldTests(unittest.TestCase):
    def fixture(self, root):
        report = root / "derived/quality/repair/report.json"
        report.parent.mkdir(parents=True)
        rows = report.parent / "bbox_repair_proposals.jsonl"
        rows.write_text(json.dumps({"candidate_id": "c", "safe_to_merge_gold": False}) + "\n")
        report.write_text(json.dumps({"active_gold_modified": False, "safe_to_merge_gold": False,
                                     "proposals": 1, "output_hashes": {rows.name: digest(rows)}}))
        (root / CURRENT_HOLDS).write_text(json.dumps({"schema_version": 1, "reports": [{
            "report_path": report.relative_to(root).as_posix(), "report_sha256": digest(report),
        }]}))
        return report, rows

    def test_optional_empty_registry_and_verified_holds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(set(), evidence_hold_ids(root))
            self.fixture(root)
            self.assertEqual({"c"}, evidence_hold_ids(root))
            self.assertTrue(is_evidence_held({"item_id": "public", "source_candidate_id": "c"}, {"c"}))

    def test_changed_report_or_rows_fail_closed(self):
        for index in (0, 1):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                files = self.fixture(root)
                files[index].write_text("changed")
                with self.assertRaises(ValueError):
                    evidence_hold_ids(root)

    def test_prior_green_prepared_item_is_blocked_by_later_hold(self):
        row = {"item_id": "public", "source_candidate_id": "c", "doc_id": "d", "category": "dimension_value",
               "review_status": "accepted", "text_gt": "1 inch"}
        validate_prepared_rows([row], [])
        with self.assertRaisesRegex(ValueError, "unresolved_machine_evidence_hold"):
            validate_prepared_rows([row], [], machine_evidence_holds={"c"})

    def test_generic_hold_report_accepts_task_specific_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "derived/quality/semantic/report.json"
            report.parent.mkdir(parents=True)
            rows = report.parent / "candidate_evidence_holds.jsonl"
            rows.write_text(json.dumps({
                "pair_id": "v",
                "hold_reason": "description_mismatches_visible_evidence",
                "safe_to_merge_gold": False,
            }) + "\n")
            report.write_text(json.dumps({
                "active_gold_modified": False,
                "safe_to_merge_gold": False,
                "hold_rows_artifact": rows.name,
                "hold_count": 1,
                "output_hashes": {rows.name: digest(rows)},
            }))
            (root / CURRENT_HOLDS).write_text(json.dumps({
                "schema_version": 2,
                "reports": [{
                    "report_path": report.relative_to(root).as_posix(),
                    "report_sha256": digest(report),
                }],
            }))
            self.assertEqual({"v"}, evidence_hold_ids(root))

    def test_generic_hold_rows_reject_paths_and_missing_identity(self):
        for rows_name, row in (
            ("../outside.jsonl", {"candidate_id": "c", "safe_to_merge_gold": False}),
            ("candidate_evidence_holds.jsonl", {"safe_to_merge_gold": False}),
        ):
            with self.subTest(rows_name=rows_name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                report = root / "derived/quality/semantic/report.json"
                report.parent.mkdir(parents=True)
                rows = report.parent / "candidate_evidence_holds.jsonl"
                rows.write_text(json.dumps(row) + "\n")
                report.write_text(json.dumps({
                    "active_gold_modified": False,
                    "safe_to_merge_gold": False,
                    "hold_rows_artifact": rows_name,
                    "hold_count": 1,
                    "output_hashes": {rows.name: digest(rows)},
                }))
                (root / CURRENT_HOLDS).write_text(json.dumps({
                    "schema_version": 2,
                    "reports": [{
                        "report_path": report.relative_to(root).as_posix(),
                        "report_sha256": digest(report),
                    }],
                }))
                with self.assertRaises(ValueError):
                    evidence_hold_ids(root)

    def test_hash_bound_resolution_clears_only_the_named_hold_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, _ = self.fixture(root)
            first_hash = digest(first)
            second = root / "derived/quality/second/report.json"
            second.parent.mkdir(parents=True)
            second_rows = second.parent / "candidate_evidence_holds.jsonl"
            second_rows.write_text(json.dumps({
                "candidate_id": "c", "hold_reason": "second", "safe_to_merge_gold": False,
            }) + "\n")
            second.write_text(json.dumps({
                "active_gold_modified": False, "safe_to_merge_gold": False,
                "hold_rows_artifact": second_rows.name, "hold_count": 1,
                "output_hashes": {second_rows.name: digest(second_rows)},
            }))
            resolution = root / "derived/quality/resolution/report.json"
            resolution.parent.mkdir(parents=True)
            resolution_rows = resolution.parent / "resolved_candidate_evidence_holds.jsonl"
            resolution_rows.write_text(json.dumps({
                "candidate_id": "c", "resolution_status": "resolved",
                "resolved_report_sha256s": [first_hash],
            }) + "\n")
            resolution.write_text(json.dumps({
                "active_gold_modified": False, "safe_to_merge_gold": False,
                "resolution_rows_artifact": resolution_rows.name,
                "resolution_count": 1,
                "output_hashes": {resolution_rows.name: digest(resolution_rows)},
            }))
            (root / CURRENT_HOLDS).write_text(json.dumps({
                "schema_version": 2,
                "reports": [
                    {"report_path": first.relative_to(root).as_posix(), "report_sha256": first_hash},
                    {"report_path": second.relative_to(root).as_posix(), "report_sha256": digest(second)},
                ],
                "resolutions": [{
                    "report_path": resolution.relative_to(root).as_posix(),
                    "report_sha256": digest(resolution),
                }],
            }))
            self.assertEqual({"c"}, evidence_hold_ids(root))
            row = json.loads(resolution_rows.read_text())
            row["resolved_report_sha256s"].append("not-a-registered-report")
            resolution_rows.write_text(json.dumps(row) + "\n")
            resolution.write_text(json.dumps({
                "active_gold_modified": False, "safe_to_merge_gold": False,
                "resolution_rows_artifact": resolution_rows.name,
                "resolution_count": 1,
                "output_hashes": {resolution_rows.name: digest(resolution_rows)},
            }))
            config = json.loads((root / CURRENT_HOLDS).read_text())
            config["resolutions"][0]["report_sha256"] = digest(resolution)
            (root / CURRENT_HOLDS).write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                evidence_hold_ids(root)


if __name__ == "__main__":
    unittest.main()
