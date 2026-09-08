import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_staged_v2_capacity import CohortSpec, build_report, read_rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def add_doc(root: Path, doc_id: str, payload: bytes, task: str = "microtext") -> dict:
    relative = Path("sources") / f"{doc_id}.pdf"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "type": "doc",
        "doc_id": doc_id,
        "task": task,
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source_url": f"https://example.test/{doc_id}",
        "public_status": "public_domain",
    }


class StagedV2CapacityTest(unittest.TestCase):
    def test_read_rows_accepts_structured_json_cohorts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = [{"candidate_id": "candidate_a"}, {"candidate_id": "candidate_b"}]
            row_array = root / "array.json"
            row_object = root / "object.json"
            invalid = root / "invalid.json"
            row_array.write_text(json.dumps(expected), encoding="utf-8")
            row_object.write_text(json.dumps({"rows": expected}), encoding="utf-8")
            invalid.write_text(json.dumps({"rows": "not-a-list"}), encoding="utf-8")

            self.assertEqual(read_rows(row_array), expected)
            self.assertEqual(read_rows(row_object), expected)
            with self.assertRaisesRegex(ValueError, "JSON cohort"):
                read_rows(invalid)

    def build_root(self) -> tuple[Path, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        docs = [
            add_doc(root, "active_doc", b"active"),
            add_doc(root, "new_doc", b"new"),
            add_doc(root, "new_doc_alias", b"new"),
            add_doc(root, "old_rev", b"old revision", "visualdiff"),
            add_doc(root, "new_rev", b"new revision", "visualdiff"),
            {
                "type": "pair",
                "pair_id": "vdiff__family__v1__to__v2",
                "task": "visualdiff",
                "from_doc_id": "old_rev",
                "to_doc_id": "new_rev",
            },
        ]
        write_jsonl(root / "manifest.jsonl", docs)
        inventory_path = root / "SOURCE_INVENTORY.csv"
        with inventory_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["doc_id", "path", "source_url", "public_status", "task"]
            )
            writer.writeheader()
            for row in docs:
                if row.get("type") != "doc":
                    continue
                writer.writerow(
                    {
                        "doc_id": row["doc_id"],
                        "path": row["path"],
                        "source_url": row["source_url"],
                        "public_status": row["public_status"],
                        "task": row["task"],
                    }
                )
        write_jsonl(
            root / "microtext/annotations/microtext_items.jsonl",
            [
                {
                    "item_id": "mt__active",
                    "source_candidate_id": "mtcand__active",
                    "doc_id": "active_doc",
                    "split": "test",
                }
            ],
        )
        write_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl", [])
        return root, temp

    def test_deduplicates_payloads_and_projects_capacity(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        future = root / "future.jsonl"
        write_jsonl(
            current,
            [
                {"candidate_id": "candidate_new", "doc_id": "new_doc"},
                {"candidate_id": "candidate_alias", "doc_id": "new_doc_alias"},
            ],
        )
        write_jsonl(
            future,
            [
                {
                    "pair_id": "vdiff__family__v1__to__v2__001",
                    "project_id": "vdiff__family__v1__to__v2",
                    "split": "provisional_review",
                }
            ],
        )
        report = build_report(
            root,
            [
                CohortSpec("current", "current", current),
                CohortSpec("future", "future", future),
            ],
            agreement_decisions=185,
            row_target=10,
            source_target=4,
            family_target=1,
            test_target=5,
        )
        self.assertTrue(report["capacity_input_clean"])
        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 3)
        self.assertEqual(report["source_capacity"]["all_new_payloads"], 3)
        self.assertEqual(report["targets"]["unique_source_payloads"]["all_staged_upper_bound"], 4)
        self.assertTrue(report["targets"]["visualdiff_families"]["can_close_from_staged_capacity"])
        self.assertEqual(report["targets"]["test_rows"]["explicit_all_staged_upper_bound"], 1)
        self.assertEqual(
            report["targets"]["test_rows"]["unassigned_or_provisional_staged_rows"],
            3,
        )

    def test_balance_projection_counts_strict_component_values_as_canonical(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        active_rows = [
            {
                "item_id": f"mt__active_{index}",
                "doc_id": "active_doc",
                "category": "pin_label" if index < 6 else "dimension_value",
                "split": "test",
            }
            for index in range(10)
        ]
        write_jsonl(root / "microtext/annotations/microtext_items.jsonl", active_rows)
        future = root / "future.jsonl"
        write_jsonl(
            future,
            [
                {
                    "candidate_id": f"pin_{index}",
                    "doc_id": "new_doc",
                    "category": "pin_label",
                    "proposed_text": f"P{index}",
                }
                for index in range(100)
            ]
            + [
                {
                    "candidate_id": "dimension",
                    "doc_id": "new_doc",
                    "category": "dimension_value",
                    "proposed_text": "10 mm",
                },
                {
                    "candidate_id": "provisional",
                    "doc_id": "new_doc",
                    "category": "component_value",
                    "proposed_text": "1K",
                },
            ],
        )

        report = build_report(
            root,
            [CohortSpec("future", "future", future)],
            row_target=50,
        )
        balance = report["microtext_balance_capacity"]

        self.assertEqual(balance["staged"]["canonical_non_pin_rows"], 2)
        self.assertEqual(balance["staged"]["provisional_taxonomy_rows"], 0)
        self.assertGreater(
            balance["row_target_projection"]["additional_canonical_non_pin_rows_needed"],
            0,
        )
        self.assertFalse(
            balance["row_target_projection"]["canonical_balance_compliant_row_target_feasible"]
        )

    def test_flags_active_and_cross_cohort_identity_overlap(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        future = root / "future.jsonl"
        write_jsonl(current, [{"candidate_id": "mtcand__active", "doc_id": "active_doc"}])
        write_jsonl(future, [{"source_candidate_id": "mtcand__active", "doc_id": "active_doc"}])
        report = build_report(
            root,
            [
                CohortSpec("current", "current", current),
                CohortSpec("future", "future", future),
            ],
        )
        self.assertFalse(report["capacity_input_clean"])
        self.assertEqual(report["cohorts"][0]["active_gold_overlaps"], 1)
        self.assertEqual(report["overlaps"]["cross_cohort_identity_count"], 1)
        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 0)

    def test_generic_source_candidate_id_is_not_a_row_alias(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        future = root / "future.jsonl"
        write_jsonl(
            current,
            [{"candidate_id": "row_one", "source_candidate_id": "pcb_075", "doc_id": "new_doc"}],
        )
        write_jsonl(
            future,
            [{"candidate_id": "row_two", "source_candidate_id": "pcb_075", "doc_id": "new_doc"}],
        )

        report = build_report(
            root,
            [
                CohortSpec("current", "current", current),
                CohortSpec("future", "future", future),
            ],
        )

        self.assertTrue(report["capacity_input_clean"])
        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 2)

    def test_flags_same_microtext_region_under_different_candidate_ids(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        future = root / "future.jsonl"
        shared_region = {
            "doc_id": "new_doc",
            "page_index": 3,
            "bbox": [10, 20, 30, 40],
        }
        write_jsonl(current, [{**shared_region, "candidate_id": "ordinal_id"}])
        write_jsonl(future, [{**shared_region, "candidate_id": "fingerprint_id"}])

        report = build_report(
            root,
            [
                CohortSpec("current", "current", current),
                CohortSpec("future", "future", future),
            ],
        )

        self.assertFalse(report["capacity_input_clean"])
        self.assertEqual(report["overlaps"]["cross_cohort_identity_count"], 1)
        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 1)

    def test_flags_near_duplicate_microtext_regions_with_different_padding(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        future = root / "future.jsonl"
        write_jsonl(
            current,
            [
                {
                    "candidate_id": "tight_box",
                    "doc_id": "new_doc",
                    "page_index": 3,
                    "bbox": [10, 20, 30, 40],
                }
            ],
        )
        write_jsonl(
            future,
            [
                {
                    "candidate_id": "padded_box",
                    "doc_id": "new_doc",
                    "page_index": 3,
                    "bbox": [12, 20, 32, 40],
                }
            ],
        )

        report = build_report(
            root,
            [
                CohortSpec("current", "current", current),
                CohortSpec("future", "future", future),
            ],
        )

        self.assertFalse(report["capacity_input_clean"])
        self.assertEqual(report["overlaps"]["cross_cohort_identity_count"], 0)
        self.assertEqual(report["overlaps"]["near_region_pair_count"], 1)
        self.assertEqual(report["overlaps"]["near_region_examples"][0]["doc_id"], "new_doc")

    def test_payload_alias_regions_are_removed_from_capacity(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        for doc_id, size in (("new_doc", (400, 200)), ("new_doc_alias", (200, 100))):
            image_path = root / "derived" / "pages_300dpi" / doc_id / "page_000.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", size, "white").save(image_path)
        write_jsonl(
            root / "microtext/annotations/microtext_items.jsonl",
            [
                {
                    "item_id": "active_alias_region",
                    "doc_id": "new_doc_alias",
                    "page_index": 0,
                    "bbox": [50, 20, 100, 40],
                    "split": "test",
                }
            ],
        )
        alias_report = root / "payload_aliases.json"
        alias_report.write_text(
            json.dumps(
                {
                    "duplicate_groups": [
                        {
                            "canonical_doc_id": "new_doc",
                            "doc_ids": ["new_doc", "new_doc_alias"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        future = root / "future.jsonl"
        write_jsonl(
            future,
            [
                {
                    "candidate_id": "duplicates_active_alias",
                    "doc_id": "new_doc",
                    "page_index": 0,
                    "bbox": [100, 40, 200, 80],
                    "split": "test",
                },
                {
                    "candidate_id": "first_staged_region",
                    "doc_id": "new_doc",
                    "page_index": 0,
                    "bbox": [240, 40, 320, 80],
                    "split": "test",
                },
                {
                    "candidate_id": "duplicates_staged_alias",
                    "doc_id": "new_doc_alias",
                    "page_index": 0,
                    "bbox": [120, 20, 160, 40],
                    "split": "test",
                },
            ],
        )

        report = build_report(
            root,
            [CohortSpec("future", "future", future)],
            payload_alias_report_path=alias_report,
        )

        self.assertFalse(report["capacity_input_clean"])
        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 1)
        self.assertEqual(report["cohorts"][0]["payload_alias_active_overlaps"], 1)
        self.assertEqual(report["cohorts"][0]["payload_alias_staged_overlaps"], 1)
        self.assertEqual(report["overlaps"]["payload_alias_active_overlap_count"], 1)
        self.assertEqual(report["overlaps"]["payload_alias_staged_overlap_count"], 1)
        self.assertEqual(
            report["capacity"]["all_staged_expansion_accepted"]["explicit_test_rows"],
            2,
        )

    def test_applies_explicit_staged_split_reservations(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        future = root / "future.jsonl"
        write_jsonl(current, [{"candidate_id": "candidate_new", "doc_id": "new_doc"}])
        write_jsonl(
            future,
            [
                {
                    "pair_id": "vdiff__family__v1__to__v2__001",
                    "project_id": "vdiff__family__v1__to__v2",
                }
            ],
        )
        split_plan = root / "split_plan.json"
        split_plan.write_text(
            json.dumps(
                {
                    "valid": True,
                    "reservations": [
                        {"task": "microtext", "unit_id": "new_doc", "split": "test"},
                        {
                            "task": "visualdiff",
                            "unit_id": "vdiff__family__v1__to__v2",
                            "split": "dev",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = build_report(
            root,
            [
                CohortSpec("current", "current", current),
                CohortSpec("future", "future", future),
            ],
            test_target=5,
            split_plan_path=split_plan,
        )

        self.assertTrue(report["capacity_input_clean"])
        self.assertTrue(report["split_plan"]["applied"])
        self.assertEqual(report["capacity"]["all_staged_expansion_accepted"]["explicit_test_rows"], 2)
        self.assertEqual(report["targets"]["test_rows"]["unassigned_or_provisional_staged_rows"], 0)

    def test_counts_reserved_split_without_external_split_plan(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        write_jsonl(
            current,
            [
                {
                    "candidate_id": "candidate_new",
                    "doc_id": "new_doc",
                    "reserved_split": "test",
                }
            ],
        )

        report = build_report(
            root,
            [CohortSpec("current", "current", current)],
        )

        self.assertEqual(report["cohorts"][0]["split_counts"]["test"], 1)
        self.assertEqual(
            report["capacity"]["all_staged_expansion_accepted"]["explicit_test_rows"],
            2,
        )

    def test_provenance_replacements_do_not_inflate_expansion_gates(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        future = root / "future.jsonl"
        write_jsonl(
            future,
            [
                {
                    "candidate_id": "replacement",
                    "doc_id": "new_doc",
                    "reserved_split": "test",
                    "provenance_replacement_candidate": True,
                    "replacement_completion_reason": "retire_rights_blocked_active_gold_row",
                },
                {
                    "candidate_id": "expansion",
                    "doc_id": "new_doc_alias",
                    "reserved_split": "test",
                },
            ],
        )

        report = build_report(root, [CohortSpec("future", "future", future)])

        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 1)
        self.assertEqual(
            report["human_work"]["all_unique_provenance_replacement_rows"], 1
        )
        self.assertEqual(report["cohorts"][0]["provenance_replacement_rows"], 1)
        self.assertEqual(report["cohorts"][0]["replacement_split_counts"], {"test": 1})
        self.assertEqual(
            report["capacity"]["all_staged_expansion_accepted"]["explicit_test_rows"],
            2,
        )
        self.assertEqual(report["source_capacity"]["all_new_payloads"], 1)

    def test_audited_false_replacement_flag_overrides_stale_candidate_marker(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        future = root / "future.jsonl"
        write_jsonl(
            future,
            [
                {
                    "candidate_id": "stale_marker",
                    "doc_id": "new_doc",
                    "provenance_replacement": False,
                    "provenance_replacement_candidate": True,
                }
            ],
        )

        report = build_report(root, [CohortSpec("future", "future", future)])

        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 1)
        self.assertEqual(
            report["human_work"]["all_unique_provenance_replacement_rows"], 0
        )

    def test_excludes_terminal_non_capacity_rows(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        write_jsonl(
            current,
            [
                {
                    "candidate_id": "candidate_new",
                    "doc_id": "new_doc",
                    "review_status": "needs_review",
                },
                {
                    "candidate_id": "candidate_superseded",
                    "doc_id": "new_doc",
                    "review_status": "machine_superseded",
                },
                {
                    "candidate_id": "candidate_rejected",
                    "doc_id": "new_doc",
                    "review_status": "rejected",
                },
                {
                    "candidate_id": "candidate_held",
                    "doc_id": "new_doc",
                    "machine_qa_status": "machine_held",
                },
                {
                    "candidate_id": "candidate_reservoir_held",
                    "doc_id": "new_doc",
                    "review_status": "needs_review",
                    "reservoir_disposition": "held",
                    "reservoir_hold_reason": "per_doc_category_cap",
                },
            ],
        )

        report = build_report(root, [CohortSpec("current", "current", current)])

        self.assertTrue(report["capacity_input_clean"])
        self.assertEqual(report["human_work"]["all_unique_expansion_rows"], 1)
        self.assertEqual(report["cohorts"][0]["input_rows"], 5)
        self.assertEqual(report["cohorts"][0]["rows"], 1)
        self.assertEqual(report["cohorts"][0]["excluded_non_capacity_rows"], 4)
        self.assertEqual(
            report["cohorts"][0]["excluded_non_capacity_reasons"],
            {
                "machine_qa_status:machine_held": 1,
                "reservoir_disposition:held": 1,
                "review_status:machine_superseded": 1,
                "review_status:rejected": 1,
            },
        )

    def test_rejects_benchmark_facing_mojibake_from_capacity(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        write_jsonl(
            current,
            [
                {
                    "candidate_id": "candidate_new",
                    "doc_id": "new_doc",
                    "target_text": "10\u00c2\u00b5F",
                }
            ],
        )

        report = build_report(root, [CohortSpec("current", "current", current)])

        self.assertFalse(report["capacity_input_clean"])
        self.assertEqual(report["cohorts"][0]["text_encoding_errors"], 1)
        self.assertEqual(len(report["issues"]["text_encoding"]), 1)

    def test_allows_mojibake_only_in_forensic_raw_source_text(self) -> None:
        root, temp = self.build_root()
        self.addCleanup(temp.cleanup)
        current = root / "current.jsonl"
        write_jsonl(
            current,
            [
                {
                    "candidate_id": "candidate_new",
                    "doc_id": "new_doc",
                    "target_text": "10\u00b5F",
                    "source_raw_text": "10\u00c2\u00b5F",
                }
            ],
        )

        report = build_report(root, [CohortSpec("current", "current", current)])

        self.assertTrue(report["capacity_input_clean"])
        self.assertEqual(report["cohorts"][0]["text_encoding_errors"], 0)
        self.assertEqual(report["issues"]["text_encoding"], [])


if __name__ == "__main__":
    unittest.main()
