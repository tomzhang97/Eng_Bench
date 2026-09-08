from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_microtext_source_review_queue


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class MicrotextSourceReviewQueueUnittest(unittest.TestCase):
    def test_bbox_overlap_over_smaller_detects_contained_alias_box(self) -> None:
        self.assertEqual(
            build_microtext_source_review_queue.bbox_overlap_over_smaller(
                (0, 0, 20, 20), (2, 2, 18, 18)
            ),
            1.0,
        )
        self.assertEqual(
            build_microtext_source_review_queue.bbox_overlap_over_smaller(
                (0, 0, 10, 10), (20, 20, 30, 30)
            ),
            0.0,
        )

    def test_loads_only_requested_microtext_split_units(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split_plan = root / "split.json"
            split_plan.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "reservations": [
                            {"task": "microtext", "unit_id": "test_doc", "split": "test"},
                            {"task": "microtext", "unit_id": "train_doc", "split": "train"},
                            {"task": "visualdiff", "unit_id": "family", "split": "test"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                build_microtext_source_review_queue.split_plan_doc_ids(
                    root, Path("split.json"), "test"
                ),
                {"test_doc"},
            )
            self.assertEqual(
                build_microtext_source_review_queue.microtext_split_reservations(
                    root, Path("split.json")
                ),
                {"test_doc": "test", "train_doc": "train"},
            )

            payload = json.loads(split_plan.read_text(encoding="utf-8"))
            payload["reservations"][0].update(
                {"reservation_id": "test-id", "assignment_basis": "family_lock"}
            )
            split_plan.write_text(json.dumps(payload), encoding="utf-8")
            enriched = build_microtext_source_review_queue.apply_authoritative_split_reservations(
                root,
                [{"candidate_id": "candidate", "doc_id": "test_doc"}],
                Path("split.json"),
                "test",
            )
            self.assertEqual("test", enriched[0]["reserved_split"])
            self.assertEqual("test-id", enriched[0]["split_reservation_id"])
            self.assertEqual("family_lock", enriched[0]["split_reservation_basis"])
            self.assertFalse(enriched[0]["safe_to_merge_gold"])

    def test_capacity_report_exclusions_fail_closed_and_skip_visualdiff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cohort = root / "cohort.jsonl"
            write_jsonl(
                cohort,
                [
                    {
                        "candidate_id": "micro",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": [1, 2, 3, 4],
                    },
                    {"pair_id": "visual", "id": "alias"},
                ],
            )
            report = root / "capacity.json"
            report.write_text(
                json.dumps(
                    {
                        "capacity_input_clean": True,
                        "cohorts": [{"path": "cohort.jsonl"}],
                    }
                ),
                encoding="utf-8",
            )

            paths = build_microtext_source_review_queue.capacity_report_review_paths(
                root, [Path("capacity.json")]
            )
            ids, regions = build_microtext_source_review_queue.review_exclusions_from_files(
                root, paths
            )
            self.assertEqual(ids, {"micro"})
            self.assertEqual(regions, {("doc", 0, (1, 2, 3, 4))})

            report.write_text(
                json.dumps(
                    {
                        "capacity_input_clean": False,
                        "cohorts": [{"path": "cohort.jsonl"}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                build_microtext_source_review_queue.capacity_report_review_paths(
                    root, [Path("capacity.json")]
                )

    def test_finds_duplicate_payload_alias_doc_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sha256 = hashlib.sha256(b"same payload").hexdigest()
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {"type": "doc", "doc_id": "canonical", "sha256": sha256},
                    {"type": "doc", "doc_id": "duplicate", "sha256": sha256},
                ],
            )
            self.assertEqual(
                build_microtext_source_review_queue.duplicate_payload_alias_doc_ids(root),
                {"duplicate"},
            )
            self.assertEqual(
                build_microtext_source_review_queue.duplicate_payload_alias_doc_ids(
                    root, ({"duplicate"},)
                ),
                {"canonical"},
            )

    def test_tolerance_values_are_supported_for_drawing_domains(self) -> None:
        for domain in ("datasheet_spec", "mechanical_cad", "civil_architectural"):
            self.assertTrue(
                build_microtext_source_review_queue.domain_category_compatible(
                    domain, "tolerance_value"
                )
            )
        self.assertIn("tolerance_value", build_microtext_source_review_queue.DEFAULT_CATEGORIES)

    def test_pcb_schematic_supports_mechanical_dimensions_but_not_process_tags(self) -> None:
        for category in ("component_value", "dimension_value", "pin_label", "tolerance_value"):
            self.assertTrue(
                build_microtext_source_review_queue.domain_category_compatible(
                    "pcb_schematic", category
                )
            )
        self.assertFalse(
            build_microtext_source_review_queue.domain_category_compatible(
                "pcb_schematic", "pipe_line_tag"
            )
        )

    def test_inventory_domain_aliases_use_canonical_category_policy(self) -> None:
        self.assertFalse(
            build_microtext_source_review_queue.domain_category_compatible(
                "mechanical", "instrument_tag"
            )
        )
        self.assertTrue(
            build_microtext_source_review_queue.domain_category_compatible(
                "mechanical", "tolerance_value"
            )
        )
        for domain in ("civil_hydraulic", "civil_structural"):
            self.assertFalse(
                build_microtext_source_review_queue.domain_category_compatible(
                    domain, "instrument_tag"
                )
            )
            self.assertTrue(
                build_microtext_source_review_queue.domain_category_compatible(
                    domain, "dimension_value"
                )
            )

    def test_process_labels_are_supported_only_for_pid_sources(self) -> None:
        self.assertTrue(
            build_microtext_source_review_queue.domain_category_compatible("pid", "process_label")
        )
        self.assertFalse(
            build_microtext_source_review_queue.domain_category_compatible(
                "civil_architectural", "process_label"
            )
        )

    def test_selects_only_fresh_inactive_release_registered_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["doc_id", "domain", "public_status"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"doc_id": "good_doc", "domain": "pid", "public_status": "public_domain"},
                        {"doc_id": "active_doc", "domain": "pid", "public_status": "public_domain"},
                        {"doc_id": "held_doc", "domain": "pid", "public_status": "rights_uncertain"},
                    ]
                )
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=["candidate_id", "release_posture", "next_action"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"candidate_id": "good", "release_posture": "release_candidate", "next_action": "intake_first"},
                        {"candidate_id": "held", "release_posture": "release_candidate", "next_action": "rights_hold"},
                    ]
                )
            evidence = root / "evidence"
            evidence.mkdir()
            Image.new("RGB", (20, 20), "white").save(evidence / "page.png")
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [{"item_id": "existing", "doc_id": "active_doc"}],
            )
            base = {
                "review_status": "needs_review",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 1, 10, 10],
                "image_path": "evidence/page.png",
                "source_candidate_id": "good",
            }
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_candidates.jsonl",
                [
                    {**base, "candidate_id": "good_1", "doc_id": "good_doc", "category": "equipment_tag", "proposed_text": "P-101"},
                    {
                        **base,
                        "candidate_id": "good_2",
                        "doc_id": "good_doc",
                        "bbox": [2, 2, 11, 11],
                        "category": "instrument_tag",
                        "proposed_text": "FT-101",
                    },
                    {
                        **base,
                        "candidate_id": "active",
                        "doc_id": "active_doc",
                        "source_candidate_id": "unregistered_active",
                        "category": "equipment_tag",
                        "proposed_text": "P-102",
                    },
                    {**base, "candidate_id": "rights", "doc_id": "held_doc", "category": "equipment_tag", "proposed_text": "P-103"},
                    {**base, "candidate_id": "source_hold", "doc_id": "good_doc", "source_candidate_id": "held", "category": "equipment_tag", "proposed_text": "P-104"},
                    {**base, "candidate_id": "unknown", "doc_id": "good_doc", "category": "unknown_microtext", "proposed_text": "P-105"},
                ],
            )

            rows, report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=3,
                require_paper_ready_source=False,
            )

            self.assertEqual([row["candidate_id"] for row in rows], ["good_1", "good_2"])
            self.assertEqual(report["selected_source_docs"], 1)
            self.assertEqual(report["selected_rows"], 2)
            self.assertEqual(report["counters"]["excluded_active_source_doc"], 1)
            self.assertEqual(report["counters"]["excluded_not_release_safe"], 1)
            self.assertEqual(report["counters"]["excluded_not_release_registered"], 1)
            self.assertEqual(report["counters"]["excluded_category"], 1)

            scale_rows, scale_report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=3,
                require_paper_ready_source=False,
                allow_active_source_docs=True,
            )

            self.assertEqual(
                {row["candidate_id"] for row in scale_rows},
                {"active", "good_1", "good_2"},
            )
            self.assertTrue(scale_report["allow_active_source_docs"])
            self.assertNotIn("excluded_active_source_doc", scale_report["counters"])
            self.assertEqual(scale_report["counters"]["active_source_registration_exemptions"], 1)

            restricted_rows, restricted_report = (
                build_microtext_source_review_queue.select_source_rows(
                    root,
                    packet_date_label="missing-index",
                    target_source_docs=10,
                    rows_per_source=3,
                    require_paper_ready_source=False,
                    allow_active_source_docs=True,
                    allowed_doc_ids={"active_doc"},
                )
            )
            self.assertEqual([row["candidate_id"] for row in restricted_rows], ["active"])
            self.assertEqual(restricted_report["allowed_doc_count"], 1)
            self.assertEqual(restricted_report["counters"]["excluded_not_allowed_doc"], 5)

            input_rows, input_report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=3,
                input_review_paths=[
                    Path("microtext/annotations/microtext_review_candidates.jsonl")
                ],
                require_paper_ready_source=False,
            )
            self.assertEqual([row["candidate_id"] for row in input_rows], ["good_1", "good_2"])
            self.assertEqual(len(input_report["input_review_files"]), 1)

    def test_candidate_ids_from_review_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "exclude.jsonl"
            write_jsonl(path, [{"candidate_id": "one"}, {"candidate_id": "two"}])
            self.assertEqual(
                build_microtext_source_review_queue.candidate_ids_from_review_files(
                    root, [Path("exclude.jsonl")]
                ),
                {"one", "two"},
            )

    def test_exclude_review_file_blocks_same_region_under_an_alias_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "exclude.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "candidate_id": "new_id",
                        "doc_id": "doc",
                        "page_index": 2,
                        "bbox": [1, 2, 11, 12],
                    }
                ],
            )
            ids, regions = build_microtext_source_review_queue.review_exclusions_from_files(
                root, [Path("exclude.jsonl")]
            )

            self.assertEqual(ids, {"new_id"})
            self.assertEqual(regions, {("doc", 2, (1, 2, 11, 12))})

    def test_semantic_exclusion_blocks_stale_geometry_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "exclude.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "candidate_id": "new_render",
                        "doc_id": "doc",
                        "page_index": 2,
                        "bbox": [150, 300, 210, 330],
                        "category": "pin_label",
                        "proposed_text": "PI_0",
                    }
                ],
            )

            identities = (
                build_microtext_source_review_queue.review_semantic_exclusions_from_files(
                    root, [Path("exclude.jsonl")]
                )
            )

            self.assertEqual(identities, {("doc", 2, "pin_label", "pi_0")})
            self.assertIn(
                build_microtext_source_review_queue.microtext_semantic_identity(
                    {
                        "candidate_id": "old_render",
                        "doc_id": "doc",
                        "page_index": 2,
                        "bbox": [100, 200, 140, 220],
                        "category": "pin_label",
                        "target_text": " PI_0 ",
                    }
                ),
                identities,
            )

    def test_terminal_history_vetoes_stale_open_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (root / "SOURCE_INVENTORY.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["doc_id", "domain", "public_status"]
                )
                writer.writeheader()
                writer.writerow(
                    {"doc_id": "doc", "domain": "pid", "public_status": "public_domain"}
                )
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["candidate_id", "release_posture", "next_action"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "source",
                        "release_posture": "release_candidate",
                        "next_action": "intake_first",
                    }
                )
            evidence = root / "evidence"
            evidence.mkdir()
            Image.new("RGB", (20, 20), "white").save(evidence / "page.png")
            base = {
                "doc_id": "doc",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 1, 10, 10],
                "image_path": "evidence/page.png",
                "source_candidate_id": "source",
                "category": "equipment_tag",
                "proposed_text": "P-101",
            }
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_open.jsonl",
                [{**base, "candidate_id": "stale", "review_status": "needs_review"}],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_held.jsonl",
                [
                    {
                        **base,
                        "candidate_id": "stale",
                        "review_status": "machine_held",
                        "machine_hold_reason": "target_not_visible",
                    }
                ],
            )

            rows, report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=3,
                require_paper_ready_source=False,
            )

            self.assertEqual(rows, [])
            self.assertEqual(report["counters"]["excluded_terminal_history_identity"], 1)
            self.assertEqual(report["terminal_history_identity_count"], 1)

    def test_unresolved_versions_are_fail_closed_by_default(self) -> None:
        self.assertEqual(
            build_microtext_source_review_queue.resolved_version_id(
                {"version_id": "unknown"}
            ),
            "",
        )
        self.assertEqual(
            build_microtext_source_review_queue.resolved_version_id({"version_id": "v1"}),
            "v1",
        )

    def test_region_exclusion_and_dedup_do_not_depend_on_candidate_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["doc_id", "domain", "public_status"])
                writer.writeheader()
                writer.writerow({"doc_id": "doc", "domain": "pid", "public_status": "public_domain"})
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=["candidate_id", "release_posture", "next_action"])
                writer.writeheader()
                writer.writerow(
                    {"candidate_id": "source", "release_posture": "release_candidate", "next_action": "intake_first"}
                )
            evidence = root / "evidence"
            evidence.mkdir()
            Image.new("RGB", (20, 20), "white").save(evidence / "page.png")
            base = {
                "doc_id": "doc",
                "review_status": "needs_review",
                "version_id": "v1",
                "page_index": 0,
                "image_path": "evidence/page.png",
                "source_candidate_id": "source",
                "category": "equipment_tag",
            }
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_one.jsonl",
                [
                    {**base, "candidate_id": "alias_a", "bbox": [1, 1, 10, 10], "proposed_text": "P-101"},
                    {**base, "candidate_id": "held_alias", "bbox": [2, 2, 11, 11], "proposed_text": "P-102"},
                ],
            )
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_two.jsonl",
                [{**base, "candidate_id": "alias_b", "bbox": [1, 1, 10, 10], "proposed_text": "P-101"}],
            )

            rows, report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=10,
                require_paper_ready_source=False,
                excluded_region_keys={("doc", 0, (2, 2, 11, 11))},
            )

            self.assertEqual(len(rows), 1)
            self.assertIn(rows[0]["candidate_id"], {"alias_a", "alias_b"})
            self.assertEqual(report["counters"]["excluded_region"], 1)
            self.assertEqual(report["excluded_region_count"], 1)

            overlap_rows, overlap_report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=10,
                require_paper_ready_source=False,
                excluded_region_keys={("doc", 0, (0, 0, 12, 12))},
                excluded_region_overlap_threshold=0.8,
            )
            self.assertEqual(overlap_rows, [])
            self.assertEqual(overlap_report["counters"]["excluded_region_overlap"], 3)

    def test_infers_release_registered_source_candidate_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["doc_id", "domain", "public_status"])
                writer.writeheader()
                writer.writerow({"doc_id": "doc", "domain": "civil_architectural", "public_status": "public_domain"})
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=["candidate_id", "release_posture", "next_action"])
                writer.writeheader()
                writer.writerow(
                    {"candidate_id": "civil", "release_posture": "release_candidate", "next_action": "intake_first"}
                )
            write_jsonl(
                root / "manifest.jsonl",
                [{"type": "doc", "doc_id": "doc", "source_candidate_id": "civil"}],
            )
            evidence = root / "evidence"
            evidence.mkdir()
            Image.new("RGB", (20, 20), "white").save(evidence / "page.png")
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_candidates.jsonl",
                [
                    {
                        "candidate_id": "candidate",
                        "doc_id": "doc",
                        "review_status": "needs_review",
                        "version_id": "v1",
                        "page_index": 0,
                        "bbox": [1, 1, 10, 10],
                        "image_path": "evidence/page.png",
                        "category": "dimension_value",
                        "proposed_text": "10'",
                    }
                ],
            )

            rows, report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="missing-index",
                target_source_docs=10,
                rows_per_source=3,
                require_paper_ready_source=False,
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_candidate_id"], "civil")
            self.assertTrue(rows[0]["selection_source_candidate_inferred"])
            self.assertEqual(report["counters"]["inferred_source_candidate_from_manifest"], 1)

    def test_item_audited_override_is_explicit_and_still_requires_strict_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = root / "sources"
            sources.mkdir()
            audited_payload = sources / "audited.pdf"
            audited_payload.write_bytes(b"audited source payload")
            audited_sha = hashlib.sha256(audited_payload.read_bytes()).hexdigest()

            with (root / "SOURCE_INVENTORY.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "doc_id",
                        "domain",
                        "public_status",
                        "source_path",
                        "source_url",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "doc_id": "audited_doc",
                            "domain": "civil_architectural",
                            "public_status": "public_domain",
                            "source_path": "sources/audited.pdf",
                            "source_url": "https://example.test/audited",
                        },
                        {
                            "doc_id": "unready_doc",
                            "domain": "civil_architectural",
                            "public_status": "public_domain",
                            "source_path": "sources/missing.pdf",
                            "source_url": "https://example.test/unready",
                        },
                    ]
                )
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["candidate_id", "release_posture", "next_action"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "candidate_id": "held_parent",
                            "release_posture": "release_candidate",
                            "next_action": "rights_review",
                        },
                        {
                            "candidate_id": "other_parent",
                            "release_posture": "release_candidate",
                            "next_action": "rights_review",
                        },
                    ]
                )
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "doc_id": "audited_doc",
                        "source_candidate_id": "held_parent",
                        "sha256": audited_sha,
                    },
                    {
                        "type": "doc",
                        "doc_id": "unready_doc",
                        "source_candidate_id": "held_parent",
                        "sha256": "0" * 64,
                    },
                ],
            )
            evidence = root / "evidence"
            evidence.mkdir()
            Image.new("RGB", (20, 20), "white").save(evidence / "page.png")
            base = {
                "review_status": "needs_review",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 1, 10, 10],
                "image_path": "evidence/page.png",
                "source_candidate_id": "held_parent",
                "category": "dimension_value",
                "proposed_text": "10'-0\"",
            }
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_candidates.jsonl",
                [
                    {**base, "candidate_id": "audited", "doc_id": "audited_doc"},
                    {
                        **base,
                        "candidate_id": "lineage_mismatch",
                        "doc_id": "audited_doc",
                        "source_candidate_id": "other_parent",
                    },
                    {**base, "candidate_id": "unready", "doc_id": "unready_doc"},
                ],
            )

            default_rows, default_report = (
                build_microtext_source_review_queue.select_source_rows(
                    root,
                    packet_date_label="missing-index",
                    target_source_docs=10,
                    rows_per_source=3,
                )
            )
            self.assertEqual(default_rows, [])
            self.assertEqual(default_report["counters"]["excluded_not_release_registered"], 2)
            self.assertEqual(default_report["counters"]["excluded_not_paper_ready"], 1)

            override_rows, override_report = (
                build_microtext_source_review_queue.select_source_rows(
                    root,
                    packet_date_label="missing-index",
                    target_source_docs=10,
                    rows_per_source=3,
                    allow_item_audited_source_docs=True,
                )
            )
            self.assertEqual([row["candidate_id"] for row in override_rows], ["audited"])
            self.assertTrue(override_report["allow_item_audited_source_docs"])
            self.assertEqual(
                override_report["counters"][
                    "item_audited_source_doc_registration_exemptions"
                ],
                1,
            )
            self.assertEqual(
                override_report["counters"]["excluded_not_release_registered"], 1
            )
            self.assertEqual(override_report["counters"]["excluded_not_paper_ready"], 1)

    def test_excludes_other_rows_from_a_source_already_in_an_active_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (root / "SOURCE_INVENTORY.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["doc_id", "domain", "public_status"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"doc_id": "fresh_doc", "domain": "pid", "public_status": "public_domain"},
                        {"doc_id": "packeted_doc", "domain": "pid", "public_status": "public_domain"},
                    ]
                )
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=["candidate_id", "release_posture", "next_action"])
                writer.writeheader()
                writer.writerow(
                    {"candidate_id": "good", "release_posture": "release_candidate", "next_action": "intake_first"}
                )
            evidence = root / "evidence"
            evidence.mkdir()
            Image.new("RGB", (20, 20), "white").save(evidence / "page.png")
            base = {
                "review_status": "needs_review",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 1, 10, 10],
                "image_path": "evidence/page.png",
                "source_candidate_id": "good",
                "category": "equipment_tag",
            }
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_review_candidates.jsonl",
                [
                    {**base, "candidate_id": "fresh", "doc_id": "fresh_doc", "proposed_text": "P-101"},
                    {**base, "candidate_id": "packeted_new_row", "doc_id": "packeted_doc", "proposed_text": "P-102"},
                    {
                        **base,
                        "candidate_id": "packeted_region_alias",
                        "doc_id": "packeted_doc",
                        "bbox": [2, 2, 11, 11],
                        "proposed_text": "P-100",
                    },
                ],
            )
            packet = root / "derived" / "human_adjudication" / "packet"
            write_jsonl(
                packet / "review_packs" / "pack" / "manifest.jsonl",
                [
                    {
                        **base,
                        "candidate_id": "packeted_existing_row",
                        "doc_id": "packeted_doc",
                        "bbox": [2, 2, 11, 11],
                        "proposed_text": "P-100",
                    }
                ],
            )
            index_path = root / "derived" / "quality" / "human_packet_index_current.json"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                                "folder_path": "derived/human_adjudication/packet",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            rows, report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="current",
                target_source_docs=10,
                rows_per_source=3,
                require_paper_ready_source=False,
            )

            self.assertEqual([row["candidate_id"] for row in rows], ["fresh"])
            self.assertEqual(report["active_packet_source_docs_seen"], 1)
            self.assertEqual(report["counters"]["excluded_active_packet_source_doc"], 1)

            scale_rows, scale_report = build_microtext_source_review_queue.select_source_rows(
                root,
                packet_date_label="current",
                target_source_docs=10,
                rows_per_source=3,
                require_paper_ready_source=False,
                allow_active_packet_source_docs=True,
            )

            self.assertEqual(
                [row["candidate_id"] for row in scale_rows],
                ["fresh", "packeted_new_row"],
            )
            self.assertTrue(scale_report["allow_active_packet_source_docs"])
            self.assertNotIn("excluded_active_packet_source_doc", scale_report["counters"])
            self.assertEqual(scale_report["counters"]["excluded_active_packet"], 1)


if __name__ == "__main__":
    unittest.main()
