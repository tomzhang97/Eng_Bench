from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import filter_visualdiff_review_against_history as history_filter


def row(identifier: str, offset: int, fingerprint: str = "") -> dict[str, object]:
    value: dict[str, object] = {
        "task": "visualdiff",
        "pair_id": identifier,
        "image_old": "old.png",
        "image_new": "new.png",
        "page_old": 0,
        "page_new": 0,
        "bbox_old": [offset, 0, offset + 10, 10],
        "bbox_new": [offset, 0, offset + 10, 10],
        "safe_to_merge_gold": False,
    }
    if fingerprint:
        value["replacement_evidence_fingerprint"] = fingerprint
    return value


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )


class FilterVisualDiffReviewAgainstHistoryTests(unittest.TestCase):
    def test_holds_id_region_fingerprint_and_payload_overlaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.jsonl"
            input_rows = [
                row("id_overlap", 0),
                row("region_new_id", 10),
                row("fingerprint_new_id", 20, "visualdiff:sha256:same"),
                row("payload_overlap", 30),
                row("clean", 40, "visualdiff:sha256:clean"),
            ]
            write_jsonl(input_path, input_rows)
            write_jsonl(
                root / "visualdiff/annotations/visualdiff_pairs.jsonl",
                [row("id_overlap", 100)],
            )
            write_jsonl(
                root / "derived/review_queues/existing.jsonl",
                [row("different_region_id", 10)],
            )
            write_jsonl(
                root / "derived/review_packs/pack/manifest.jsonl",
                [row("different_fingerprint_id", 200, "visualdiff:sha256:same")],
            )
            payload = {
                "auditors": [
                    {"rows": [row("payload_overlap", 300)]},
                ]
            }
            payload_path = root / "derived/quality/human_audit_payload_test.json"
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_text(json.dumps(payload), encoding="utf-8")

            index, history_report = history_filter.load_history(root, input_path)
            passing, held, report = history_filter.filter_rows(input_rows, index)

            self.assertEqual([], history_report["history_parse_issues"])
            self.assertEqual(["clean"], [history_filter.row_identifier(item) for item in passing])
            self.assertEqual(4, len(held))
            self.assertEqual(1, report["passing_rows"])
            self.assertEqual(4, report["held_rows"])
            reasons = {
                history_filter.row_identifier(item): item["review_history_filter"]["reasons"]
                for item in held
            }
            self.assertIn("record_id", reasons["id_overlap"])
            self.assertIn("exact_region", reasons["region_new_id"])
            self.assertIn("evidence_fingerprint", reasons["fingerprint_new_id"])
            self.assertIn("record_id", reasons["payload_overlap"])

    def test_input_file_is_not_indexed_as_its_own_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "visualdiff/annotations/current.jsonl"
            input_rows = [row("clean", 0)]
            write_jsonl(input_path, input_rows)
            write_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl", [])

            index, history_report = history_filter.load_history(root, input_path)
            passing, held, _ = history_filter.filter_rows(input_rows, index)

            self.assertEqual([], history_report["history_parse_issues"])
            self.assertEqual(1, len(passing))
            self.assertEqual([], held)

    def test_explicit_mirrors_can_be_excluded_without_hiding_active_gold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "derived/review_queues/reserved.jsonl"
            mirror_queue = root / "derived/review_queues/provisional.jsonl"
            mirror_pack = root / "derived/review_packs/pack/manifest.jsonl"
            input_rows = [row("clean", 0), row("active_overlap", 10)]
            write_jsonl(input_path, input_rows)
            write_jsonl(mirror_queue, input_rows)
            write_jsonl(mirror_pack, input_rows)
            write_jsonl(
                root / "visualdiff/annotations/visualdiff_pairs.jsonl",
                [row("active_overlap", 100)],
            )

            index, history_report = history_filter.load_history(
                root,
                input_path,
                excluded_paths=[mirror_queue, mirror_pack],
            )
            passing, held, _ = history_filter.filter_rows(input_rows, index)

            self.assertEqual(["clean"], [history_filter.row_identifier(item) for item in passing])
            self.assertEqual(
                ["active_overlap"],
                [history_filter.row_identifier(item) for item in held],
            )
            self.assertEqual(
                {
                    "derived/review_packs/pack/manifest.jsonl",
                    "derived/review_queues/provisional.jsonl",
                    "derived/review_queues/reserved.jsonl",
                },
                set(history_report["excluded_history_files"]),
            )


if __name__ == "__main__":
    unittest.main()
