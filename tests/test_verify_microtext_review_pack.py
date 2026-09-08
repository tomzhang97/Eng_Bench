from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from tools.verify_microtext_review_pack import audit_pack


class VerifyMicrotextReviewPackTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path]:
        pack = root / "pack"
        crop = pack / "crops" / "cand.png"
        page = pack / "pages" / "page.png"
        source = root / "source.png"
        for path in (crop, page, source):
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4), "white").save(path)
        row = {
            "candidate_id": "cand",
            "crop_path": "crops/cand.png",
            "page_path": "pages/page.png",
            "image_path": "source.png",
        }
        (pack / "manifest.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        queue = root / "queue.jsonl"
        queue.write_text(json.dumps(row) + "\n", encoding="utf-8")
        (pack / "index.html").write_text('<img src="crops/cand.png">', encoding="utf-8")
        (pack / "README.md").write_text("review\n", encoding="utf-8")
        (pack / "INTERN_INSTRUCTIONS_ZH.md").write_text("review\n", encoding="utf-8")
        with (pack / "checklist.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["candidate_id", "review_status"])
            writer.writeheader()
            writer.writerow({"candidate_id": "cand", "review_status": ""})
        with zipfile.ZipFile(pack / "checklist.xlsx", "w") as workbook:
            workbook.writestr("[Content_Types].xml", "<Types/>")
            workbook.writestr("xl/workbook.xml", "<workbook/>")
        gold = root / "microtext" / "annotations" / "microtext_items.jsonl"
        gold.parent.mkdir(parents=True)
        gold.write_text("", encoding="utf-8")
        return pack, queue

    def test_valid_pack_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            report = audit_pack(root, pack, queue)
            self.assertTrue(report["valid"])
            self.assertEqual(report["counts"]["queue_rows"], 1)
            self.assertEqual(report["counts"]["missing_evidence_paths"], 0)

    def test_embedded_evidence_requirement_rejects_legacy_external_only_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)

            report = audit_pack(
                root,
                pack,
                queue,
                require_embedded_evidence=True,
            )

            self.assertFalse(report["valid"])
            self.assertFalse(report["embedded_evidence_valid"])
            self.assertTrue(
                any("embedded evidence" in issue for issue in report["issues"])
            )

    def test_gold_collision_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            gold = root / "microtext" / "annotations" / "microtext_items.jsonl"
            gold.write_text(json.dumps({"item_id": "cand"}) + "\n", encoding="utf-8")
            report = audit_pack(root, pack, queue)
            self.assertFalse(report["valid"])
            self.assertEqual(report["counts"]["gold_id_collisions"], 1)

    def test_gold_region_alias_collision_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, queue = self.build_fixture(root)
            manifest_path = pack / "manifest.jsonl"
            row = json.loads(manifest_path.read_text(encoding="utf-8"))
            row.update(doc_id="doc", page_index=2, bbox=[1, 2, 3, 4])
            manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            queue.write_text(json.dumps(row) + "\n", encoding="utf-8")
            gold = root / "microtext" / "annotations" / "microtext_items.jsonl"
            gold.write_text(
                json.dumps(
                    {
                        "item_id": "different_gold_id",
                        "doc_id": "doc",
                        "page_index": 2,
                        "bbox": [1, 2, 3, 4],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = audit_pack(root, pack, queue)

            self.assertFalse(report["valid"])
            self.assertEqual(report["counts"]["gold_id_collisions"], 0)
            self.assertEqual(report["counts"]["gold_region_collisions"], 1)


if __name__ == "__main__":
    unittest.main()
