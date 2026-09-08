from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import build_rights_replacement_plan


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class RightsReplacementPlanTests(unittest.TestCase):
    def test_matches_blocked_rows_and_split_reserved_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {"type": "doc", "doc_id": "blocked_micro", "task": "microtext"},
                    {
                        "type": "doc",
                        "doc_id": "vendor_alpha_v1",
                        "task": "microtext",
                        "same_model_id": "vendor_alpha_board",
                        "version": {"pcb_version": "V1"},
                    },
                    {
                        "type": "doc",
                        "doc_id": "vendor_alpha_v2",
                        "task": "microtext",
                        "same_model_id": "vendor_alpha_board",
                        "version": {"pcb_version": "V2"},
                    },
                ],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {"item_id": "mt_blocked", "doc_id": "blocked_micro", "split": "dev"},
                    {"item_id": "mt_safe", "doc_id": "safe_micro", "split": "train"},
                ],
            )
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "vdiff_alpha",
                        "doc_id": "alpha",
                        "project_id": "vdiff__alpha__V1__to__V2",
                        "version_id_old": "unknown",
                        "version_id_new": "unknown",
                        "split": "test",
                    },
                    {
                        "pair_id": "vdiff_beta",
                        "doc_id": "beta",
                        "project_id": "vdiff__beta__V1__to__V2",
                        "version_id_old": "V1",
                        "version_id_new": "V2",
                        "split": "test",
                    },
                ],
            )
            provenance = root / "provenance.json"
            provenance.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"doc_id": "blocked_micro", "blocker": "license_evidence_missing"},
                            {"doc_id": "vendor_alpha_v1", "blocker": "license_evidence_missing"},
                            {"doc_id": "vendor_alpha_v2", "blocker": "license_evidence_missing"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            write_jsonl(
                current,
                [{"candidate_id": "micro_replacement", "task": "microtext", "doc_id": "safe_micro"}],
            )
            write_jsonl(
                future,
                [
                    {
                        "pair_id": "vdiff_replacement",
                        "task": "visualdiff",
                        "project_id": "vdiff__safe__V1__to__V2",
                    },
                    {
                        "pair_id": "unused_train",
                        "task": "visualdiff",
                        "project_id": "vdiff__train__V1__to__V2",
                    },
                ],
            )
            split_plan = root / "split.json"
            split_plan.write_text(
                json.dumps(
                    {
                        "reservations": [
                            {"task": "microtext", "unit_id": "safe_micro", "split": "dev"},
                            {"task": "visualdiff", "unit_id": "vdiff__safe__V1__to__V2", "split": "test"},
                            {"task": "visualdiff", "unit_id": "vdiff__train__V1__to__V2", "split": "train"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report, affected, selected = build_rights_replacement_plan.build_plan(
                root, provenance, current, future, split_plan
            )

            self.assertEqual(report["active_rows_requiring_replacement"], 2)
            self.assertEqual(report["review_only_replacement_rows_selected"], 2)
            self.assertEqual(report["residual_replacement_rows_needed"], 0)
            self.assertEqual(report["issues"], [])
            self.assertEqual({row["id"] for row in affected}, {"mt_blocked", "vdiff_alpha"})
            self.assertTrue(all(row["safe_to_merge_gold"] is False for row in selected))


if __name__ == "__main__":
    unittest.main()
