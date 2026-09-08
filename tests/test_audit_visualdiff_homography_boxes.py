import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tools.audit_visualdiff_homography_boxes import (
    ACTIVE_PATHS, bbox_iou, build_audit, local_visual_match, transform_bbox,
)


class VisualDiffHomographyBoxAuditTest(unittest.TestCase):
    def test_inverse_translation_maps_new_box_to_old_coordinates(self):
        old_to_new = np.array([[1, 0, 10], [0, 1, 0], [0, 0, 1]], dtype=float)
        mapped = transform_bbox([20, 20, 40, 40], np.linalg.inv(old_to_new))
        self.assertEqual([10.0, 20.0, 30.0, 40.0], mapped)
        self.assertAlmostEqual(1 / 3, bbox_iou([20, 20, 40, 40], mapped))

    def test_builds_read_only_repair_proposal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
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
            pairs_path = root / ACTIVE_PATHS[1]
            pairs_path.write_text(json.dumps(pair) + "\n", encoding="utf-8")
            unified = {
                "id": "q_row", "task": "visualdiff",
                "images": ["old.png", "new.png"],
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
            before = pairs_path.read_bytes()
            report = build_audit(root, "project", root / "derived/quality/audit")
            self.assertEqual(1, report["repair_candidate_rows"])
            self.assertFalse(report["active_gold_modified"])
            self.assertEqual(before, pairs_path.read_bytes())
            proposal = json.loads(
                (root / report["proposal_jsonl"]).read_text(encoding="utf-8").strip()
            )
            self.assertEqual([10, 20, 30, 40], proposal["bbox_old_projected"])
            self.assertFalse(proposal["safe_to_apply"])

    def test_local_visual_match_finds_small_translation(self):
        aligned = np.full((80, 100, 3), 255, dtype=np.uint8)
        new = aligned.copy()
        aligned[25:35, 33:45] = 0
        new[27:37, 36:48] = 0
        match = local_visual_match(aligned, new, [30, 20, 55, 45], pad=10)
        self.assertEqual("measured", match["status"])
        self.assertEqual("high", match["class"])
        self.assertEqual((-3, -2), (match["offset_x"], match["offset_y"]))

    def test_rejects_output_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ACTIVE_PATHS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_audit(root, "missing", root / "outside")


if __name__ == "__main__":
    unittest.main()
