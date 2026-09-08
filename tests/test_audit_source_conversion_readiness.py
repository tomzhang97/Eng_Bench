from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import audit_source_conversion_readiness


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class CandidatePrefixLineageTests(unittest.TestCase):
    def test_hold_disposition_is_not_counted_as_open_review_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            annotation_dir = tmp_path / "microtext" / "annotations"
            annotation_dir.mkdir(parents=True)
            rows = (
                {
                    "candidate_id": "selected",
                    "doc_id": "demo",
                    "review_status": "needs_review",
                },
                {
                    "candidate_id": "held",
                    "doc_id": "demo",
                    "review_status": "needs_review",
                    "reservoir_disposition": "held",
                    "reservoir_hold_reason": "target_cap",
                },
            )
            (annotation_dir / "microtext_review_demo.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            by_doc, _by_candidate = audit_source_conversion_readiness.collect_review_stats(
                tmp_path,
            )

            self.assertEqual(by_doc["demo"]["review_rows"], 2)
            self.assertEqual(by_doc["demo"]["open_rows"], 1)
            self.assertEqual(by_doc["demo"]["fresh_open_rows"], 1)
            self.assertEqual(by_doc["demo"]["nonactionable_hold_rows"], 1)

    def test_duplicate_identity_with_one_complete_representation_does_not_request_evidence_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            annotation_dir = tmp_path / "microtext" / "annotations"
            annotation_dir.mkdir(parents=True)
            evidence_path = tmp_path / "derived" / "review_crops" / "complete.png"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_bytes(b"complete")
            rows = (
                {
                    "candidate_id": "duplicate-candidate",
                    "doc_id": "demo",
                    "review_status": "needs_review",
                    "crop_path": "derived/review_crops/missing.png",
                },
                {
                    "candidate_id": "duplicate-candidate",
                    "doc_id": "demo",
                    "review_status": "needs_review",
                    "crop_path": "derived/review_crops/complete.png",
                },
            )
            (annotation_dir / "microtext_review_demo.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            by_doc, _by_candidate = audit_source_conversion_readiness.collect_review_stats(tmp_path)
            review = by_doc["demo"]

            self.assertEqual(review["missing_evidence_rows"], 1)
            self.assertEqual(review["unique_open_rows"], 1)
            self.assertEqual(review["unique_fresh_missing_evidence_rows"], 0)
            self.assertEqual(
                audit_source_conversion_readiness.local_next_step(
                    {
                        "doc_id": "demo",
                        "task": "microtext",
                        "public_status": "public_domain_candidate",
                    },
                    review,
                    Counter(),
                    pages=1,
                    spans=1,
                    mineable_candidates=1,
                ),
                "human_review",
            )

    def test_packeted_or_resolved_missing_evidence_does_not_request_fresh_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            annotation_dir = tmp_path / "microtext" / "annotations"
            annotation_dir.mkdir(parents=True)
            rows = (
                {
                    "candidate_id": "packeted-candidate",
                    "doc_id": "demo",
                    "review_status": "needs_review",
                    "crop_path": "derived/review_crops/packeted-missing.png",
                },
                {
                    "candidate_id": "resolved-candidate",
                    "doc_id": "demo",
                    "review_status": "needs_review",
                    "crop_path": "derived/review_crops/resolved-missing.png",
                },
            )
            (annotation_dir / "microtext_review_demo.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            by_doc, _by_candidate = audit_source_conversion_readiness.collect_review_stats(
                tmp_path,
                packet_row_keys={"packeted-candidate"},
                resolved_row_keys={"resolved-candidate"},
            )
            review = by_doc["demo"]

            self.assertEqual(review["missing_evidence_rows"], 2)
            self.assertEqual(review["unique_fresh_missing_evidence_rows"], 0)
            self.assertEqual(
                audit_source_conversion_readiness.local_next_step(
                    {
                        "doc_id": "demo",
                        "task": "microtext",
                        "public_status": "public_domain_candidate",
                    },
                    review,
                    Counter(),
                    pages=1,
                    spans=1,
                    mineable_candidates=1,
                ),
                "await_human_return",
            )

    def test_explicit_exhaustion_suppresses_repeated_extraction_work(self) -> None:
        row = {
            "doc_id": "audited_sheet",
            "domain": "civil_architectural",
            "task": "microtext",
            "public_status": "public_domain_us_federal_candidate",
        }

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            Counter(),
            Counter(),
            pages=1,
            spans=0,
            mineable_candidates=0,
            exhaustion_status="machine_exhausted_no_candidate",
        )

        self.assertEqual(next_step, "machine_exhausted_no_candidate")

    def test_explicit_exhaustion_overrides_stale_open_review_rows(self) -> None:
        row = {
            "doc_id": "audited_sheet",
            "domain": "mechanical_cad",
            "task": "microtext",
            "public_status": "public_domain_candidate",
        }
        review = Counter(
            {
                "unique_open_rows": 120,
                "unique_fresh_open_rows": 120,
                "unique_fresh_missing_evidence_rows": 0,
            }
        )

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            review,
            Counter(),
            pages=40,
            spans=1000,
            mineable_candidates=200,
            exhaustion_status="machine_exhausted_no_candidate",
        )
        priority = audit_source_conversion_readiness.local_priority(
            row,
            review,
            Counter(),
            pages=40,
            spans=1000,
            mineable_candidates=200,
            exhaustion_status="machine_exhausted_no_candidate",
        )

        self.assertEqual(next_step, "machine_exhausted_no_candidate")
        self.assertEqual(priority, -100)

    def test_imported_candidate_with_mixed_exhausted_sources_stays_terminal(self) -> None:
        candidate = {
            "candidate_id": "pid_mixed",
            "rights_tier": "public_domain_us_federal_candidate",
        }
        validation = {"release_posture": "release_candidate"}

        next_step = audit_source_conversion_readiness.candidate_next_step(
            candidate,
            validation,
            imported=True,
            review=Counter(),
            linked_exhaustion_status="machine_exhausted",
        )
        priority = audit_source_conversion_readiness.candidate_priority(
            candidate,
            validation,
            Counter(),
            imported=True,
            linked_exhaustion_status="machine_exhausted",
        )

        self.assertEqual(next_step, "machine_exhausted")
        self.assertEqual(priority, -100)

    def test_reviewed_pass_exhaustion_suppresses_repeated_mining(self) -> None:
        row = {
            "doc_id": "productive_but_exhausted_sheet",
            "domain": "civil_architectural",
            "task": "microtext",
            "public_status": "public_domain_us_federal_candidate",
        }

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            Counter(),
            Counter(),
            pages=2,
            spans=900,
            mineable_candidates=90,
            exhaustion_status="machine_exhausted_after_reviewed_pass",
        )
        priority = audit_source_conversion_readiness.local_priority(
            row,
            Counter(),
            Counter(),
            pages=2,
            spans=900,
            mineable_candidates=90,
            exhaustion_status="machine_exhausted_after_reviewed_pass",
        )

        self.assertEqual(next_step, "machine_exhausted_after_reviewed_pass")
        self.assertEqual(priority, -100)

    def test_staged_future_rows_are_not_routed_back_to_mining(self) -> None:
        row = {
            "doc_id": "already_staged_sheet",
            "domain": "civil_architectural",
            "task": "microtext",
            "public_status": "public_domain_us_federal_candidate",
        }

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            Counter(),
            Counter(),
            pages=30,
            spans=2300,
            mineable_candidates=760,
            staged_future_rows=300,
        )
        priority = audit_source_conversion_readiness.local_priority(
            row,
            Counter(),
            Counter(),
            pages=30,
            spans=2300,
            mineable_candidates=760,
            staged_future_rows=300,
        )

        self.assertEqual(next_step, "staged_future_review_capacity")
        self.assertEqual(priority, -50)

    def test_new_staged_rows_supersede_older_local_exhaustion(self) -> None:
        row = {
            "doc_id": "recovered_sheet",
            "domain": "pid",
            "task": "microtext",
            "public_status": "public_domain_us_federal_candidate",
        }

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            Counter(),
            Counter(),
            pages=1,
            spans=20,
            mineable_candidates=0,
            exhaustion_status="machine_exhausted_no_candidate",
            staged_future_rows=12,
        )
        priority = audit_source_conversion_readiness.local_priority(
            row,
            Counter(),
            Counter(),
            pages=1,
            spans=20,
            mineable_candidates=0,
            exhaustion_status="machine_exhausted_no_candidate",
            staged_future_rows=12,
        )

        self.assertEqual(next_step, "staged_future_review_capacity")
        self.assertEqual(priority, -50)

    def test_terminal_review_history_is_not_routed_back_to_mining(self) -> None:
        row = {
            "doc_id": "all_held_sheet",
            "domain": "civil_architectural",
            "task": "microtext",
            "public_status": "public_domain_us_federal_candidate",
        }
        review = Counter({"review_rows": 12, "status:machine_rejected": 12})

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            review,
            Counter(),
            pages=1,
            spans=200,
            mineable_candidates=20,
        )
        priority = audit_source_conversion_readiness.local_priority(
            row,
            review,
            Counter(),
            pages=1,
            spans=200,
            mineable_candidates=20,
        )

        self.assertEqual(next_step, "machine_reviewed_no_actionable_candidate")
        self.assertEqual(priority, -75)

    def test_latest_canonical_future_capacity_ignores_holds_and_preapply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / "derived" / "quality"
            quality.mkdir(parents=True)
            canonical = quality / "v2_0_canonical_future_capacity_2026-08-12-wave1.jsonl"
            canonical.write_text(
                json.dumps({"doc_id": "demo", "candidate_id": "mt-demo"}) + "\n",
                encoding="utf-8",
            )
            (quality / "v2_0_canonical_future_capacity_holds_2026-08-12-wave2.jsonl").write_text(
                json.dumps({"doc_id": "held"}) + "\n", encoding="utf-8"
            )
            (quality / "v2_0_canonical_future_capacity_2026-08-12-wave3-preapply.jsonl").write_text(
                json.dumps({"doc_id": "preapply"}) + "\n", encoding="utf-8"
            )

            stats, selected_paths = audit_source_conversion_readiness.collect_staged_future_stats(root)

            self.assertEqual(selected_paths, [canonical])
            self.assertEqual(stats["demo"]["staged_future_rows"], 1)
            self.assertNotIn("held", stats)
            self.assertNotIn("preapply", stats)

    def test_validated_staged_capacity_report_aggregates_future_cohorts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / "derived" / "quality"
            queues = root / "derived" / "review_queues"
            quality.mkdir(parents=True)
            queues.mkdir(parents=True)
            cohort = queues / "fresh.jsonl"
            cohort.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "task": "microtext",
                            "candidate_id": f"mt-{index}",
                            "doc_id": "recovered_sheet",
                        }
                    )
                    for index in range(3)
                )
                + "\n",
                encoding="utf-8",
            )
            report = quality / "v2_0_staged_capacity_2026-08-30-wave1.json"
            write_json(
                report,
                {
                    "capacity_input_clean": True,
                    "cohorts": [
                        {
                            "phase": "future",
                            "path": "derived/review_queues/fresh.jsonl",
                        }
                    ],
                },
            )

            stats, selected_paths = (
                audit_source_conversion_readiness.collect_staged_future_stats(root)
            )

            self.assertEqual(selected_paths, [report])
            self.assertEqual(stats["recovered_sheet"]["staged_future_rows"], 3)

    def test_staged_visualdiff_capacity_is_attributed_to_both_revision_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / "derived" / "quality"
            quality.mkdir(parents=True)
            canonical = quality / "v2_0_canonical_future_capacity_2026-08-12-wave1.jsonl"
            canonical.write_text(
                json.dumps(
                    {
                        "task": "visualdiff",
                        "pair_id": "vdiff__demo_old__to__demo_new",
                        "metadata": {
                            "old_doc_id": "demo_old",
                            "new_doc_id": "demo_new",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            stats, _selected_paths = audit_source_conversion_readiness.collect_staged_future_stats(root)

            self.assertEqual(stats["demo_old"]["staged_future_rows"], 1)
            self.assertEqual(stats["demo_new"]["staged_future_rows"], 1)

    def test_imported_candidate_with_linked_staged_rows_is_not_remined(self) -> None:
        next_step = audit_source_conversion_readiness.candidate_next_step(
            {
                "candidate_id": "pcb_999",
                "rights_tier": "cc_by_sa_4_0_open_hardware_candidate",
            },
            {"release_posture": "release_candidate"},
            imported=True,
            review=Counter(),
            linked_staged_future_rows=42,
        )
        priority = audit_source_conversion_readiness.candidate_priority(
            {
                "candidate_id": "pcb_999",
                "rights_tier": "cc_by_sa_4_0_open_hardware_candidate",
            },
            {"release_posture": "release_candidate"},
            Counter(),
            imported=True,
            linked_staged_future_rows=42,
        )

        self.assertEqual(next_step, "staged_future_review_capacity")
        self.assertEqual(priority, -50)

    def test_new_linked_staged_rows_supersede_older_candidate_exhaustion(self) -> None:
        candidate = {
            "candidate_id": "pid_recovered",
            "rights_tier": "public_domain_us_federal_candidate",
        }
        validation = {"release_posture": "release_candidate"}

        next_step = audit_source_conversion_readiness.candidate_next_step(
            candidate,
            validation,
            imported=True,
            review=Counter(),
            linked_staged_future_rows=12,
            linked_exhaustion_status="machine_exhausted_no_candidate",
        )
        priority = audit_source_conversion_readiness.candidate_priority(
            candidate,
            validation,
            Counter(),
            imported=True,
            linked_staged_future_rows=12,
            linked_exhaustion_status="machine_exhausted_no_candidate",
        )

        self.assertEqual(next_step, "staged_future_review_capacity")
        self.assertEqual(priority, -50)

    def test_imported_candidate_inherits_linked_machine_exhaustion(self) -> None:
        candidate = {
            "candidate_id": "pid_999",
            "rights_tier": "public_domain_us_federal_candidate",
        }
        validation = {"release_posture": "release_candidate"}

        next_step = audit_source_conversion_readiness.candidate_next_step(
            candidate,
            validation,
            imported=True,
            review=Counter({"unique_open_rows": 50, "unique_fresh_open_rows": 50}),
            linked_exhaustion_status="machine_exhausted_no_candidate",
        )
        priority = audit_source_conversion_readiness.candidate_priority(
            candidate,
            validation,
            Counter({"unique_open_rows": 50, "unique_fresh_open_rows": 50}),
            imported=True,
            linked_exhaustion_status="machine_exhausted_no_candidate",
        )

        self.assertEqual(next_step, "machine_exhausted_no_candidate")
        self.assertEqual(priority, -100)

    def test_linked_active_gold_wins_over_duplicate_mergeable_review_history(self) -> None:
        next_step = audit_source_conversion_readiness.candidate_next_step(
            {
                "candidate_id": "pid_999",
                "rights_tier": "cc_by_sa_3_0_candidate",
            },
            {"release_posture": "release_candidate"},
            imported=True,
            review=Counter({"review_rows": 40, "mergeable_rows": 40}),
            linked_gold_rows=20,
        )

        self.assertEqual(next_step, "linked_local_active_gold_or_reviewed")

    def test_staged_visualdiff_capacity_resolves_page_paths_and_manifest_pair_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / "derived" / "quality"
            quality.mkdir(parents=True)
            (root / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "pair",
                        "pair_id": "vdiff__demo__v1__to__v2",
                        "from_doc_id": "demo_v1",
                        "to_doc_id": "demo_v2",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            canonical = quality / "v2_0_canonical_future_capacity_2026-08-12-wave1.jsonl"
            canonical.write_text(
                json.dumps(
                    {
                        "pair_id": "vdiff__demo__v1__to__v2__p0000__001",
                        "project_id": "vdiff__demo__v1__to__v2",
                        "image_old": "derived/pages_300dpi/demo_v1/page_000.png",
                        "image_new": "derived/pages_300dpi/demo_v2/page_000.png",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            stats, _selected_paths = audit_source_conversion_readiness.collect_staged_future_stats(root)

            self.assertEqual(stats["demo_v1"]["staged_future_rows"], 1)
            self.assertEqual(stats["demo_v2"]["staged_future_rows"], 1)

    def test_visualdiff_review_is_attributed_to_both_revision_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            annotations = root / "visualdiff" / "annotations"
            annotations.mkdir(parents=True)
            (annotations / "visualdiff_review_demo.jsonl").write_text(
                json.dumps(
                    {
                        "pair_id": "vdiff__demo__v1__to__v2__p0000__001",
                        "project_id": "vdiff__demo__v1__to__v2",
                        "image_old": "derived/pages_300dpi/demo_v1/page_000.png",
                        "image_new": "derived/pages_300dpi/demo_v2/page_000.png",
                        "review_status": "needs_review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            stats, _candidate_stats = audit_source_conversion_readiness.collect_review_stats(root)

            self.assertEqual(stats["demo_v1"]["unique_open_rows"], 1)
            self.assertEqual(stats["demo_v2"]["unique_open_rows"], 1)

    def test_visualdiff_gold_is_attributed_to_both_revision_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            microtext = root / "microtext" / "annotations"
            visualdiff = root / "visualdiff" / "annotations"
            microtext.mkdir(parents=True)
            visualdiff.mkdir(parents=True)
            (microtext / "microtext_items.jsonl").write_text("", encoding="utf-8")
            (visualdiff / "visualdiff_pairs.jsonl").write_text(
                json.dumps(
                    {
                        "pair_id": "vdiff__demo__v1__to__v2__p0000__001",
                        "project_id": "vdiff__demo__v1__to__v2",
                        "image_old": "derived/pages_300dpi/demo_v1/page_000.png",
                        "image_new": "derived/pages_300dpi/demo_v2/page_000.png",
                        "split": "dev",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            stats = audit_source_conversion_readiness.collect_gold_stats(root)

            self.assertEqual(stats["demo_v1"]["gold_visualdiff_rows"], 1)
            self.assertEqual(stats["demo_v2"]["gold_visualdiff_rows"], 1)

    def test_visualdiff_render_is_not_routed_to_microtext_ocr(self) -> None:
        row = {
            "doc_id": "revision_v2",
            "domain": "mechanical_cad",
            "task": "visualdiff",
            "public_status": "gpl_3_0_open_source_candidate",
        }

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            Counter(),
            Counter(),
            pages=1,
            spans=0,
            mineable_candidates=0,
        )

        self.assertEqual(next_step, "visualdiff_pair_alignment_or_review")

    def test_duplicate_payload_alias_is_not_remined(self) -> None:
        row = {
            "doc_id": "duplicate_alias",
            "domain": "pid",
            "task": "microtext",
            "public_status": "cc_by_sa_3_0_commons_candidate",
        }

        next_step = audit_source_conversion_readiness.local_next_step(
            row,
            Counter(),
            Counter(),
            pages=1,
            spans=0,
            mineable_candidates=0,
            duplicate_payload_alias=True,
        )

        self.assertEqual(next_step, "duplicate_payload_alias")

    def test_current_validation_action_schema_preserves_deliberate_hold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "pcb_010",
                        "domain": "pcb_schematic",
                        "task_fit": "visualdiff,microtext",
                        "rights_tier": "misc_public_candidate",
                        "source_url": "https://example.test/spec",
                    }
                ],
            )
            write_csv(
                tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
                [
                    {
                        "candidate_id": "pcb_010",
                        "release_posture": "research_hold",
                        "validation_next_action": "hold_no_renderable_engineering_asset",
                    }
                ],
            )
            (tmp_path / "SOURCE_INTAKE_LOG.md").write_text(
                "## Hold\n\n- Candidate: `pcb_010`\n",
                encoding="utf-8",
            )

            report = audit_source_conversion_readiness.build_report(tmp_path)
            candidate = report["candidate_sources"][0]

            self.assertTrue(candidate["imported_or_staged"])
            self.assertEqual(candidate["next_step"], "select_or_deprioritize")

    def test_inventory_source_path_schema_is_preserved_for_render_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_INVENTORY.csv",
                [
                    {
                        "doc_id": "manual_doc",
                        "domain": "datasheet_spec",
                        "task": "microtext",
                        "public_status": "public_vendor_manual_candidate",
                        "source_path": "microtext/docs/manual.pdf",
                        "source_url": "https://example.test/manual.pdf",
                    }
                ],
            )
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "ds_999",
                        "domain": "datasheet_spec",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/manual.pdf",
                    }
                ],
            )

            report = audit_source_conversion_readiness.build_report(tmp_path)
            local = report["local_sources"][0]

            self.assertEqual(local["source_path"], "microtext/docs/manual.pdf")
            self.assertEqual(local["next_step"], "rights_review_or_hold")

    def test_manifest_source_candidate_and_alias_link_local_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "ds_006",
                        "domain": "pcb_schematic",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/rp2040",
                    },
                    {
                        "candidate_id": "ds_006_duplicate",
                        "domain": "pcb_schematic",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/rp2040-duplicate",
                    },
                ],
            )
            write_csv(
                tmp_path / "SOURCE_INVENTORY.csv",
                [
                    {
                        "path": "microtext/docs/rpi_rp2040_hardware_design.pdf",
                        "task": "microtext",
                        "doc_id": "rpi_rp2040_hardware_design",
                        "domain": "pcb_schematic",
                        "source_url": "https://example.test/rp2040.pdf",
                        "public_status": "public_domain_candidate",
                        "notes": "",
                    }
                ],
            )
            (tmp_path / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "rpi_rp2040_hardware_design",
                        "source_candidate_id": "ds_006",
                        "source_candidate_aliases": ["ds_006_duplicate"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = audit_source_conversion_readiness.build_report(tmp_path)
            candidates = {row["candidate_id"]: row for row in report["candidate_sources"]}

            self.assertEqual(
                candidates["ds_006"]["linked_local_doc_ids"],
                "rpi_rp2040_hardware_design",
            )
            self.assertIn(
                "manifest_source_candidate_id",
                candidates["ds_006"]["lineage_evidence"],
            )
            self.assertEqual(
                candidates["ds_006_duplicate"]["linked_local_doc_ids"],
                "rpi_rp2040_hardware_design",
            )
            self.assertIn(
                "manifest_candidate_alias",
                candidates["ds_006_duplicate"]["lineage_evidence"],
            )

    def test_alias_ledger_requires_canonical_lineage_for_the_doc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "pcb_047",
                        "domain": "pcb_schematic",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.test/adafruit",
                    },
                    {
                        "candidate_id": "pcb_016",
                        "domain": "pcb_schematic",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/adafruit",
                    },
                ],
            )
            write_csv(
                tmp_path / "SOURCE_INVENTORY.csv",
                [
                    {
                        "path": "visualdiff/docs/adafruit.sch",
                        "task": "visualdiff",
                        "doc_id": "adafruit_sch",
                        "domain": "pcb_schematic",
                        "source_url": "https://example.test/adafruit.sch",
                        "public_status": "public_candidate",
                        "notes": "",
                    },
                    {
                        "path": "visualdiff/docs/unrelated.sch",
                        "task": "visualdiff",
                        "doc_id": "unrelated_sch",
                        "domain": "pcb_schematic",
                        "source_url": "https://example.test/unrelated.sch",
                        "public_status": "public_candidate",
                        "notes": "",
                    },
                ],
            )
            (tmp_path / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "adafruit_sch",
                        "source_candidate_id": "pcb_047",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_csv(
                tmp_path / "SOURCE_CANDIDATE_ALIASES.csv",
                [
                    {
                        "alias_candidate_id": "pcb_016",
                        "canonical_candidate_id": "pcb_047",
                        "doc_id": "adafruit_sch",
                        "reason": "Exact duplicate source URL.",
                    },
                    {
                        "alias_candidate_id": "pcb_016",
                        "canonical_candidate_id": "pcb_047",
                        "doc_id": "unrelated_sch",
                        "reason": "Must not link without canonical evidence.",
                    },
                ],
            )

            report = audit_source_conversion_readiness.build_report(tmp_path)
            candidates = {row["candidate_id"]: row for row in report["candidate_sources"]}

            self.assertEqual(candidates["pcb_016"]["linked_local_doc_ids"], "adafruit_sch")
            self.assertIn("candidate_alias:pcb_047", candidates["pcb_016"]["lineage_evidence"])
            self.assertNotIn("unrelated_sch", candidates["pcb_016"]["linked_local_doc_ids"])

    def test_candidate_prefix_links_local_doc_and_propagates_rights_hold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "civil_015",
                        "domain": "civil",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/civil-015",
                    }
                ],
            )
            write_csv(
                tmp_path / "SOURCE_INVENTORY.csv",
                [
                    {
                        "path": "microtext/docs/civil_015_sheet.pdf",
                        "task": "microtext",
                        "doc_id": "civil_015_sheet",
                        "domain": "civil",
                        "source_url": "https://example.test/civil-015/sheet.pdf",
                        "public_status": "release_review_needed",
                        "notes": "Rights confirmation pending.",
                    }
                ],
            )

            report = audit_source_conversion_readiness.build_report(tmp_path)
            candidate = report["candidate_sources"][0]

            self.assertEqual(candidate["linked_local_doc_ids"], "civil_015_sheet")
            self.assertEqual(candidate["lineage_evidence"], "candidate_doc_id_prefix")
            self.assertEqual(candidate["linked_rights_blocked_source_count"], 1)
            self.assertEqual(candidate["next_step"], "rights_review_or_hold")

    def test_candidate_prefix_requires_delimiter_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "pid_041",
                        "domain": "pid",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/pid-041",
                    }
                ],
            )
            write_csv(
                tmp_path / "SOURCE_INVENTORY.csv",
                [
                    {
                        "path": "microtext/docs/pid_0410_sheet.pdf",
                        "task": "microtext",
                        "doc_id": "pid_0410_sheet",
                        "domain": "pid",
                        "source_url": "https://example.test/pid-0410/sheet.pdf",
                        "public_status": "public_candidate",
                        "notes": "Different candidate namespace.",
                    }
                ],
            )

            report = audit_source_conversion_readiness.build_report(tmp_path)

            self.assertEqual(report["candidate_sources"][0]["linked_local_source_count"], 0)

    def test_active_packet_keys_include_supplemental_review_pack_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pack_dir = tmp_path / "derived" / "review_packs" / "microtext_new"
            pack_dir.mkdir(parents=True)
            (pack_dir / "manifest.jsonl").write_text(
                json.dumps({"candidate_id": "mtcand__new__001", "doc_id": "new_doc"}) + "\n",
                encoding="utf-8",
            )
            write_json(
                tmp_path / "derived" / "quality" / "supplemental_review_pack_index_2026-07-05u.json",
                {
                    "date_label": "2026-07-05u",
                    "totals": {"packs": 1, "ready_to_send": 1, "manifest_rows": 1},
                    "packs": [
                        {
                            "packet_id": "microtext_new",
                            "ready_to_send": True,
                            "manifest_rows": 1,
                            "folder_path": "derived/review_packs/microtext_new",
                        }
                    ],
                },
            )

            keys, index_path = audit_source_conversion_readiness.load_active_packet_row_keys(
                tmp_path,
                "2026-07-05u",
            )

            self.assertEqual(index_path.name, "supplemental_review_pack_index_2026-07-05u.json")
            self.assertIn("mtcand__new__001", keys)

    def test_active_packet_keys_tolerate_legacy_encoded_checklist_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            pack_dir = tmp_path / "derived" / "review_packs" / "microtext_legacy_csv"
            pack_dir.mkdir(parents=True)
            (pack_dir / "manifest.jsonl").write_text(
                json.dumps({"candidate_id": "mtcand__legacy__001", "doc_id": "legacy_doc"}) + "\n",
                encoding="utf-8",
            )
            (pack_dir / "legacy_validation_checklist.csv").write_bytes(
                b"candidate_id,review_status,review_notes\n"
                b"mtcand__legacy__001,,needs human check \xbf\n"
            )
            write_json(
                tmp_path / "derived" / "quality" / "supplemental_review_pack_index_2026-07-05u.json",
                {
                    "date_label": "2026-07-05u",
                    "totals": {"packs": 1, "ready_to_send": 1, "manifest_rows": 1},
                    "packs": [
                        {
                            "packet_id": "microtext_legacy_csv",
                            "ready_to_send": True,
                            "manifest_rows": 1,
                            "folder_path": "derived/review_packs/microtext_legacy_csv",
                        }
                    ],
                },
            )

            keys, _index_path = audit_source_conversion_readiness.load_active_packet_row_keys(
                tmp_path,
                "2026-07-05u",
            )

            self.assertIn("mtcand__legacy__001", keys)

    def test_ready_packet_manifest_rows_count_as_local_review_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_INVENTORY.csv",
                [
                    {
                        "path": "microtext/docs/demo_sheet.pdf",
                        "task": "microtext",
                        "doc_id": "demo_sheet",
                        "domain": "pcb_schematic",
                        "source_url": "https://example.test/demo-sheet.pdf",
                        "public_status": "public_domain_candidate",
                        "notes": "",
                    }
                ],
            )
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "pcb_999",
                        "domain": "pcb_schematic",
                        "task_fit": "microtext",
                        "rights_tier": "public_domain_candidate",
                        "source_url": "https://example.test/demo",
                    }
                ],
            )
            page_dir = tmp_path / "derived" / "pages_300dpi" / "demo_sheet"
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"fake")
            textlayer_dir = tmp_path / "derived" / "textlayer"
            textlayer_dir.mkdir(parents=True)
            (textlayer_dir / "demo_sheet.jsonl").write_text(
                json.dumps(
                    {
                        "text": "VBUS",
                        "bbox": [1, 2, 3, 4],
                        "page_index": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            pack_dir = tmp_path / "derived" / "review_packs" / "demo_pack"
            pack_dir.mkdir(parents=True)
            (pack_dir / "manifest.jsonl").write_text(
                json.dumps(
                    {
                        "candidate_id": "mtcand__demo_sheet__unknown__p0000__000000",
                        "doc_id": "demo_sheet",
                        "image_path": "derived/pages_300dpi/demo_sheet/page_000.png",
                        "review_status": "needs_review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_json(
                tmp_path / "derived" / "quality" / "supplemental_review_pack_index_2026-07-05u.json",
                {
                    "date_label": "2026-07-05u",
                    "totals": {"packs": 1, "ready_to_send": 1, "manifest_rows": 1},
                    "packs": [
                        {
                            "packet_id": "demo_pack",
                            "ready_to_send": True,
                            "manifest_rows": 1,
                            "folder_path": "derived/review_packs/demo_pack",
                        }
                    ],
                },
            )

            report = audit_source_conversion_readiness.build_report(
                tmp_path,
                date_label="2026-07-05u",
            )

            local = report["local_sources"][0]
            self.assertEqual(local["review_rows"], 1)
            self.assertEqual(local["open_review_rows"], 1)
            self.assertEqual(local["packeted_open_review_rows"], 1)
            self.assertEqual(local["fresh_open_review_rows"], 0)
            self.assertEqual(local["next_step"], "await_human_return")
