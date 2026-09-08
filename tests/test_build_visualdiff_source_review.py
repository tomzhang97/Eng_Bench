import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BuildVisualdiffSourceReviewTests(unittest.TestCase):
    def test_map_bbox_to_old_uses_inverse_homography(self):
        mod = load_module(
            "build_visualdiff_source_review",
            ROOT / "tools" / "build_visualdiff_source_review.py",
        )

        homography_old_to_new = [
            [1.0, 0.0, 10.0],
            [0.0, 1.0, 20.0],
            [0.0, 0.0, 1.0],
        ]

        self.assertEqual(
            mod.map_bbox_to_old([30, 50, 60, 90], homography_old_to_new, 100, 100),
            [20, 30, 50, 70],
        )

    def test_cluster_candidate_boxes_merges_nearby_change_fragments(self):
        mod = load_module(
            "build_visualdiff_source_review_clusters",
            ROOT / "tools" / "build_visualdiff_source_review.py",
        )

        clusters = mod.cluster_candidate_boxes(
            [
                {"bbox": [100, 100, 140, 130]},
                {"bbox": [150, 102, 180, 132]},
                {"bbox": [400, 400, 440, 440]},
            ],
            gap_px=12,
        )

        self.assertEqual(clusters, [[100, 100, 180, 132], [400, 400, 440, 440]])

    def test_build_rows_filters_bad_alignment_border_and_large_boxes(self):
        mod = load_module(
            "build_visualdiff_source_review_rows",
            ROOT / "tools" / "build_visualdiff_source_review.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            align = root / "derived" / "align" / "vdiff__demo__a__to__b"
            align.mkdir(parents=True)
            old_page = root / "derived" / "pages_300dpi" / "demo_a" / "page_000.png"
            new_page = root / "derived" / "pages_300dpi" / "demo_b" / "page_000.png"
            old_page.parent.mkdir(parents=True)
            new_page.parent.mkdir(parents=True)
            Image.new("RGB", (1000, 800), "white").save(old_page)
            Image.new("RGB", (1000, 800), "white").save(new_page)
            (align / "H_page_000.json").write_text(
                json.dumps(
                    {
                        "page_index": 0,
                        "info": {"status": "ok", "inlier_ratio": 0.8, "inliers": 100},
                        "H": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                    }
                ),
                encoding="utf-8",
            )
            (align / "candidates_page_000.json").write_text(
                json.dumps(
                    {
                        "page_index": 0,
                        "candidates": [
                            {"bbox": [100, 100, 220, 210], "area": 13200},
                            {"bbox": [2, 2, 80, 80], "area": 6084},
                            {"bbox": [100, 100, 900, 700], "area": 480000},
                            {"bbox": [850, 700, 900, 750], "area": 2500},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            rows, report = mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pcb_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
                min_area=1000,
                max_area_ratio=0.2,
                border_margin=8,
                min_inlier_ratio=0.3,
                merge_gap_px=0,
                max_total=20,
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["bbox_old"], [100, 100, 220, 210])
            self.assertEqual(rows[0]["bbox_new"], [100, 100, 220, 210])
            self.assertEqual(rows[0]["source_candidate_id"], "pcb_999")
            self.assertEqual(report["skipped_border"], 1)
            self.assertEqual(report["skipped_too_large"], 1)
            self.assertEqual(report["skipped_titleblock_corner"], 1)


if __name__ == "__main__":
    unittest.main()
