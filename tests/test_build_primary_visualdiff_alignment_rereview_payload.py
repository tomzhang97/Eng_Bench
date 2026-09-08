import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_primary_visualdiff_alignment_rereview_payload import (
    ACTIVE_GOLD_FILES,
    build_payload,
    normalized_change_type,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class CorrectedVisualDiffPayloadTest(unittest.TestCase):
    def test_change_type_aliases_are_normalized(self):
        self.assertEqual(normalized_change_type("text_change_candidate"), "text_change")
        self.assertEqual(normalized_change_type("addition+text"), "addition")
        self.assertEqual(normalized_change_type("not_a_type"), "unclear")

    def test_build_payload_is_identity_bound_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in ACTIVE_GOLD_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")

            panel = root / "panels" / "pair-1.png"
            panel.parent.mkdir(parents=True)
            panel.write_bytes(b"corrected-evidence")
            panel_hash = hashlib.sha256(panel.read_bytes()).hexdigest()

            triage = root / "triage.jsonl"
            write_jsonl(
                triage,
                [
                    {
                        "pair_id": "pair-1",
                        "primary_index": 7,
                        "project_id": "family-1",
                        "reserved_split": "dev",
                        "inspection_panel": "panels/pair-1.png",
                        "inspection_panel_sha256": panel_hash,
                        "triage_lane": "rerender_aligned_evidence_then_human_rereview",
                        "requires_new_human_review": True,
                        "reviewer_decision_code": 4,
                        "reviewer_status": "needs_context",
                        "reviewer_basis": "old evidence was misaligned",
                        "corrected_old_text": ["R1"],
                        "corrected_new_text": ["R2"],
                        "corrected_text_relation": "different_text",
                        "bbox_old_recommended_from_new": [1, 2, 3, 4],
                        "bbox_new_current": [1, 2, 3, 4],
                    }
                ],
            )
            primary = root / "primary.json"
            primary.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "primary_index": 7,
                                "record_id": "pair-1",
                                "task": "visualdiff",
                                "source_group": "family-1",
                                "reserved_split": "dev",
                                "capacity_cohort": "cohort-a",
                                "change_type": "text_change_candidate",
                                "change_description": "R1 changes to R2",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload, report = build_payload(root, triage, primary, expected_rows=1)
            self.assertEqual(payload["counts"]["total"], 1)
            self.assertEqual(payload["rows"][0]["change_type"], "text_change")
            self.assertEqual(payload["rows"][0]["corrected_old_text"], "R1")
            self.assertFalse(payload["rows"][0]["safe_to_merge_gold"])
            self.assertFalse(report["active_gold_modified"])

    def test_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in ACTIVE_GOLD_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            panel = root / "panel.png"
            panel.write_bytes(b"panel")
            triage = root / "triage.jsonl"
            write_jsonl(
                triage,
                [
                    {
                        "pair_id": "pair-1",
                        "primary_index": 1,
                        "inspection_panel": "panel.png",
                        "inspection_panel_sha256": "0" * 64,
                        "triage_lane": "rerender_aligned_evidence_then_human_rereview",
                        "requires_new_human_review": True,
                    }
                ],
            )
            primary = root / "primary.json"
            primary.write_text(
                json.dumps({"rows": [{"primary_index": 1, "record_id": "pair-1"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                build_payload(root, triage, primary, expected_rows=1)


if __name__ == "__main__":
    unittest.main()
