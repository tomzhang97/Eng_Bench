import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_visualdiff_staged_readiness import build_report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def candidate(root: Path, family: str, suffix: str, **updates) -> dict:
    old_path = root / f"old_{family}_{suffix}.png"
    new_path = root / f"new_{family}_{suffix}.png"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    row = {
        "pair_id": f"vdiff__{family}__v1__to__v2__{suffix}",
        "project_id": f"vdiff__{family}__v1__to__v2",
        "image_old": old_path.name,
        "image_new": new_path.name,
        "bbox_old": [0, 0, 10, 10],
        "bbox_new": [1, 1, 11, 11],
        "reserved_split": "test",
        "source_rights_check": "release_safe_status",
        "machine_qa_status": "visualdiff_queue_audit_pass",
        "human_review_status": "unassigned",
        "review_status": "needs_review",
        "old_text": "GPIO1",
        "new_text": "GPIO2",
        "safe_to_merge_gold": False,
    }
    row.update(updates)
    return row


class VisualDiffStagedReadinessTest(unittest.TestCase):
    def test_excludes_active_and_paused_and_separates_human_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [candidate(root, "active", "000")])
            paused = root / "paused.jsonl"
            write_jsonl(paused, [candidate(root, "paused", "000")])
            capacity = root / "capacity.jsonl"
            rows = [
                candidate(root, "active", "001"),
                candidate(root, "paused", "001"),
                candidate(root, "fresh_a", "000"),
                candidate(root, "fresh_b", "000", human_review_status="edit"),
                candidate(root, "broken", "000", source_rights_check=""),
            ]
            write_jsonl(capacity, rows)

            report, selected = build_report(
                root, capacity, paused, target_families=3, rows_per_family=2
            )

            self.assertEqual(report["totals"]["active_gold_families"], 1)
            self.assertEqual(report["totals"]["paused_assignment_families"], 1)
            self.assertEqual(report["totals"]["prior_human_decision_rows"], 1)
            self.assertEqual(report["selected_family_ids"], ["vdiff__fresh_a__v1"])
            self.assertEqual(len(selected), 1)
            self.assertFalse(selected[0]["safe_to_merge_gold"])

    def test_caps_rows_per_family_and_requires_complete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [])
            capacity = root / "capacity.jsonl"
            rows = [candidate(root, "family", f"{index:03d}") for index in range(5)]
            rows[0]["confidence"] = "machine_candidate"
            rows.append(candidate(root, "missing", "000", image_new="absent.png"))
            write_jsonl(capacity, rows)

            report, selected = build_report(
                root, capacity, None, target_families=1, rows_per_family=3
            )

            self.assertEqual(report["totals"]["selected_family_seeds"], 1)
            self.assertEqual(len(selected), 3)
            missing = next(row for row in report["families"] if "missing" in row["family_id"])
            self.assertFalse(missing["eligible_for_future_packet"])
            self.assertEqual(missing["readiness_reason_counts"]["missing_new_image"], 1)

    def test_prefers_semantic_text_change_over_blank_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [])
            capacity = root / "capacity.jsonl"
            blank = candidate(
                root,
                "family",
                "000",
                old_text="",
                new_text="",
                gap_mining_metrics={"normalized_changed_pixel_ratio_gt16": 0.9},
            )
            semantic = candidate(
                root,
                "family",
                "001",
                old_text="GPIO1",
                new_text="GPIO2",
                gap_mining_metrics={"normalized_changed_pixel_ratio_gt16": 0.1},
            )
            write_jsonl(capacity, [blank, semantic])

            _report, selected = build_report(
                root, capacity, None, target_families=1, rows_per_family=1
            )

            self.assertEqual(selected[0]["pair_id"], semantic["pair_id"])

    def test_described_geometry_change_is_seed_ready_without_ocr_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [])
            capacity = root / "capacity.jsonl"
            geometry = candidate(
                root,
                "family",
                "000",
                old_text="",
                new_text="",
                change_type="layout",
                description="The stepped edge changes to a straight edge.",
            )
            write_jsonl(capacity, [geometry])

            report, selected = build_report(
                root, capacity, None, target_families=1, rows_per_family=1
            )

            self.assertEqual(report["totals"]["seed_review_ready_rows"], 1)
            self.assertEqual([geometry["pair_id"]], [row["pair_id"] for row in selected])

    def test_prefers_related_atomic_change_over_unrelated_text_jump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [])
            capacity = root / "capacity.jsonl"
            unrelated = candidate(
                root,
                "family",
                "000",
                old_text="GPIO2 HS2_DATA0 GPIO3",
                new_text="Battery Measurement",
                gap_mining_metrics={
                    "normalized_changed_pixel_ratio_gt16": 0.9,
                    "bbox_area_ratio": 0.02,
                },
            )
            related = candidate(
                root,
                "family",
                "001",
                old_text="SPI_MOSI/SD_CMD",
                new_text="SPI_COPI/SD_CMD",
                gap_mining_metrics={
                    "normalized_changed_pixel_ratio_gt16": 0.1,
                    "bbox_area_ratio": 0.0001,
                },
            )
            write_jsonl(capacity, [unrelated, related])

            _report, selected = build_report(
                root, capacity, None, target_families=1, rows_per_family=1
            )

            self.assertEqual(selected[0]["pair_id"], related["pair_id"])

    def test_seed_screen_excludes_ambiguous_and_revision_metadata_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [])
            capacity = root / "capacity.jsonl"
            rows = [
                candidate(root, "family", "000", old_text="", new_text=""),
                candidate(root, "family", "001", old_text="GND", new_text="GND"),
                candidate(
                    root,
                    "family",
                    "002",
                    old_text="board_rev_a-1234567890ab",
                    new_text="board_rev_b-abcdef123456",
                ),
                candidate(root, "family", "003", old_text="", new_text="Patch"),
                candidate(root, "family", "004", old_text="GND GND", new_text="GNDGND"),
                candidate(root, "family", "005", old_text="GPIO1", new_text="GPIO2"),
            ]
            write_jsonl(capacity, rows)

            report, selected = build_report(
                root, capacity, None, target_families=1, rows_per_family=5
            )

            self.assertEqual(report["totals"]["fresh_review_ready_rows"], 6)
            self.assertEqual(report["totals"]["seed_review_ready_rows"], 1)
            self.assertEqual(report["totals"]["seed_excluded_rows"], 5)
            self.assertEqual([row["pair_id"] for row in selected], [rows[5]["pair_id"]])

    def test_excludes_machine_failed_family_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            write_jsonl(active, [])
            capacity = root / "capacity.jsonl"
            rows = [candidate(root, "bad", "000"), candidate(root, "good", "000")]
            write_jsonl(capacity, rows)

            report, selected = build_report(
                root,
                capacity,
                None,
                target_families=1,
                family_exclusions={"vdiff__bad__v1": "page_alignment_failure"},
            )

            self.assertEqual(report["totals"]["machine_excluded_families"], 1)
            self.assertEqual(report["selected_family_ids"], ["vdiff__good__v1"])
            self.assertEqual(selected[0]["pair_id"], rows[1]["pair_id"])


if __name__ == "__main__":
    unittest.main()
