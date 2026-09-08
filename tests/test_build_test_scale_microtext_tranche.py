from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import build_test_scale_microtext_tranche as tranche


class TestBuildTestScaleMicrotextTranche(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "splits").mkdir()
        (self.root / "images").mkdir()
        for split, docs in {
            "train": ["train_doc"],
            "dev": ["dev_doc"],
            "test": ["test_doc"],
        }.items():
            (self.root / "splits" / f"microtext_{split}.txt").write_text(
                "\n".join(docs) + "\n", encoding="utf-8"
            )
        for name in ("train", "dev", "test", "new"):
            (self.root / "images" / f"{name}.png").write_bytes(b"not-decoded")
        (self.root / "manifest.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": f"{name}_doc",
                        "source_candidate_id": f"candidate_{name}",
                    }
                )
                + "\n"
                for name in ("train", "dev", "test", "new")
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def row(
        self,
        row_id: str,
        source_doc: str,
        category: str = "dimension_value",
        *,
        page: int = 0,
        text: str = "10 mm",
    ) -> dict:
        return {
            "candidate_id": row_id,
            "doc_id": source_doc,
            "category": category,
            "page_index": page,
            "bbox": [10, 10, 40, 30],
            "image_path": f"images/{source_doc.split('_')[0]}.png",
            "proposed_text": text,
            "unstaged_capacity_tier": "machine_prequalified_needs_visual_qa",
            "unstaged_capacity_source_docs": [source_doc],
            "machine_qa_status": "selected_for_human_review",
            "safe_to_merge_gold": False,
        }

    def write_input(self, rows: list[dict]) -> Path:
        path = self.root / "input.jsonl"
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        return path

    def test_selects_only_test_or_unseen_nonpin_rows(self) -> None:
        rows = [
            self.row("test", "test_doc"),
            self.row("new", "new_doc", "instrument_tag", text="PT-101"),
            self.row("train", "train_doc"),
            self.row("dev", "dev_doc"),
            self.row("pin", "new_doc", "pin_label", text="GPIO1"),
        ]
        selected, holds, report = tranche.build_tranche(
            self.root,
            self.write_input(rows),
            row_target=10,
            max_rows_per_doc=10,
            max_rows_per_page=10,
            max_same_text_per_doc=3,
            max_same_text_global=10,
            date_label="fixture",
        )
        self.assertEqual({"test", "new"}, {row["candidate_id"] for row in selected})
        self.assertTrue(all(row["split"] == "test" for row in selected))
        self.assertTrue(all(row["reserved_split"] == "test" for row in selected))
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in selected))
        reasons = {reason for row in holds for reason in row["test_scale_hold_reasons"]}
        self.assertIn("source_locked_train", reasons)
        self.assertIn("source_locked_dev", reasons)
        self.assertIn("not_supported_nonpin_category", reasons)
        self.assertEqual(2, report["counts"]["selected_rows"])

    def test_enforces_page_and_text_diversity_caps(self) -> None:
        rows = [
            self.row(f"row-{index}", "new_doc", page=0, text="10 mm")
            for index in range(5)
        ]
        selected, holds, report = tranche.build_tranche(
            self.root,
            self.write_input(rows),
            row_target=5,
            max_rows_per_doc=5,
            max_rows_per_page=2,
            max_same_text_per_doc=1,
            max_same_text_global=5,
            date_label="fixture",
        )
        self.assertEqual(1, len(selected))
        self.assertEqual(4, len(holds))
        self.assertGreaterEqual(
            report["counts"]["selection_cap_rejections"]["same_text_per_document_cap"],
            1,
        )

    def test_rejects_conflicting_active_split_locks(self) -> None:
        (self.root / "splits" / "microtext_dev.txt").write_text(
            "dev_doc\ntest_doc\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "multiple splits"):
            tranche.build_tranche(
                self.root,
                self.write_input([self.row("test", "test_doc")]),
                row_target=1,
                max_rows_per_doc=1,
                max_rows_per_page=1,
                max_same_text_per_doc=1,
                max_same_text_global=1,
                date_label="fixture",
            )

    def test_source_identity_alias_inherits_active_split_lock(self) -> None:
        manifest_rows = [
            {
                "type": "doc",
                "doc_id": "train_doc",
                "source_candidate_id": "shared_family",
            },
            {
                "type": "doc",
                "doc_id": "new_doc",
                "source_candidate_id": "shared_family",
            },
            {"type": "doc", "doc_id": "dev_doc", "source_candidate_id": "dev"},
            {"type": "doc", "doc_id": "test_doc", "source_candidate_id": "test"},
        ]
        (self.root / "manifest.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in manifest_rows),
            encoding="utf-8",
        )
        selected, holds, _ = tranche.build_tranche(
            self.root,
            self.write_input([self.row("new", "new_doc")]),
            row_target=1,
            max_rows_per_doc=1,
            max_rows_per_page=1,
            max_same_text_per_doc=1,
            max_same_text_global=1,
            date_label="fixture",
        )
        self.assertEqual([], selected)
        self.assertIn("source_locked_train", holds[0]["test_scale_hold_reasons"])


if __name__ == "__main__":
    unittest.main()
