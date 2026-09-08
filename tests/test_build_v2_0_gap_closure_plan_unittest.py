from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import build_v2_0_gap_closure_plan


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class V20GapClosurePlanUnittest(unittest.TestCase):
    def test_summarizes_packet_index_and_source_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            gates = {
                "gates": {
                    "total_rows": {"current": 2889, "target": "25000-50000", "passes": False},
                    "gold_source_docs": {"current": 23, "target": 150, "passes": False},
                    "hidden_public_test_examples": {"current": 382, "target": 5000, "passes": False},
                    "human_agreement_audit": {
                        "current": "0/185 complete",
                        "target": "all sampled rows complete and agreement thresholds pass",
                        "passes": False,
                    },
                }
            }
            packet_index = {
                "date_label": "2026-07-04c",
                "base_date_label": "2026-07-03f",
                "totals": {
                    "packets": 45,
                    "ready_to_send": 43,
                    "verified_packets": 43,
                    "review_rows": 4955,
                    "missing_evidence_refs": 0,
                    "overlap_count": 0,
                    "mergeable_rows": 0,
                },
                "packets": [
                    {
                        "packet_id": "p01",
                        "ready_to_send": True,
                        "review_rows": 300,
                        "return_command": "python tools\\process_next_review_batch_return.py --root .",
                    }
                ],
            }
            source_readiness = {
                "totals": {
                    "date_label": "2026-07-04d",
                    "active_packet_index_label": "2026-07-04c",
                    "active_packet_row_keys": 4955,
                    "open_review_rows_by_local_source": 7730,
                    "packeted_open_review_rows_by_local_source": 5417,
                    "unpacketed_open_review_rows_by_local_source": 2313,
                    "fresh_open_review_rows_by_local_source": 0,
                    "fresh_open_review_rows_by_candidate": 0,
                    "local_by_next_step": {"await_human_return": 171, "extract_textlayer_or_ocr": 25},
                    "candidate_by_next_step": {"await_human_return": 60, "browser_validate": 22},
                }
            }
            write_json(tmp_path / "derived" / "quality" / "human_packet_index_2026-07-04c.json", packet_index)
            write_json(
                tmp_path / "derived" / "quality" / "source_conversion_readiness_2026-07-04d.json",
                source_readiness,
            )

            report = build_v2_0_gap_closure_plan.build_report(root=tmp_path, gate_status=gates)
            markdown = build_v2_0_gap_closure_plan.render_markdown(report)

            self.assertEqual(report["active_packet_index"]["date_label"], "2026-07-04c")
            self.assertEqual(report["active_packet_index"]["ready_to_send"], 43)
            self.assertEqual(report["active_packet_index"]["review_rows"], 4955)
            self.assertEqual(report["source_readiness"]["fresh_open_review_rows_by_local_source"], 0)
            self.assertEqual(report["source_readiness"]["active_packet_row_keys"], 4955)
            self.assertEqual(report["acceptance_scenarios"][0]["accepted_rows"], 4955)
            self.assertEqual(report["acceptance_scenarios"][0]["total_rows_after"], 7844)
            self.assertEqual(report["acceptance_scenarios"][0]["row_source"], "active_packet_index")
            self.assertTrue(any("Wait for active human returns" in item for item in report["next_machine_actions"]))
            self.assertTrue(any("active packet set leaves 17156 rows" in item for item in report["next_machine_actions"]))
            self.assertTrue(any("43 ready packets" in item for item in report["next_human_actions"]))
            self.assertIn("Source Readiness", markdown)
            self.assertIn("Active Packet Index", markdown)

    def test_cli_accepts_explicit_packet_index_and_source_readiness_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            gate_path = tmp_path / "gate.json"
            packet_index_path = tmp_path / "packet_index.json"
            source_readiness_path = tmp_path / "source_readiness.json"
            output_json = tmp_path / "gap.json"
            output_md = tmp_path / "gap.md"
            write_json(
                gate_path,
                {
                    "gates": {
                        "total_rows": {"current": 2889, "target": "25000-50000", "passes": False},
                        "hidden_public_test_examples": {"current": 382, "target": 5000, "passes": False},
                    }
                },
            )
            write_json(
                packet_index_path,
                {
                    "date_label": "explicit-packet-index",
                    "totals": {
                        "packets": 2,
                        "ready_to_send": 2,
                        "verified_packets": 2,
                        "review_rows": 600,
                        "missing_evidence_refs": 0,
                        "overlap_count": 0,
                    },
                    "packets": [],
                },
            )
            write_json(
                source_readiness_path,
                {
                    "totals": {
                        "date_label": "explicit-source-readiness",
                        "active_packet_index_label": "explicit-packet-index",
                        "active_packet_row_keys": 600,
                        "fresh_open_review_rows_by_local_source": 7,
                        "fresh_open_review_rows_by_candidate": 11,
                    }
                },
            )

            exit_code = build_v2_0_gap_closure_plan.main(
                [
                    "--root",
                    str(tmp_path),
                    "--gate-report",
                    str(gate_path),
                    "--packet-index-report",
                    str(packet_index_path),
                    "--source-readiness-report",
                    str(source_readiness_path),
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            markdown = output_md.read_text(encoding="utf-8")
            self.assertEqual(payload["active_packet_index"]["date_label"], "explicit-packet-index")
            self.assertEqual(payload["active_packet_index"]["review_rows"], 600)
            self.assertEqual(payload["source_readiness"]["date_label"], "explicit-source-readiness")
            self.assertEqual(payload["source_readiness"]["fresh_open_review_rows_by_local_source"], 7)
            self.assertIn("explicit-packet-index", markdown)
            self.assertIn("explicit-source-readiness", markdown)

    def test_supplemental_review_pack_index_counts_manifest_rows_as_review_runway(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            gates = {
                "gates": {
                    "total_rows": {"current": 2889, "target": "25000-50000", "passes": False},
                    "hidden_public_test_examples": {"current": 382, "target": 5000, "passes": False},
                }
            }
            packet_index = {
                "date_label": "supplemental-2026-07-05",
                "review_pack_root": "derived/review_packs",
                "totals": {
                    "packs": 3,
                    "ready_to_send": 3,
                    "manifest_rows": 111,
                    "checklist_rows": 111,
                    "missing_evidence_refs": 0,
                    "blank_rows": 111,
                },
                "packs": [
                    {
                        "packet_id": "microtext_a",
                        "ready_to_send": True,
                        "manifest_rows": 40,
                        "folder_path": "derived/review_packs/microtext_a",
                    },
                    {
                        "packet_id": "visualdiff_b",
                        "ready_to_send": True,
                        "manifest_rows": 71,
                        "folder_path": "derived/review_packs/visualdiff_b",
                    },
                ],
            }

            report = build_v2_0_gap_closure_plan.build_report(
                root=tmp_path,
                gate_status=gates,
                packet_index_report=packet_index,
            )

            self.assertEqual(report["active_packet_index"]["date_label"], "supplemental-2026-07-05")
            self.assertEqual(report["active_packet_index"]["packets"], 3)
            self.assertEqual(report["active_packet_index"]["ready_to_send"], 3)
            self.assertEqual(report["active_packet_index"]["review_rows"], 111)
            self.assertEqual(report["acceptance_scenarios"][0]["accepted_rows"], 111)
            self.assertEqual(report["acceptance_scenarios"][0]["row_source"], "active_packet_index")
            self.assertTrue(any("3 ready packets with 111 review rows" in item for item in report["next_human_actions"]))

    def test_agreement_rows_do_not_count_as_new_gold_runway(self) -> None:
        gates = {
            "gates": {
                "total_rows": {"current": 3689, "target": "25000-50000", "passes": False},
                "hidden_public_test_examples": {"current": 1230, "target": 5000, "passes": False},
            }
        }
        packet_index = {
            "date_label": "2026-07-31",
            "totals": {
                "packets": 3,
                "ready_to_send": 3,
                "verified_packets": 3,
                "review_rows": 302,
                "gold_expansion_rows": 117,
            },
            "packets": [
                {
                    "packet_id": "agreement",
                    "ready_to_send": True,
                    "review_rows": 185,
                    "gold_expansion_rows": 0,
                },
                {
                    "packet_id": "new_rows",
                    "ready_to_send": True,
                    "review_rows": 117,
                    "gold_expansion_rows": 117,
                },
            ],
        }

        report = build_v2_0_gap_closure_plan.build_report(
            root=Path("."),
            gate_status=gates,
            packet_index_report=packet_index,
        )

        self.assertEqual(report["active_packet_index"]["review_rows"], 302)
        self.assertEqual(report["active_packet_index"]["gold_expansion_rows"], 117)
        self.assertEqual(report["acceptance_scenarios"][0]["accepted_rows"], 117)
        self.assertEqual(report["acceptance_scenarios"][0]["total_rows_after"], 3806)

    def test_source_validation_summary_reads_current_validation_next_action_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
                [
                    {
                        "candidate_id": "civil_001",
                        "validation_next_action": "intake_first",
                        "next_step": "import_render_extract",
                        "release_posture": "release_candidate",
                    },
                    {
                        "candidate_id": "pid_001",
                        "validation_next_action": "browser_validate",
                        "next_step": "browser_validate",
                        "release_posture": "candidate",
                    },
                ],
            )

            summary = build_v2_0_gap_closure_plan.count_source_validation(tmp_path)

            self.assertEqual(summary["rows"], 2)
            self.assertEqual(summary["next_actions"], {"browser_validate": 1, "intake_first": 1})
            self.assertEqual(summary["recommended_next_steps"], {"browser_validate": 1, "import_render_extract": 1})


if __name__ == "__main__":
    unittest.main()
