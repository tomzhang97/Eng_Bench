import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.audit_visualdiff_homography_boxes import ACTIVE_PATHS, build_audit
from tools.preview_visualdiff_homography_bbox_repair import build_preview, file_hash


class VisualDiffHomographyBoxPreviewTest(unittest.TestCase):
    def make_fixture(self, root: Path) -> Path:
        for name in ACTIVE_PATHS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        pair = {
            "pair_id": "row", "project_id": "project", "split": "train",
            "change_desc_gt": "TODO", "page_index_old": 0,
            "page_index_new": 0, "bbox_old": [20, 20, 40, 40],
            "bbox_new": [20, 20, 40, 40],
        }
        (root / ACTIVE_PATHS[1]).write_text(json.dumps(pair) + "\n", encoding="utf-8")
        unified = {
            "id": "q_row", "task": "visualdiff", "images": ["old.png", "new.png"],
            "evidence": [
                {"bbox": [20, 20, 40, 40], "image_index": 0},
                {"bbox": [20, 20, 40, 40], "image_index": 1},
            ],
            "metadata": {"pair_id": "row"},
        }
        (root / ACTIVE_PATHS[0]).write_text(json.dumps(unified) + "\n", encoding="utf-8")
        Image.new("RGB", (100, 100), "white").save(root / "old.png")
        Image.new("RGB", (100, 100), "white").save(root / "new.png")
        (root / "manifest.jsonl").write_text(json.dumps({
            "type": "pair", "pair_id": "project",
            "from_doc_id": "old_doc", "to_doc_id": "new_doc",
        }) + "\n", encoding="utf-8")
        textlayer = root / "derived/textlayer"
        textlayer.mkdir(parents=True)
        (textlayer / "old_doc.jsonl").write_text("", encoding="utf-8")
        (textlayer / "new_doc.jsonl").write_text("", encoding="utf-8")
        align = root / "derived/align/project"
        align.mkdir(parents=True)
        (align / "H_page_000.json").write_text(json.dumps({
            "info": {"status": "ok", "inlier_ratio": 1.0},
            "H": [[1, 0, 10], [0, 1, 0], [0, 0, 1]],
        }), encoding="utf-8")
        audit_dir = root / "derived/quality/audit"
        build_audit(root, "project", audit_dir)
        return audit_dir / "report.json"

    def test_preview_updates_only_old_bbox_in_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit_report = self.make_fixture(root)
            active_before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
            report = build_preview(
                root, audit_report, file_hash(audit_report),
                root / "derived/quality/preview",
            )
            self.assertTrue(report["ready_for_apply"])
            self.assertEqual(1, report["correction_rows"])
            self.assertFalse(report["active_gold_modified"])
            self.assertEqual(active_before, {path: file_hash(root / path) for path in ACTIVE_PATHS})
            preview_pair = json.loads(
                (root / report["artifacts"]["pairs_preview"]).read_text(encoding="utf-8").strip()
            )
            preview_item = json.loads(
                (root / report["artifacts"]["unified_preview"]).read_text(encoding="utf-8").strip()
            )
            self.assertEqual([10, 20, 30, 40], preview_pair["bbox_old"])
            self.assertEqual([10, 20, 30, 40], preview_item["evidence"][0]["bbox"])
            self.assertEqual([20, 20, 40, 40], preview_item["evidence"][1]["bbox"])

    def test_preview_rejects_bad_report_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit_report = self.make_fixture(root)
            with self.assertRaises(ValueError):
                build_preview(
                    root, audit_report, "0" * 64,
                    root / "derived/quality/preview",
                )


if __name__ == "__main__":
    unittest.main()
