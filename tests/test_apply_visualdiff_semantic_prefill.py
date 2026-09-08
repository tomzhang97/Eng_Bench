from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
import hashlib
import json

from tools.apply_visualdiff_semantic_prefill import (
    apply_prefills,
    select_queue_rows,
    verify_attribution_files,
    verify_evidence_files,
    verify_source_files,
)


class ApplyVisualDiffSemanticPrefillTests(unittest.TestCase):
    def test_applies_prefill_without_claiming_human_review(self) -> None:
        queue = [
            {
                "pair_id": "pair_1",
                "description": "generic",
                "description_source": "machine_visual_candidate_missing_textlayer",
                "review_status": "needs_review",
                "human_review_status": "unassigned",
                "safe_to_merge_gold": False,
            }
        ]
        decisions = [
            {
                "pair_id": "pair_1",
                "description": "A value changed from 1 mm to 2 mm.",
                "machine_semantic_class": "engineering_value_change",
                "machine_recommendation": "confirm_change",
                "confidence": "high",
                "evidence_sheet": "sheet_001.png",
                "proposed_change_type": "text",
                "old_text": "1 mm",
                "new_text": "2 mm",
            }
        ]
        output, report = apply_prefills(
            queue, decisions, "machine_visual_candidate_missing_textlayer"
        )
        self.assertEqual("A value changed from 1 mm to 2 mm.", output[0]["description"])
        self.assertEqual("needs_review", output[0]["review_status"])
        self.assertEqual("unassigned", output[0]["human_review_status"])
        self.assertFalse(output[0]["safe_to_merge_gold"])
        self.assertEqual("text", output[0]["change_type"])
        self.assertEqual("1 mm", output[0]["old_text"])
        self.assertEqual("2 mm", output[0]["new_text"])
        self.assertTrue(output[0]["machine_semantic_prefill"]["human_confirmation_required"])
        self.assertEqual(0, report["human_reviewed_rows"])

    def test_rejects_unknown_proposed_change_type(self) -> None:
        queue = [
            {
                "pair_id": "pair_1",
                "description_source": "machine_visual_candidate_missing_textlayer",
            }
        ]
        decisions = [
            {
                "pair_id": "pair_1",
                "description": "A value changed.",
                "machine_semantic_class": "engineering_value_change",
                "machine_recommendation": "confirm_change",
                "confidence": "high",
                "evidence_sheet": "sheet_001.png",
                "proposed_change_type": "unsupported",
            }
        ]
        with self.assertRaisesRegex(ValueError, "unsupported proposed_change_type"):
            apply_prefills(queue, decisions, "machine_visual_candidate_missing_textlayer")

    def test_requires_exact_target_coverage(self) -> None:
        queue = [
            {
                "pair_id": "pair_1",
                "description_source": "machine_visual_candidate_missing_textlayer",
            }
        ]
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            apply_prefills(queue, [], "machine_visual_candidate_missing_textlayer")

    def test_selects_strict_pair_subset_in_input_order(self) -> None:
        queue = [
            {"pair_id": "pair_1"},
            {"pair_id": "pair_2"},
            {"pair_id": "pair_3"},
        ]

        selected = select_queue_rows(queue, ["pair_3", "pair_1"])

        self.assertEqual(["pair_1", "pair_3"], [row["pair_id"] for row in selected])

    def test_selected_subset_fails_on_unknown_pair(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing from queue"):
            select_queue_rows([{"pair_id": "pair_1"}], ["pair_missing"])

    def test_verifies_and_hashes_evidence_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence" / "panel.png"
            evidence.parent.mkdir()
            evidence.write_bytes(b"panel")

            hashes = verify_evidence_files(
                root,
                [{"pair_id": "pair_1", "evidence_sheet": "evidence/panel.png"}],
            )

            self.assertEqual(64, len(hashes["evidence/panel.png"]))

    def test_missing_evidence_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "evidence file missing"):
                verify_evidence_files(
                    Path(tmp),
                    [{"pair_id": "pair_1", "evidence_sheet": "missing.png"}],
                )

    def test_release_safe_attribution_is_required_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attribution = root / "source_bundle.json"
            attribution.write_text("{}", encoding="utf-8")

            hashes = verify_attribution_files(
                root,
                [
                    {
                        "pair_id": "pair_1",
                        "source_rights_check": "release_safe_status",
                        "source_attribution_path": "source_bundle.json",
                    }
                ],
            )

            self.assertEqual(64, len(hashes["source_bundle.json"]))

    def test_release_safe_attribution_missing_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "missing source_attribution_path"):
                verify_attribution_files(
                    Path(tmp),
                    [{"pair_id": "pair_1", "source_rights_check": "release_safe_status"}],
                )

    def test_source_files_are_bound_to_manifest_and_actual_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.dxf"
            source.write_bytes(b"source")
            expected_sha = hashlib.sha256(b"source").hexdigest()
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "doc_v1",
                        "path": "source.dxf",
                        "sha256": expected_sha,
                        "public_status": "gpl_3_0_open_source_candidate",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            verified = verify_source_files(
                root,
                [
                    {
                        "pair_id": "pair_1",
                        "source_doc_ids": ["doc_v1"],
                        "source_sha256_by_doc": {"doc_v1": expected_sha},
                        "source_public_status": "gpl_3_0_open_source_candidate",
                    }
                ],
            )

            self.assertEqual(expected_sha, verified["doc_v1"]["sha256"])

    def test_source_file_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.dxf").write_bytes(b"source")
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "doc_v1",
                        "path": "source.dxf",
                        "sha256": "0" * 64,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "source file SHA mismatch"):
                verify_source_files(
                    root,
                    [
                        {
                            "pair_id": "pair_1",
                            "source_doc_ids": ["doc_v1"],
                            "source_sha256_by_doc": {"doc_v1": "0" * 64},
                        }
                    ],
                )


if __name__ == "__main__":
    unittest.main()
