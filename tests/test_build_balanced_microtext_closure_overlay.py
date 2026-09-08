from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.build_balanced_microtext_closure_overlay import build_overlay


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class BalancedMicrotextClosureOverlayTest(unittest.TestCase):
    def make_fixture(self, root: Path, *, bad_payload_hash: bool = False) -> dict[str, Path]:
        payload = root / "source.pdf"
        payload.write_bytes(b"verified source")
        payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
        image_path = root / "page_000.png"
        Image.new("RGB", (200, 100), "white").save(image_path)
        paths = {
            name: root / name
            for name in (
                "active.jsonl",
                "assignment.jsonl",
                "eligible.jsonl",
                "future.jsonl",
                "inventory.csv",
                "manifest.jsonl",
            )
        }
        write_jsonl(
            paths["active.jsonl"],
            [
                {
                    "id": "q-active",
                    "task": "microtext",
                    "answer": "10",
                    "images": [image_path.name],
                    "evidence": [{"bbox": [1, 1, 20, 20]}],
                    "metadata": {
                        "item_id": "active",
                        "doc_id": "doc",
                        "category": "dimension_value",
                    },
                }
            ],
        )
        write_jsonl(paths["assignment.jsonl"], [])
        write_jsonl(paths["eligible.jsonl"], [])
        future = [
            {
                "candidate_id": "candidate-1",
                "task": "microtext",
                "doc_id": "doc",
                "category": "dimension_value",
                "page_index": 0,
                "bbox": [30, 10, 60, 30],
                "image_path": image_path.name,
                "proposed_text": "25 mm",
                "reserved_split": "test",
                "machine_qa_status": "selected_for_human_review",
                "source_public_status": "public_domain",
                "safe_to_merge_gold": False,
            },
            {
                "candidate_id": "candidate-2",
                "task": "microtext",
                "doc_id": "doc",
                "category": "dimension_value",
                "page_index": 0,
                "bbox": [70, 10, 100, 30],
                "image_path": image_path.name,
                "proposed_text": "50 mm",
                "reserved_split": "train",
                "machine_qa_status": "selected_for_human_review",
                "source_public_status": "public_domain",
                "safe_to_merge_gold": False,
            },
        ]
        write_jsonl(paths["future.jsonl"], future)
        with paths["inventory.csv"].open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["doc_id", "public_status", "source_path", "duplicate_payload_alias"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "doc_id": "doc",
                    "public_status": "public_domain",
                    "source_path": payload.name,
                    "duplicate_payload_alias": "False",
                }
            )
        write_jsonl(
            paths["manifest.jsonl"],
            [
                {
                    "type": "doc",
                    "doc_id": "doc",
                    "task": "microtext",
                    "path": payload.name,
                    "sha256": "0" * 64 if bad_payload_hash else payload_sha,
                    "public_status": "public_domain",
                }
            ],
        )
        return paths

    def test_selects_diverse_review_overlay_and_backfills_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.make_fixture(root)
            selected, holds, report = build_overlay(
                root,
                paths["active.jsonl"],
                paths["assignment.jsonl"],
                paths["eligible.jsonl"],
                paths["future.jsonl"],
                paths["inventory.csv"],
                paths["manifest.jsonl"],
                date_label="fixture",
                min_acceptance_rate=0.5,
                category_minimums={"dimension_value": 2},
            )

            self.assertEqual(2, len(selected))
            self.assertEqual([], holds)
            self.assertEqual("test", selected[0]["reserved_split"])
            self.assertTrue(all(row["closure_overlay_only"] for row in selected))
            self.assertTrue(all(not row["safe_to_merge_gold"] for row in selected))
            self.assertTrue(all(row["source_payload_sha256"] for row in selected))
            self.assertTrue(
                all(row["source_payload_sha256_backfill_source"] == "manifest.jsonl" for row in selected)
            )
            details = report["category_projection"]["dimension_value"]
            self.assertEqual(1, details["shortfall_before_overlay"])
            self.assertEqual(2, details["selected_overlay_rows"])
            self.assertEqual(0, details["shortfall_after_acceptance_floor"])
            self.assertFalse(report["policy"]["active_gold_modified"])

    def test_holds_rows_when_local_payload_does_not_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.make_fixture(root, bad_payload_hash=True)
            selected, holds, report = build_overlay(
                root,
                paths["active.jsonl"],
                paths["assignment.jsonl"],
                paths["eligible.jsonl"],
                paths["future.jsonl"],
                paths["inventory.csv"],
                paths["manifest.jsonl"],
                date_label="fixture",
                category_minimums={"dimension_value": 2},
            )

            self.assertEqual([], selected)
            self.assertEqual(2, len(holds))
            self.assertEqual(2, report["exclusion_reasons"]["source_payload_sha256_mismatch"])
            self.assertTrue(
                all("source_payload_sha256_mismatch" in row["closure_overlay_hold_reasons"] for row in holds)
            )

    def test_accepts_separately_auditable_supplemental_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.make_fixture(root)
            supplemental = root / "supplemental.jsonl"
            write_jsonl(
                supplemental,
                [
                    {
                        "candidate_id": "supplemental-tolerance",
                        "task": "microtext",
                        "doc_id": "doc",
                        "category": "tolerance_value",
                        "page_index": 0,
                        "bbox": [110, 10, 150, 30],
                        "image_path": "page_000.png",
                        "proposed_text": "+/-0.01",
                        "reserved_split": "train",
                        "machine_qa_status": "selected_for_human_review",
                        "source_public_status": "public_domain",
                        "safe_to_merge_gold": False,
                    }
                ],
            )
            selected, holds, report = build_overlay(
                root,
                paths["active.jsonl"],
                paths["assignment.jsonl"],
                paths["eligible.jsonl"],
                paths["future.jsonl"],
                paths["inventory.csv"],
                paths["manifest.jsonl"],
                date_label="fixture",
                min_acceptance_rate=1.0,
                category_minimums={"dimension_value": 2, "tolerance_value": 1},
                supplemental_future_paths=[supplemental],
            )

            self.assertEqual(2, len(selected))
            self.assertEqual([], holds)
            self.assertEqual(
                {"dimension_value": 1, "tolerance_value": 1},
                report["counts"]["selected_categories"],
            )
            self.assertEqual(
                hashlib.sha256(supplemental.read_bytes()).hexdigest(),
                report["inputs"]["supplemental_future_1"]["sha256"],
            )

    def test_test_only_nonpin_mode_excludes_packets_replacements_and_other_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.make_fixture(root)
            future = [json.loads(line) for line in paths["future.jsonl"].read_text().splitlines()]
            future.extend(
                [
                    {
                        **future[0],
                        "candidate_id": "candidate-replacement",
                        "category": "room_label",
                        "bbox": [105, 10, 130, 30],
                        "proposed_text": "ROOM 101",
                        "provenance_replacement": True,
                    },
                    {
                        **future[0],
                        "candidate_id": "candidate-component",
                        "category": "component_value",
                        "bbox": [135, 10, 160, 30],
                        "proposed_text": "10k",
                    },
                    {
                        **future[0],
                        "candidate_id": "candidate-pin",
                        "category": "pin_label",
                        "bbox": [165, 10, 190, 30],
                        "proposed_text": "GPIO1",
                    },
                ]
            )
            write_jsonl(paths["future.jsonl"], future)
            packet_dir = root / "packet"
            packet_dir.mkdir()
            with (packet_dir / "checklist.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["candidate_id", "doc_id", "page_index", "bbox"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "candidate-1",
                        "doc_id": "doc",
                        "page_index": 0,
                        "bbox": json.dumps([30, 10, 60, 30]),
                    }
                )
            packet_index = root / "packet_index.json"
            packet_index.write_text(
                json.dumps(
                    {
                        "packs": [
                            {
                                "folder_path": "packet",
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            selected, holds, report = build_overlay(
                root,
                paths["active.jsonl"],
                paths["assignment.jsonl"],
                paths["eligible.jsonl"],
                paths["future.jsonl"],
                paths["inventory.csv"],
                paths["manifest.jsonl"],
                date_label="fixture",
                min_acceptance_rate=1.0,
                category_minimums={
                    "pin_label": 0,
                    "dimension_value": 2,
                    "room_label": 1,
                    "component_value": 1,
                },
                packet_index_path=packet_index,
                required_splits={"test"},
                exclude_provenance_replacements=True,
                selection_mode="all_known_nonpin",
            )

            self.assertEqual(["candidate-component"], [row["candidate_id"] for row in selected])
            hold_reasons = {
                reason
                for row in holds
                for reason in row["closure_overlay_hold_reasons"]
            }
            self.assertIn("candidate_id_in_active_review_packet", hold_reasons)
            self.assertIn("provenance_replacement_not_expansion_capacity", hold_reasons)
            self.assertEqual(1, report["counts"]["prefilter_reasons"]["required_split_mismatch"])
            self.assertEqual(1, report["counts"]["ignored_non_target_categories"]["pin_label"])
            self.assertEqual(1, report["counts"]["selected_known_nonpin_rows"])
            self.assertEqual(0, report["counts"]["selected_unknown_rows"])
            self.assertTrue(report["balance_projection"]["improves_pin_share_at_full_acceptance"])
            self.assertIn(
                "additional_review_rows_at_modeled_rate_to_limit",
                report["balance_projection"],
            )

    def test_packet_scan_tolerates_legacy_encoded_note_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_dir = root / "packet"
            packet_dir.mkdir()
            (packet_dir / "checklist.csv").write_bytes(
                b"candidate_id,notes\ncandidate-legacy,legacy-\xbf-note\n"
            )
            packet_index = root / "packet_index.json"
            packet_index.write_text(
                json.dumps(
                    {
                        "packs": [
                            {
                                "folder_path": "packet",
                                "ready_to_send": True,
                                "mergeable_rows": 0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            from tools.build_balanced_microtext_closure_overlay import load_packet_exclusions

            ids, _geometry, summary = load_packet_exclusions(root, packet_index)
            self.assertEqual({"candidate-legacy"}, ids)
            self.assertEqual(1, summary["packet_rows_scanned"])

    def test_authoritative_split_plan_holds_stale_row_reservations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.make_fixture(root)
            split_plan = root / "split_plan.json"
            split_plan.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "reservations": [
                            {"task": "microtext", "unit_id": "doc", "split": "train"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            selected, holds, report = build_overlay(
                root,
                paths["active.jsonl"],
                paths["assignment.jsonl"],
                paths["eligible.jsonl"],
                paths["future.jsonl"],
                paths["inventory.csv"],
                paths["manifest.jsonl"],
                date_label="fixture",
                category_minimums={"dimension_value": 2},
                split_plan_path=split_plan,
                required_splits={"test"},
                selection_mode="all_known_nonpin",
            )

            self.assertEqual([], selected)
            self.assertEqual(1, len(holds))
            self.assertIn("split_plan_mismatch:train", holds[0]["closure_overlay_hold_reasons"])
            self.assertEqual(1, report["exclusion_reasons"]["split_plan_mismatch:train"])


if __name__ == "__main__":
    unittest.main()
