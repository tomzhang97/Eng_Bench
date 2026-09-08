from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "curate_human_return", ROOT / "tools" / "curate_human_return.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class CurateHumanReturnTests(unittest.TestCase):
    def test_curate_rows_promotes_allowlist_and_holds_other_rows(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            reviewed = Path(tmp) / "reviewed.jsonl"
            write_jsonl(
                reviewed,
                [
                    {
                        "candidate_id": "a",
                        "doc_id": "release_doc",
                        "review_status": "accepted",
                    },
                    {
                        "candidate_id": "b",
                        "doc_id": "rights_hold",
                        "review_status": "edited",
                    },
                    {
                        "candidate_id": "c",
                        "doc_id": "release_doc",
                        "review_status": "rejected",
                    },
                ],
            )
            policy = {
                "microtext": {
                    "allow": {"release_doc": "train"},
                    "hold_reason_by_key": {"rights_hold": "rights_not_release_ready"},
                    "hold_reason_by_row_id": {"a": "duplicate_qa"},
                }
            }

            promoted, held, report = module.curate_rows("microtext", [reviewed], policy)

            self.assertEqual([row["candidate_id"] for row in promoted], [])
            self.assertEqual([row["candidate_id"] for row in held], ["a", "b"])
            self.assertEqual(held[0]["integration_hold_reason"], "duplicate_qa")
            self.assertEqual(
                held[1]["integration_hold_reason"], "rights_not_release_ready"
            )
            self.assertEqual(report["promoted_rows"], 0)
            self.assertEqual(report["held_rows"], 2)
            self.assertEqual(report["stats"]["ignored_non_promotable_status"], 1)

    def test_curate_rows_holds_duplicate_return_ids(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            reviewed = Path(tmp) / "reviewed.jsonl"
            write_jsonl(
                reviewed,
                [
                    {
                        "pair_id": "p1",
                        "project_id": "family",
                        "review_status": "edit",
                    },
                    {
                        "pair_id": "p1",
                        "project_id": "family",
                        "review_status": "edited",
                    },
                ],
            )
            policy = {"visualdiff": {"allow": {"family": "dev"}}}

            promoted, held, report = module.curate_rows("visualdiff", [reviewed], policy)

            self.assertEqual(len(promoted), 1)
            self.assertEqual(
                promoted[0]["integration_normalized_review_status"], "edited"
            )
            self.assertEqual(len(held), 1)
            self.assertEqual(
                held[0]["integration_hold_reason"], "duplicate_return_row_id"
            )
            self.assertEqual(
                report["held_by_reason"], {"duplicate_return_row_id": 1}
            )


if __name__ == "__main__":
    unittest.main()
