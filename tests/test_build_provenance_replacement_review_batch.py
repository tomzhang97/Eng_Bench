import csv
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_provenance_replacement_review_batch as batch


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class ProvenanceReplacementReviewBatchTests(unittest.TestCase):
    def make_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        (root / "eng_bench.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
        for relative, color in (
            ("derived/pages_300dpi/micro/page_000.png", "white"),
            ("derived/pages_300dpi/old/page_000.png", "white"),
            ("derived/pages_300dpi/new/page_000.png", "gray"),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (160, 120), color=color).save(path)
        return root

    def rows(self) -> list[dict]:
        common = {
            "review_status": "needs_review",
            "safe_to_merge_gold": False,
                "replacement_match_level": "task_split_category",
        }
        return [
            {
                **common,
                "candidate_id": "mt_001",
                "replacement_for_task": "microtext",
                "replacement_for_split": "dev",
                "replacement_source_unit": "micro",
                "candidate_id": "mt_001",
                "doc_id": "micro",
                "page_index": 0,
                "bbox": [20, 30, 100, 60],
                "image_path": "derived/pages_300dpi/micro/page_000.png",
                "proposed_text": "R10",
                "category": "dimension_value",
                "replacement_evidence_fingerprint": "microtext:sha256:one",
            },
            {
                **common,
                "pair_id": "vd_001",
                "replacement_for_task": "visualdiff",
                "replacement_for_split": "test",
                "replacement_source_unit": "pair",
                "bbox_old": [20, 20, 80, 70],
                "bbox_new": [20, 20, 80, 70],
                "image_old": "derived/pages_300dpi/old/page_000.png",
                "image_new": "derived/pages_300dpi/new/page_000.png",
                "change_type": "text",
                "description": "CHANGE_DESC_GT_TODO",
                "replacement_evidence_fingerprint": "visualdiff:sha256:two",
            },
        ]

    def test_build_writes_portable_evidence_and_checklists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            queue = root / "derived/review_queues/new.jsonl"
            assigned = root / "derived/review_queues/assigned.jsonl"
            write_jsonl(queue, self.rows())
            write_jsonl(assigned, [])

            report = batch.build_batch(
                root=root,
                input_path=Path("derived/review_queues/new.jsonl"),
                already_assigned_path=Path("derived/review_queues/assigned.jsonl"),
                output_dir=Path("derived/human_adjudication/test_batch"),
                date_label="test",
                pad_px=8,
            )

            output = root / "derived/human_adjudication/test_batch"
            self.assertEqual(report["counts"]["total"], 2)
            self.assertTrue(report["audit"]["valid"])
            micro_manifest = batch.read_jsonl(output / "review_packs/microtext_1/manifest.jsonl")
            visual_manifest = batch.read_jsonl(output / "review_packs/visualdiff_1/manifest.jsonl")
            self.assertEqual(micro_manifest[0]["crop_path"], "crops/mt_001.png")
            self.assertEqual(visual_manifest[0]["panel_path"], "panels/vd_001.png")
            self.assertFalse(micro_manifest[0]["safe_to_merge_gold"])
            self.assertFalse(visual_manifest[0]["safe_to_merge_gold"])
            self.assertTrue(visual_manifest[0]["description_rewrite_required"])

            with (output / "review_packs/microtext_1/microtext_validation_checklist.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            with (output / "review_packs/visualdiff_1/visualdiff_validation_checklist.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                visual_checklist = list(csv.DictReader(handle))
            self.assertEqual(visual_checklist[0]["description_rewrite_required"], "True")
            self.assertIn("必须选择 2", visual_checklist[0]["description_task"])
            with Image.open(output / "review_packs/visualdiff_1/panels/vd_001.png") as panel:
                self.assertEqual(panel.getpixel((4, 4)), (153, 27, 27))
                self.assertEqual(panel.getpixel((panel.width - 5, 4)), (15, 107, 111))
            with (output / "NEXT_REVIEW_BATCH_MANIFEST.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                batch_manifest = list(csv.DictReader(handle))
            self.assertEqual(
                [row["kind"] for row in batch_manifest], ["microtext", "visualdiff"]
            )
            self.assertEqual(
                {row["source_jsonl"] for row in batch_manifest},
                {"derived/review_queues/new.jsonl"},
            )
            steps = (output / "HUMAN_REVIEW_STEPS.md").read_text(encoding="utf-8")
            self.assertIn("本批次共 2 条（MicroText 1 条，VisualDiff 1 条）", steps)
            self.assertIn("01_MicroText_1.xlsx", steps)
            self.assertIn("02_VisualDiff_1.xlsx", steps)
            self.assertIn("## MicroText 规则", steps)
            self.assertIn("## VisualDiff 规则", steps)
            self.assertIn("VisualDiff 强制描述任务", steps)
            self.assertIn("有 `1` 条机器描述", steps)
            self.assertIn("只交回上述 2 个填写后的 XLSX", steps)
            self.assertNotIn("本批次共 672 条", steps)

    def test_build_rejects_overlap_with_assigned_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            queue = root / "derived/review_queues/new.jsonl"
            assigned = root / "derived/review_queues/assigned.jsonl"
            rows = self.rows()
            write_jsonl(queue, rows)
            write_jsonl(assigned, [{"candidate_id": "mt_001"}])

            with self.assertRaisesRegex(ValueError, "already-assigned overlap"):
                batch.build_batch(
                    root=root,
                    input_path=Path("derived/review_queues/new.jsonl"),
                    already_assigned_path=Path("derived/review_queues/assigned.jsonl"),
                    output_dir=Path("derived/human_adjudication/test_batch"),
                    date_label="test",
                    pad_px=8,
                )

    def test_visualdiff_only_batch_omits_empty_microtext_pack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.make_root(temp_dir)
            queue = root / "derived/review_queues/new.jsonl"
            assigned = root / "derived/review_queues/assigned.jsonl"
            write_jsonl(queue, [self.rows()[1]])
            write_jsonl(assigned, [])

            report = batch.build_batch(
                root=root,
                input_path=Path("derived/review_queues/new.jsonl"),
                already_assigned_path=Path("derived/review_queues/assigned.jsonl"),
                output_dir=Path("derived/human_adjudication/visual_only"),
                date_label="test",
                pad_px=8,
            )

            output = root / "derived/human_adjudication/visual_only"
            self.assertEqual(report["counts"]["total"], 1)
            self.assertFalse((output / "review_packs/microtext_0").exists())
            self.assertTrue((output / "review_packs/visualdiff_1").is_dir())
            steps = (output / "HUMAN_REVIEW_STEPS.md").read_text(encoding="utf-8")
            self.assertNotIn("MicroText_0", steps)
            self.assertIn("02_VisualDiff_1.xlsx", steps)
            self.assertNotIn("## MicroText 规则", steps)
            self.assertIn("## VisualDiff 规则", steps)
            self.assertIn("只交回上述 1 个填写后的 XLSX", steps)


if __name__ == "__main__":
    unittest.main()
