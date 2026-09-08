from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from tools.verify_visualdiff_review_pack import audit_pack, audit_zip


class VerifyVisualdiffReviewPackTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path]:
        pack = root / "pack"
        for folder in ("old", "new", "panels", "pages_old", "pages_new"):
            path = pack / folder / "pair.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(path)
        row = {
            "pair_id": "pair",
            "project_id": "family",
            "split": "provisional_review",
            "reserved_split": "test",
            "safe_to_merge_gold": False,
            "old_crop_path": "old/pair.png",
            "new_crop_path": "new/pair.png",
            "panel_path": "panels/pair.png",
            "old_page_path": "pages_old/pair.png",
            "new_page_path": "pages_new/pair.png",
        }
        (pack / "manifest.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        queue = root / "queue.jsonl"
        queue.write_text(json.dumps(row) + "\n", encoding="utf-8")
        (pack / "index.html").write_text("panels/pair.png", encoding="utf-8")
        (pack / "README.md").write_text("review\n", encoding="utf-8")
        (pack / "INTERN_REVIEW_STEPS_ZH.md").write_text("review\n", encoding="utf-8")
        with (pack / "validation_checklist.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["pair_id", "split", "human_status", "human_description", "human_notes"],
            )
            writer.writeheader()
            writer.writerow({"pair_id": "pair", "split": "test"})
        gold = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
        gold.parent.mkdir(parents=True)
        gold.write_text("", encoding="utf-8")
        return pack, queue

    def test_valid_pack_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            report = audit_pack(root, pack, queue)
            self.assertTrue(report["valid"])
            self.assertEqual(report["counts"]["missing_evidence_paths"], 0)
            self.assertEqual(report["counts"]["rows_by_reserved_split"], {"test": 1})

    def test_missing_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            (pack / "panels" / "pair.png").unlink()
            report = audit_pack(root, pack, queue)
            self.assertFalse(report["valid"])
            self.assertEqual(report["counts"]["missing_evidence_paths"], 1)

    def test_gold_collision_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            gold = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
            gold.write_text(json.dumps({"pair_id": "pair"}) + "\n", encoding="utf-8")
            report = audit_pack(root, pack, queue)
            self.assertFalse(report["valid"])
            self.assertEqual(report["counts"]["gold_id_collisions"], 1)

    def test_zip_round_trip_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            zip_path = root / "pack.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                for path in sorted(pack.rglob("*")):
                    if path.is_file():
                        archive.write(path, f"pack/{path.relative_to(pack).as_posix()}")
            report = audit_zip(root, zip_path, queue)
            self.assertTrue(report["valid"])
            self.assertEqual(report["zip_single_root"], "pack")


if __name__ == "__main__":
    unittest.main()
