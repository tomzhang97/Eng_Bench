from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import csv
from pathlib import Path

from tools.build_microtext_balance_conversion_plan import HOLD_NEXT_STEPS, build_plan


class MicrotextBalanceConversionPlanTest(unittest.TestCase):
    def test_ranks_release_safe_nonpin_sources_and_excludes_rights_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            readiness = root / "readiness.json"
            capacity = root / "capacity.json"
            payload = root / "pid_safe.pdf"
            payload.write_bytes(b"paper-ready source payload")
            payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
            pcb_payload = root / "pcb_large.pdf"
            pcb_payload.write_bytes(b"paper-ready schematic payload")
            pcb_payload_sha = hashlib.sha256(pcb_payload.read_bytes()).hexdigest()
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "pid_safe",
                        "task": "microtext",
                        "path": payload.name,
                        "source_url": "https://example.test/pid-safe",
                        "public_status": "public_domain",
                        "sha256": payload_sha,
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "pcb_large",
                        "task": "microtext",
                        "path": pcb_payload.name,
                        "source_url": "https://example.test/pcb-large",
                        "public_status": "public_domain",
                        "sha256": pcb_payload_sha,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with (root / "SOURCE_INVENTORY.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "doc_id",
                        "path",
                        "source_url",
                        "public_status",
                        "domain",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "pid_safe",
                        "path": payload.name,
                        "source_url": "https://example.test/pid-safe",
                        "public_status": "public_domain",
                        "domain": "pid",
                    }
                )
                writer.writerow(
                    {
                        "doc_id": "pcb_large",
                        "path": pcb_payload.name,
                        "source_url": "https://example.test/pcb-large",
                        "public_status": "public_domain",
                        "domain": "pcb_schematic",
                    }
                )
            readiness.write_text(
                json.dumps(
                    {
                        "local_sources": [
                            {
                                "doc_id": "pid_safe",
                                "domain": "pid",
                                "task": "microtext",
                                "public_status": "public_domain",
                                "rendered_pages": 2,
                                "textlayer_spans": 100,
                                "mineable_candidates": 20,
                                "fresh_open_review_rows": 5,
                                "staged_future_rows": 0,
                                "next_step": "human_review",
                            },
                            {
                                "doc_id": "pcb_large",
                                "domain": "pcb_schematic",
                                "task": "microtext",
                                "public_status": "public_domain",
                                "rendered_pages": 10,
                                "textlayer_spans": 10000,
                                "mineable_candidates": 5000,
                                "fresh_open_review_rows": 9000,
                                "staged_future_rows": 2000,
                                "next_step": "active_gold_expand_later",
                            },
                            {
                                "doc_id": "pid_blocked",
                                "domain": "pid",
                                "task": "microtext",
                                "public_status": "rights_uncertain",
                                "rendered_pages": 2,
                                "textlayer_spans": 100,
                                "mineable_candidates": 20,
                                "next_step": "rights_review_or_hold",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            capacity.write_text(
                json.dumps(
                    {
                        "microtext_balance_capacity": {
                            "active": {
                                "category_shortfalls": {
                                    "equipment_tag": 221,
                                    "instrument_tag": 247,
                                    "pipe_line_tag": 139,
                                    "component_value": 300,
                                    "process_label": 150,
                                    "process_value": 142,
                                }
                            },
                            "row_target_projection": {
                                "additional_canonical_non_pin_rows_needed": 8611
                            },
                            "category_floor_shortfalls_after_all_canonical_staged_rows": {
                                "instrument_tag": 73
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            frozen_machine = root / "frozen_machine.jsonl"
            frozen_machine.write_text(
                json.dumps({"candidate_id": "already_reserved"}) + "\n",
                encoding="utf-8",
            )

            report = build_plan(
                root,
                readiness,
                capacity,
                date_label="fixture",
                top_sources=2,
                min_sources_per_domain=1,
                exclude_review_files=[frozen_machine],
            )

            rows = {row["doc_id"]: row for row in report["ranked_sources"]}
            self.assertEqual({"pcb_large", "pid_safe"}, set(rows))
            self.assertIn("pipe_line_tag", rows["pid_safe"]["target_categories"])
            self.assertIn("process_label", rows["pid_safe"]["target_categories"])
            self.assertEqual("component_value", rows["pcb_large"]["target_categories"])
            self.assertEqual(
                "existing_review_reservoir", rows["pid_safe"]["conversion_mode"]
            )
            self.assertEqual(
                "textlayer_regex_remine", rows["pcb_large"]["conversion_mode"]
            )
            self.assertEqual(7000, rows["pcb_large"]["fresh_unstaged_rows"])
            self.assertEqual(8000, rows["pcb_large"]["textlayer_headroom_after_staged"])
            self.assertEqual(1, report["policy"]["minimum_sources_per_eligible_domain"])
            self.assertEqual(2, len(report["commands"]))
            pcb_command = next(command for command in report["commands"] if "pcb_large" in command)
            pid_command = next(command for command in report["commands"] if "pid_safe" in command)
            self.assertIn("--categories component_value ", pcb_command)
            self.assertIn("--exact-span-only", pcb_command)
            self.assertNotIn("process_label", pcb_command)
            self.assertIn("build_microtext_source_review_queue.py", pid_command)
            self.assertIn("--category pipe_line_tag", pid_command)
            self.assertIn("--category process_label", pid_command)
            self.assertIn("--exclude-capacity-report capacity.json", pid_command)
            self.assertIn("--exclude-review-file frozen_machine.jsonl", pid_command)
            self.assertNotIn("component_value", pid_command)
            substring_report = build_plan(
                root,
                readiness,
                capacity,
                date_label="substring",
                top_sources=2,
                min_sources_per_domain=1,
                exact_span_only=False,
            )
            substring_pcb = next(
                command for command in substring_report["commands"] if "pcb_large" in command
            )
            self.assertNotIn("--exact-span-only", substring_pcb)
            self.assertIn("--max-per-answer 25", substring_pcb)
            self.assertEqual(
                "capped_substring", substring_report["policy"]["textlayer_match_scope"]
            )
            self.assertTrue(report["policy"]["release_safe_sources_only"])
            self.assertTrue(report["policy"]["paper_ready_provenance_required"])
            self.assertTrue(
                report["policy"][
                    "existing_open_review_rows_are_capacity_filtered_before_remining"
                ]
            )
            self.assertTrue(
                report["policy"]["machine_reserved_review_rows_are_excluded"]
            )
            self.assertEqual(
                "frozen_machine.jsonl",
                report["inputs"]["excluded_review_files"][0]["path"],
            )
            self.assertEqual(1, report["source_counts"]["selected_existing_review_sources"])
            self.assertFalse(report["policy"]["active_gold_modified"])
            self.assertIn("machine_exhausted_no_candidate", HOLD_NEXT_STEPS)
            self.assertIn("machine_exhausted_after_reviewed_pass", HOLD_NEXT_STEPS)
            self.assertIn("machine_exhausted", HOLD_NEXT_STEPS)
            self.assertIn("staged_future_review_capacity", HOLD_NEXT_STEPS)
            self.assertIn("await_human_return", HOLD_NEXT_STEPS)
            self.assertEqual(
                hashlib.sha256(readiness.read_bytes()).hexdigest(),
                report["inputs"]["source_readiness_sha256"],
            )

            no_fresh_payload = json.loads(readiness.read_text(encoding="utf-8"))
            no_fresh_payload["local_sources"][0].update(
                {
                    "next_step": "human_review_partial_packeted",
                    "fresh_open_review_rows": 0,
                }
            )
            readiness.write_text(json.dumps(no_fresh_payload), encoding="utf-8")
            no_fresh_report = build_plan(
                root,
                readiness,
                capacity,
                date_label="no-fresh",
                top_sources=2,
                min_sources_per_domain=1,
            )
            self.assertNotIn(
                "pid_safe",
                {row["doc_id"] for row in no_fresh_report["ranked_sources"]},
            )
            self.assertEqual(
                1,
                no_fresh_report["source_counts"]["excluded"][
                    "next_step:human_review_partial_packeted_without_fresh_rows"
                ],
            )

            no_fresh_payload["local_sources"][0]["next_step"] = "await_human_return"
            readiness.write_text(json.dumps(no_fresh_payload), encoding="utf-8")
            awaiting_report = build_plan(
                root,
                readiness,
                capacity,
                date_label="awaiting",
                top_sources=2,
                min_sources_per_domain=1,
            )
            self.assertNotIn(
                "pid_safe",
                {row["doc_id"] for row in awaiting_report["ranked_sources"]},
            )
            self.assertEqual(
                1,
                awaiting_report["source_counts"]["excluded"][
                    "next_step:await_human_return"
                ],
            )
            self.assertTrue(
                awaiting_report["policy"][
                    "awaiting_human_return_sources_are_not_remined"
                ]
            )


if __name__ == "__main__":
    unittest.main()
