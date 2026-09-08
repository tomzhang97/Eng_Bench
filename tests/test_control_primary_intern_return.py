from __future__ import annotations

import unittest
from pathlib import Path

from tools.control_primary_intern_return import (
    DEFAULT_AFFECTED_ROWS,
    DEFAULT_MIGRATION_SPLIT_PLANS,
    DEFAULT_REPLACEMENT_CANDIDATES,
    DEFAULT_REPLACEMENT_PLAN,
    next_actions,
    parse_args,
    partition_reviewed_rows,
    render_markdown,
)


def micro(identity: str, *, replacement: bool = False) -> dict:
    row = {
        "candidate_id": identity,
        "review_status": "accepted",
        "safe_to_merge_gold": False,
    }
    if replacement:
        row.update(
            provenance_replacement_candidate=True,
            replacement_for_task="microtext",
            replacement_for_split="dev",
            replacement_rights_check="release_safe_status",
            replacement_evidence_fingerprint=f"microtext:sha256:{identity}",
            replacement_evidence_fingerprint_status="pixel_crop_sha256",
        )
    return row


def visual(identity: str, *, replacement: bool = False) -> dict:
    row = {
        "pair_id": identity,
        "human_review_status": "edit",
        "safe_to_merge_gold": False,
    }
    if replacement:
        row.update(
            provenance_replacement_candidate=True,
            replacement_for_task="visualdiff",
            replacement_for_split="test",
            replacement_rights_check="release_safe_status",
            replacement_evidence_fingerprint=f"visualdiff:sha256:{identity}",
            replacement_evidence_fingerprint_status="pixel_crop_sha256",
        )
    return row


class PrimaryReturnControlTests(unittest.TestCase):
    def test_cli_defaults_to_current_rights_contract(self) -> None:
        args = parse_args(
            [
                "--workbook",
                "primary.xlsx",
                "--payload",
                "payload.json",
                "--output-dir",
                "derived/quality/control",
                "--date-label",
                "2026-08-29",
            ]
        )

        self.assertEqual(args.replacement_plan, Path(DEFAULT_REPLACEMENT_PLAN))
        self.assertEqual(args.affected_rows, Path(DEFAULT_AFFECTED_ROWS))
        self.assertEqual(
            args.replacement_candidates,
            Path(DEFAULT_REPLACEMENT_CANDIDATES),
        )
        self.assertIn("wave993-current-rights", DEFAULT_REPLACEMENT_PLAN)
        self.assertEqual(
            list(DEFAULT_MIGRATION_SPLIT_PLANS),
            [
                "derived/quality/v2_0_staged_split_plan_2026-08-21-wave335.json",
                "derived/quality/v2_0_staged_split_plan_2026-08-22-wave472-arduino-leonardo-complete.json",
            ],
        )

    def test_cli_accepts_repeatable_migration_split_plans(self) -> None:
        args = parse_args(
            [
                "--workbook",
                "primary.xlsx",
                "--payload",
                "payload.json",
                "--output-dir",
                "derived/quality/control",
                "--date-label",
                "2026-08-29",
                "--migration-split-plan",
                "plan-a.json",
                "--migration-split-plan",
                "plan-b.json",
            ]
        )

        self.assertEqual(
            [str(path) for path in args.migration_split_plan],
            ["plan-a.json", "plan-b.json"],
        )

    def test_cli_accepts_repeatable_additional_overlap_inputs(self) -> None:
        args = parse_args(
            [
                "--workbook",
                "primary.xlsx",
                "--payload",
                "payload.json",
                "--output-dir",
                "derived/quality/control",
                "--date-label",
                "2026-08-21",
                "--additional-overlap-ids",
                "auditor-decisions.jsonl",
                "--additional-overlap-ids",
                "agreement-bridge.jsonl",
            ]
        )

        self.assertEqual(
            [str(path) for path in args.additional_overlap_ids],
            ["auditor-decisions.jsonl", "agreement-bridge.jsonl"],
        )

    def test_partition_separates_atomic_replacements(self) -> None:
        partitions, issues = partition_reviewed_rows(
            [micro("m-standard"), micro("m-replacement", replacement=True)],
            [visual("v-standard"), visual("v-replacement", replacement=True)],
        )

        self.assertEqual(issues, [])
        self.assertEqual(len(partitions["standard_microtext"]), 1)
        self.assertEqual(len(partitions["standard_visualdiff"]), 1)
        self.assertEqual(len(partitions["provenance_replacements"]), 2)

    def test_partition_rejects_unsafe_and_duplicate_rows(self) -> None:
        unsafe = micro("m-1")
        unsafe["safe_to_merge_gold"] = True
        partitions, issues = partition_reviewed_rows([unsafe, micro("m-1")], [])

        self.assertEqual(len(partitions["standard_microtext"]), 1)
        self.assertEqual(
            {issue["issue"] for issue in issues},
            {"unsafe_merge_flag", "duplicate_reviewed_identity"},
        )

    def test_partition_requires_complete_replacement_metadata(self) -> None:
        row = micro("m-1", replacement=True)
        row["replacement_evidence_fingerprint"] = ""
        row["replacement_for_task"] = "visualdiff"

        _, issues = partition_reviewed_rows([row], [])

        self.assertEqual(
            {issue["issue"] for issue in issues},
            {"replacement_task_mismatch", "missing_replacement_evidence_fingerprint"},
        )

    def test_frozen_contract_normalizes_stale_replacement_marker(self) -> None:
        stale = visual("v-stale", replacement=True)
        stale.pop("replacement_rights_check")

        partitions, issues = partition_reviewed_rows(
            [],
            [stale],
            replacement_ids={"v-real"},
        )

        self.assertEqual(issues, [])
        self.assertEqual(len(partitions["standard_visualdiff"]), 1)
        normalized = partitions["standard_visualdiff"][0]
        self.assertTrue(normalized["stale_provenance_marker_normalized"])
        self.assertNotIn("provenance_replacement_candidate", normalized)
        self.assertNotIn("replacement_for_task", normalized)

    def test_frozen_contract_requires_marker_for_true_replacement(self) -> None:
        row = visual("v-real")

        partitions, issues = partition_reviewed_rows(
            [],
            [row],
            replacement_ids={"v-real"},
        )

        self.assertEqual(len(partitions["provenance_replacements"]), 1)
        self.assertIn("replacement_marker_missing", {issue["issue"] for issue in issues})

    def test_incomplete_primary_action_stops_before_promotion(self) -> None:
        processing = {
            "complete": False,
            "totals": {"rework_rows": 2000},
        }
        actions = next_actions(
            processing,
            "not_run",
            {"counts": {"outstanding_review_rows": 1437}},
        )

        self.assertEqual(len(actions), 1)
        self.assertIn("2000", actions[0])

    def test_markdown_states_direct_merge_is_false(self) -> None:
        report = {
            "primary_processing": {
                "complete": False,
                "totals": {
                    "ready_decisions": 0,
                    "expected_rows": 2000,
                    "engineering_ready": 0,
                    "engineering_expected": 537,
                },
            },
            "ordinary_promotion": {"state": "not_run_primary_incomplete"},
            "provenance_migration": {
                "ready_for_atomic_migration": False,
                "counts": {
                    "accepted_or_edited_reviewed_rows": 0,
                    "replacement_candidates": 1437,
                },
            },
            "active_gold_modified": False,
            "next_actions": ["Finish the workbook."],
        }

        markdown = render_markdown(report)

        self.assertIn("Gold v2.0 Global", markdown)
        self.assertIn("Safe to merge Gold directly: `false`", markdown)


if __name__ == "__main__":
    unittest.main()
