import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tools.apply_visualdiff_homography_bbox_repair import apply_preview, file_hash
from tools.audit_visualdiff_homography_boxes import ACTIVE_PATHS, build_audit
from tools.preview_visualdiff_homography_bbox_repair import build_preview


class ApplyVisualDiffHomographyBoxRepairTest(unittest.TestCase):
    def make_preview(self, root: Path) -> Path:
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
        preview_dir = root / "derived/quality/preview"
        build_preview(root, audit_dir / "report.json", file_hash(audit_dir / "report.json"), preview_dir)
        return preview_dir / "report.json"

    @patch("tools.apply_visualdiff_homography_bbox_repair.validate_active")
    def test_applies_preview_with_snapshot(self, validate_active_mock):
        validate_active_mock.return_value = {"repaired_crosslinks_verified": 1}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview = self.make_preview(root)
            report = apply_preview(
                root, preview, file_hash(preview),
                root / "derived/snapshots/apply",
                root / "derived/quality/apply_report.json",
            )
            self.assertEqual("APPLIED", report["status"])
            self.assertEqual(1, report["existing_gold_rows_geometrically_repaired"])
            self.assertEqual(0, report["descriptions_modified"])
            pair = json.loads((root / ACTIVE_PATHS[1]).read_text(encoding="utf-8").strip())
            item = json.loads((root / ACTIVE_PATHS[0]).read_text(encoding="utf-8").strip())
            self.assertEqual([10, 20, 30, 40], pair["bbox_old"])
            self.assertEqual([10, 20, 30, 40], item["evidence"][0]["bbox"])
            self.assertTrue((root / "derived/snapshots/apply/snapshot_manifest.json").is_file())

    def test_rejects_wrong_preview_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview = self.make_preview(root)
            with self.assertRaises(ValueError):
                apply_preview(
                    root, preview, "0" * 64,
                    root / "derived/snapshots/apply",
                    root / "derived/quality/apply_report.json",
                )


if __name__ == "__main__":
    unittest.main()
