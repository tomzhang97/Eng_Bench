from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image

from tools import repair_primary_pool_payload_aliases as repair


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class RepairPrimaryPoolPayloadAliasesTest(unittest.TestCase):
    def add_page(self, root: Path, doc_id: str, size: tuple[int, int]) -> str:
        path = root / "pages" / doc_id / "page.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "white").save(path)
        return path.relative_to(root).as_posix()

    def test_replaces_active_and_within_pool_aliases_while_preserving_auditor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_paths = {
                doc_id: self.add_page(root, doc_id, size)
                for doc_id, size in (
                    ("canonical", (400, 200)),
                    ("alias_one", (200, 100)),
                    ("alias_two", (800, 400)),
                    ("clean_one", (400, 200)),
                    ("clean_two", (400, 200)),
                )
            }
            write_jsonl(
                root / "microtext" / "annotations" / "microtext_items.jsonl",
                [
                    {
                        "item_id": "active",
                        "doc_id": "canonical",
                        "page_index": 0,
                        "bbox": [40, 20, 80, 40],
                        "image_path": image_paths["canonical"],
                    }
                ],
            )
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            pool = [
                {
                    "primary_pool_index": 1,
                    "candidate_id": "duplicates_active",
                    "doc_id": "alias_one",
                    "page_index": 0,
                    "bbox": [20, 10, 40, 20],
                    "image_path": image_paths["alias_one"],
                    "category": "instrument_tag",
                    "reserved_split": "train",
                },
                {
                    "primary_pool_index": 2,
                    "candidate_id": "ordinary_duplicate",
                    "doc_id": "canonical",
                    "page_index": 0,
                    "bbox": [200, 80, 280, 120],
                    "image_path": image_paths["canonical"],
                    "category": "instrument_tag",
                    "reserved_split": "train",
                },
                {
                    "primary_pool_index": 3,
                    "candidate_id": "auditor_duplicate",
                    "doc_id": "alias_two",
                    "page_index": 0,
                    "bbox": [400, 160, 560, 240],
                    "image_path": image_paths["alias_two"],
                    "category": "instrument_tag",
                    "reserved_split": "train",
                },
            ]
            candidates = [
                {
                    "candidate_id": "replacement_one",
                    "doc_id": "clean_one",
                    "page_index": 0,
                    "bbox": [10, 10, 40, 40],
                    "image_path": image_paths["clean_one"],
                    "category": "instrument_tag",
                    "reserved_split": "train",
                    "review_status": "needs_review",
                },
                {
                    "candidate_id": "replacement_two",
                    "doc_id": "clean_two",
                    "page_index": 0,
                    "bbox": [50, 50, 80, 80],
                    "image_path": image_paths["clean_two"],
                    "category": "instrument_tag",
                    "reserved_split": "train",
                    "review_status": "needs_review",
                },
            ]
            alias_map = {
                "canonical": "canonical",
                "alias_one": "canonical",
                "alias_two": "canonical",
            }

            repaired, report = repair.repair_pool(
                root,
                pool_rows=pool,
                candidate_rows=candidates,
                alias_map=alias_map,
                auditor_ids={"auditor_duplicate"},
            )

            self.assertTrue(report["valid"])
            self.assertEqual(report["replaced_rows"], 2)
            self.assertEqual(report["auditor_overlap_preserved"], 1)
            ids = {row["candidate_id"] for row in repaired}
            self.assertIn("auditor_duplicate", ids)
            self.assertNotIn("ordinary_duplicate", ids)
            self.assertNotIn("duplicates_active", ids)
            self.assertEqual(
                Counter((row["category"], row["reserved_split"]) for row in repaired),
                Counter({("instrument_tag", "train"): 3}),
            )


if __name__ == "__main__":
    unittest.main()
