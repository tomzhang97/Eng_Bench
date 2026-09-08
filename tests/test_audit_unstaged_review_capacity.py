from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from audit_unstaged_review_capacity import build_report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_inventory(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["doc_id", "path", "source_url", "public_status", "domain", "sha256"],
        )
        writer.writeheader()
        writer.writerows(rows)


def source(root: Path, doc_id: str, status: str = "public_domain_us_federal_candidate") -> dict:
    path = root / f"{doc_id}.pdf"
    path.write_bytes(doc_id.encode())
    return {
        "doc_id": doc_id,
        "path": path.name,
        "source_url": f"https://example.test/{doc_id}.pdf",
        "public_status": status,
        "domain": "civil",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def candidate(root: Path, doc_id: str, suffix: str, bbox: list[int]) -> dict:
    image = root / f"{doc_id}.png"
    image.write_bytes(b"png")
    return {
        "candidate_id": f"candidate_{suffix}",
        "doc_id": doc_id,
        "page_index": 0,
        "bbox": bbox,
        "image_path": image.name,
        "category": "dimension_value",
        "proposed_text": "12 in",
        "review_status": "needs_review",
        "version_id": "current",
        "safe_to_merge_gold": False,
    }


class UnstagedReviewCapacityTest(unittest.TestCase):
    def setUpFixture(self, root: Path) -> dict[str, dict]:
        good = source(root, "good")
        blocked = source(root, "blocked", "restricted_reference_only")
        write_inventory(root / "SOURCE_INVENTORY.csv", [good, blocked])
        write_jsonl(
            root / "manifest.jsonl",
            [
                {"type": "doc", "doc_id": value["doc_id"], "path": value["path"], "source_url": value["source_url"], "public_status": value["public_status"], "sha256": value["sha256"], "task": "microtext"}
                for value in (good, blocked)
            ],
        )
        return {"good": good, "blocked": blocked}

    def test_filters_reference_near_terminal_evidence_and_rights_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.setUpFixture(root)
            active = candidate(root, "good", "active", [0, 0, 20, 20])
            active["item_id"] = active.pop("candidate_id")
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [active])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            write_jsonl(current, [candidate(root, "good", "current", [100, 100, 120, 120])])
            write_jsonl(future, [candidate(root, "good", "future", [200, 200, 220, 220])])
            history = root / "history_decisions.jsonl"
            write_jsonl(
                history,
                [
                    {
                        **candidate(root, "good", "prior_terminal", [700, 700, 720, 720]),
                        "review_status": "rejected",
                    }
                ],
            )
            review = root / "review.jsonl"
            rows = [
                candidate(root, "good", "exact", [200, 200, 220, 220]),
                candidate(root, "good", "near", [201, 201, 221, 221]),
                candidate(root, "good", "fresh", [300, 300, 320, 320]),
                candidate(root, "good", "duplicate_alias", [300, 300, 320, 320]),
                candidate(root, "blocked", "blocked", [400, 400, 420, 420]),
                candidate(root, "good", "terminal", [500, 500, 520, 520]),
                candidate(root, "good", "missing", [600, 600, 620, 620]),
                candidate(root, "good", "prior_terminal", [700, 700, 720, 720]),
                candidate(root, "good", "terminal_region_alias", [700, 700, 720, 720]),
            ]
            rows[5]["review_status"] = "rejected"
            rows[6]["image_path"] = "missing.png"
            write_jsonl(review, rows)

            report, samples, ranked = build_report(
                root,
                review_paths=[history, review],
                current_paths=[current],
                future_paths=[future],
                date_label="fixture",
                sample_rows_per_doc=5,
            )

            self.assertEqual(report["totals"]["net_new_rows"], 1)
            self.assertEqual(report["totals"]["duplicate_representations"], 1)
            self.assertEqual(report["totals"]["non_paper_ready_rows"], 1)
            self.assertEqual(report["totals"]["missing_or_invalid_evidence_rows"], 1)
            self.assertEqual(report["totals"]["not_open_or_terminal_rows"], 2)
            self.assertEqual(report["totals"]["exact_reference_overlap_rows"], 1)
            self.assertEqual(report["totals"]["near_future_capacity_rows"], 1)
            self.assertEqual(report["totals"]["terminal_review_overlap_rows"], 1)
            self.assertEqual(report["totals"]["terminal_review_region_overlap_rows"], 1)
            self.assertEqual(
                report["totals"]["machine_prequalified_needs_visual_qa_rows"], 1
            )
            self.assertEqual(len(samples), 1)
            self.assertEqual(len(ranked), 1)
            self.assertFalse(samples[0]["safe_to_merge_gold"])

    def test_near_reference_region_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.setUpFixture(root)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            write_jsonl(current, [])
            write_jsonl(future, [candidate(root, "good", "future", [100, 100, 120, 120])])
            review = root / "review.jsonl"
            write_jsonl(review, [candidate(root, "good", "near", [101, 101, 121, 121])])

            report, samples, ranked = build_report(
                root,
                review_paths=[review],
                current_paths=[current],
                future_paths=[future],
                date_label="fixture",
            )

            self.assertEqual(report["totals"].get("near_future_capacity_rows"), 1)
            self.assertEqual(report["totals"]["net_new_rows"], 0)
            self.assertEqual(samples, [])
            self.assertEqual(ranked, [])

    def test_machine_reserved_rows_are_excluded_from_unstaged_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.setUpFixture(root)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            reserved = root / "machine_reserved.jsonl"
            write_jsonl(current, [])
            write_jsonl(future, [])
            write_jsonl(
                reserved,
                [candidate(root, "good", "reserved", [100, 100, 120, 120])],
            )
            review = root / "review.jsonl"
            write_jsonl(
                review,
                [
                    candidate(root, "good", "reserved", [100, 100, 120, 120]),
                    candidate(root, "good", "near_reserved", [101, 101, 121, 121]),
                    candidate(root, "good", "fresh", [200, 200, 220, 220]),
                ],
            )

            report, samples, ranked = build_report(
                root,
                review_paths=[review],
                current_paths=[current],
                future_paths=[future],
                reserved_paths=[reserved],
                date_label="fixture",
            )

            self.assertEqual(report["totals"]["exact_reference_overlap_rows"], 1)
            self.assertEqual(
                report["totals"]["near_reserved_machine_capacity_rows"], 1
            )
            self.assertEqual(report["totals"]["net_new_rows"], 1)
            self.assertEqual(report["reserved_paths"], ["machine_reserved.jsonl"])
            self.assertEqual(len(samples), 1)
            self.assertEqual(len(ranked), 1)
            self.assertEqual(ranked[0]["candidate_id"], "candidate_fresh")

    def test_textlayer_coordinate_artifact_is_not_machine_prequalified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.setUpFixture(root)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            write_jsonl(current, [])
            write_jsonl(future, [])
            row = candidate(root, "good", "svg_coordinate", [10, 10, 30, 30])
            row["proposed_text"] = '1.587500"'
            row["raw_text"] = '<tspan x="0" y="1.587500">BMEx80 Digital Sensor</tspan>'
            review = root / "review.jsonl"
            write_jsonl(review, [row])

            report, samples, ranked = build_report(
                root,
                review_paths=[review],
                current_paths=[current],
                future_paths=[future],
                date_label="fixture",
            )

            self.assertEqual(report["totals"]["net_new_rows"], 1)
            self.assertEqual(report["totals"]["machine_semantic_hold_rows"], 1)
            self.assertEqual(len(ranked), 1)
            self.assertEqual(samples[0]["unstaged_capacity_tier_reasons"], [
                "textlayer_target_not_in_visible_text"
            ])

    def test_uppercase_textlayer_markup_is_also_semantically_held(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.setUpFixture(root)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            write_jsonl(current, [])
            write_jsonl(future, [])
            row = candidate(root, "good", "svg_coordinate_upper", [10, 10, 30, 30])
            row["proposed_text"] = '1.587500"'
            row["raw_text"] = '<TSPAN x="0" y="1.587500">BMEx80 Digital Sensor</TSPAN>'
            review = root / "review.jsonl"
            write_jsonl(review, [row])

            report, samples, ranked = build_report(
                root,
                review_paths=[review],
                current_paths=[current],
                future_paths=[future],
                date_label="fixture",
            )

            self.assertEqual(report["totals"]["machine_semantic_hold_rows"], 1)
            self.assertEqual(len(ranked), 1)
            self.assertEqual(samples[0]["unstaged_capacity_tier_reasons"], [
                "textlayer_target_not_in_visible_text"
            ])

    def test_derived_terminal_hold_suppresses_original_open_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.setUpFixture(root)
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            current = root / "current.jsonl"
            future = root / "future.jsonl"
            write_jsonl(current, [])
            write_jsonl(future, [])
            held = candidate(root, "good", "held", [10, 10, 30, 30])
            open_alias = dict(held)
            open_alias["candidate_id"] = "candidate_open_alias"
            fresh = candidate(root, "good", "fresh", [100, 100, 120, 120])
            review = root / "review.jsonl"
            write_jsonl(review, [open_alias, fresh])
            held["review_status"] = "machine_held"
            held["machine_qa_status"] = "machine_held"
            terminal = (
                root
                / "derived"
                / "review_queues"
                / "wave_visual_held.jsonl"
            )
            write_jsonl(terminal, [held])

            report, samples, ranked = build_report(
                root,
                review_paths=[review],
                current_paths=[current],
                future_paths=[future],
                date_label="fixture",
            )

            self.assertEqual(report["totals"]["net_new_rows"], 1)
            self.assertEqual(
                report["totals"]["terminal_review_region_overlap_rows"], 1
            )
            self.assertEqual(len(samples), 1)
            self.assertEqual(len(ranked), 1)
            self.assertEqual(ranked[0]["candidate_id"], "candidate_fresh")
            self.assertEqual(
                report["terminal_review_files"],
                ["derived/review_queues/wave_visual_held.jsonl"],
            )


if __name__ == "__main__":
    unittest.main()
