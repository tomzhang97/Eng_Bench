import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "tools" / "build_visualdiff_provenance_gap_reservoir.py"
    spec = importlib.util.spec_from_file_location("build_visualdiff_provenance_gap_reservoir", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class VisualDiffProvenanceGapReservoirTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def _fixture(self, root: Path, blocked=False):
        pair_id = "vdiff__fixture__v1__to__v2"
        manifest = [
            {"type": "document", "doc_id": "fixture_v1"},
            {"type": "document", "doc_id": "fixture_v2"},
            {
                "type": "pair",
                "pair_id": pair_id,
                "from_doc_id": "fixture_v1",
                "to_doc_id": "fixture_v2",
            },
        ]
        write_jsonl(root / "manifest.jsonl", manifest)
        status = "rights_uncertain" if blocked else "public_domain_fixture"
        (root / "SOURCE_INVENTORY.csv").write_text(
            "doc_id,public_status\n"
            f"fixture_v1,{status}\n"
            f"fixture_v2,{status}\n",
            encoding="utf-8",
        )
        split_plan = {
            "reservations": [
                {
                    "task": "visualdiff",
                    "unit_id": pair_id,
                    "split": "test",
                    "reservation_id": "fixture-lock",
                    "assignment_basis": "fixture",
                }
            ]
        }
        (root / "split_plan.json").write_text(json.dumps(split_plan), encoding="utf-8")
        for doc_id in ("fixture_v1", "fixture_v2"):
            directory = root / "derived" / "pages_300dpi" / doc_id
            directory.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (120, 120), "white")
            if doc_id == "fixture_v2":
                draw = ImageDraw.Draw(image)
                draw.rectangle((30, 30, 55, 55), fill="black")
            image.save(directory / "p0000.png")
        align = root / "derived" / "align" / pair_id
        align.mkdir(parents=True, exist_ok=True)
        (align / "candidates_page_000.json").write_text(
            json.dumps(
                {
                    "candidates": [
                        {"bbox": [25, 25, 60, 60]},
                        {"bbox": [25, 25, 60, 60]},
                        {"bbox": [75, 75, 95, 95]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (align / "H_page_000.json").write_text(
            json.dumps({"page_index": 0, "H": np.eye(3).tolist()}),
            encoding="utf-8",
        )
        return pair_id

    def _args(self, root: Path):
        return argparse.Namespace(
            root=str(root),
            manifest="manifest.jsonl",
            inventory="SOURCE_INVENTORY.csv",
            split_plan="split_plan.json",
            exclude_jsonl=[],
            allowed_splits="test",
            date_label="fixture",
            target_rows=10,
            min_output_rows=1,
            max_per_pair=10,
            category_quota=[],
            min_area=20,
            max_area_ratio=0.25,
            border_margin=2,
            pad_px=4,
            min_mean_absolute_delta=0.5,
            min_changed_pixel_ratio=0.001,
            min_normalized_mean_absolute_delta=0.5,
        )

    def test_builds_nonmergeable_release_safe_pixel_distinct_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pair_id = self._fixture(root)
            rows, report = self.mod.build_reservoir(self._args(root))
            self.assertTrue(report["valid"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["project_id"], pair_id)
            self.assertEqual(rows[0]["reserved_split"], "test")
            self.assertEqual(rows[0]["review_status"], "needs_machine_review")
            self.assertFalse(rows[0]["safe_to_merge_gold"])
            self.assertEqual(rows[0]["bbox_old"], rows[0]["bbox_new"])
            self.assertEqual(
                rows[0]["replacement_evidence_fingerprint_status"],
                "aligned_pixel_crop_sha256",
            )
            self.assertEqual(report["selected_unique_evidence_fingerprints"], 1)
            self.assertEqual(report["rejections"]["existing_or_duplicate_bbox"], 1)
            self.assertEqual(report["rejections"]["pixel_identical"], 1)

    def test_rejects_pair_without_affirmative_release_rights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, blocked=True)
            args = self._args(root)
            args.min_output_rows = 0
            rows, report = self.mod.build_reservoir(args)
            self.assertEqual(rows, [])
            self.assertEqual(report["rejections"]["non_release_safe_pair"], 1)
            self.assertEqual(report["safe_to_merge_gold_rows"], 0)

    def test_excludes_recorded_pixel_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            args = self._args(root)
            rows, _ = self.mod.build_reservoir(args)
            write_jsonl(
                root / "exclude.jsonl",
                [
                    {
                        "replacement_evidence_fingerprint": rows[0][
                            "replacement_evidence_fingerprint"
                        ]
                    }
                ],
            )
            args.exclude_jsonl = ["exclude.jsonl"]
            args.min_output_rows = 0
            second_rows, report = self.mod.build_reservoir(args)
            self.assertEqual(second_rows, [])
            self.assertEqual(report["rejections"]["duplicate_evidence_fingerprint"], 2)

    def test_alignment_rejects_pure_page_translation_as_pixel_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pair_id = self._fixture(root)
            old_path = root / "derived" / "pages_300dpi" / "fixture_v1" / "p0000.png"
            new_path = root / "derived" / "pages_300dpi" / "fixture_v2" / "p0000.png"
            old_image = Image.new("RGB", (120, 120), "white")
            ImageDraw.Draw(old_image).rectangle((10, 30, 35, 55), fill="black")
            old_image.save(old_path)
            new_image = Image.new("RGB", (120, 120), "white")
            ImageDraw.Draw(new_image).rectangle((30, 30, 55, 55), fill="black")
            new_image.save(new_path)
            align = root / "derived" / "align" / pair_id
            homography = np.asarray(
                [[1.0, 0.0, 20.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            )
            (align / "H_page_000.json").write_text(
                json.dumps({"page_index": 0, "H": homography.tolist()}),
                encoding="utf-8",
            )
            args = self._args(root)
            args.min_output_rows = 0
            rows, report = self.mod.build_reservoir(args)
            self.assertEqual(rows, [])
            self.assertEqual(report["rejections"]["pixel_identical"], 3)

    def test_rejects_candidates_without_recorded_homography(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pair_id = self._fixture(root)
            (root / "derived" / "align" / pair_id / "H_page_000.json").unlink()
            args = self._args(root)
            args.min_output_rows = 0
            rows, report = self.mod.build_reservoir(args)
            self.assertEqual(rows, [])
            self.assertEqual(report["rejections"]["missing_or_invalid_homography"], 1)


if __name__ == "__main__":
    unittest.main()
