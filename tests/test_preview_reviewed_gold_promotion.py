from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.apply_promotion_hold_corrections import apply_corrections
from tools.preview_reviewed_gold_promotion import (
    build_preview,
    visualdiff_description_requires_english_localization,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


class ReviewedGoldPromotionPreviewTest(unittest.TestCase):
    def test_language_rule_allows_english_with_verbatim_source_label(self) -> None:
        self.assertFalse(
            visualdiff_description_requires_english_localization(
                "The visible switch label changed from 开 to 关."
            )
        )
        self.assertTrue(
            visualdiff_description_requires_english_localization(
                "开关标签由 开 改为 关。"
            )
        )

    def build_root(self) -> tuple[Path, tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        source = root / "sources" / "micro.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"micro")
        source_hash = hashlib.sha256(b"micro").hexdigest()
        visual_old = root / "sources" / "visual_old.pdf"
        visual_new = root / "sources" / "visual_new.pdf"
        visual_old.write_bytes(b"visual-old")
        visual_new.write_bytes(b"visual-new")
        visual_old_hash = hashlib.sha256(b"visual-old").hexdigest()
        visual_new_hash = hashlib.sha256(b"visual-new").hexdigest()
        manifest = [{
            "type": "doc",
            "doc_id": "micro",
            "task": "microtext",
            "path": "sources/micro.pdf",
            "sha256": source_hash,
            "source_url": "https://example.test/micro",
            "public_status": "public_domain",
            "version": {"revision": "v1"},
        }, {
            "type": "doc",
            "doc_id": "visual_old",
            "task": "visualdiff",
            "path": "sources/visual_old.pdf",
            "sha256": visual_old_hash,
            "source_url": "https://example.test/visual-old",
            "public_status": "public_domain",
            "same_model_id": "visual_fixture",
            "version": {"revision": "v1"},
        }, {
            "type": "doc",
            "doc_id": "visual_new",
            "task": "visualdiff",
            "path": "sources/visual_new.pdf",
            "sha256": visual_new_hash,
            "source_url": "https://example.test/visual-new",
            "public_status": "public_domain",
            "same_model_id": "visual_fixture",
            "version": {"revision": "v2"},
        }, {
            "type": "pair",
            "pair_id": "vdiff__visual_fixture__v1__to__v2",
            "from_doc_id": "visual_old",
            "to_doc_id": "visual_new",
        }]
        write_jsonl(root / "manifest.jsonl", manifest)
        with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["doc_id", "task", "path", "source_url", "public_status"],
            )
            writer.writeheader()
            writer.writerow({
                "doc_id": "micro",
                "task": "microtext",
                "path": "sources/micro.pdf",
                "source_url": "https://example.test/micro",
                "public_status": "public_domain",
            })
            for doc_id, path, url in (
                ("visual_old", "sources/visual_old.pdf", "https://example.test/visual-old"),
                ("visual_new", "sources/visual_new.pdf", "https://example.test/visual-new"),
            ):
                writer.writerow({
                    "doc_id": doc_id,
                    "task": "visualdiff",
                    "path": path,
                    "source_url": url,
                    "public_status": "public_domain",
                })
        image = root / "derived/pages_300dpi/micro/page_000.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (100, 100), "white").save(image)
        visual_old_image = root / "derived/pages_300dpi/visual_old/page_000.png"
        visual_new_image = root / "derived/pages_300dpi/visual_new/page_000.png"
        visual_old_image.parent.mkdir(parents=True)
        visual_new_image.parent.mkdir(parents=True)
        Image.new("RGB", (100, 100), "white").save(visual_old_image)
        Image.new("RGB", (100, 100), "white").save(visual_new_image)
        for relative in (
            "microtext/annotations/microtext_items.jsonl",
            "microtext/annotations/microtext_questions.jsonl",
            "visualdiff/annotations/visualdiff_pairs.jsonl",
            "visualdiff/annotations/visualdiff_questions.jsonl",
            "eng_bench.jsonl",
        ):
            write_jsonl(root / relative, [])
        for task in ("microtext", "visualdiff"):
            for split in ("train", "dev", "test"):
                path = root / "splits" / f"{task}_{split}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({
            "valid": True,
            "reservations": [{
                "reservation_id": "r1",
                "task": "microtext",
                "unit_id": "micro",
                "split": "test",
            }, {
                "reservation_id": "r2",
                "task": "visualdiff",
                "unit_id": "vdiff__visual_fixture__v1__to__v2",
                "split": "test",
            }],
        }), encoding="utf-8")
        return root, temp, plan

    def test_clean_reviewed_row_builds_read_only_strict_preview(self) -> None:
        root, temp, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        reviewed = root / "reviewed.jsonl"
        write_jsonl(reviewed, [{
            "candidate_id": "candidate_1",
            "doc_id": "micro",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [1, 2, 30, 40],
            "image_path": "derived/pages_300dpi/micro/page_000.png",
            "category": "equipment_tag",
            "proposed_text": "P-101",
            "review_status": "accepted",
            "safe_to_merge_gold": False,
            "reserved_split": "test",
            "split_reservation_id": "r1",
        }])
        before = (root / "eng_bench.jsonl").read_bytes()

        report = build_preview(
            root,
            [reviewed],
            [],
            plan,
            Path("derived/quality/preview"),
            "fixture",
        )

        self.assertTrue(report["ready_for_apply"])
        self.assertTrue(all(report["gates"].values()))
        self.assertEqual(1, report["counts"]["prepared_microtext_rows"])
        self.assertEqual(before, (root / "eng_bench.jsonl").read_bytes())
        self.assertEqual(1, len((root / report["artifacts"]["unified"]).read_text().splitlines()))

    def test_split_conflict_and_unknown_change_type_are_held(self) -> None:
        root, temp, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        reviewed = root / "reviewed.jsonl"
        write_jsonl(reviewed, [{
            "candidate_id": "candidate_1",
            "doc_id": "micro",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [1, 2, 30, 40],
            "image_path": "derived/pages_300dpi/micro/page_000.png",
            "category": "equipment_tag",
            "proposed_text": "P-101",
            "review_status": "accepted",
            "safe_to_merge_gold": False,
            "reserved_split": "train",
            "split_reservation_id": "r1",
        }])

        report = build_preview(
            root,
            [reviewed],
            [],
            plan,
            Path("derived/quality/preview"),
            "fixture",
        )

        self.assertFalse(report["ready_for_apply"])
        self.assertEqual(1, report["counts"]["held_rows"])
        self.assertEqual(1, report["hold_reasons"]["reserved_split_conflict:train:test"])

    def test_final_rows_only_reports_ignored_nonfinal_rows(self) -> None:
        root, temp, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        reviewed = root / "reviewed.jsonl"
        base = {
            "doc_id": "micro",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [1, 2, 30, 40],
            "image_path": "derived/pages_300dpi/micro/page_000.png",
            "category": "equipment_tag",
            "proposed_text": "P-101",
            "safe_to_merge_gold": False,
            "reserved_split": "test",
            "split_reservation_id": "r1",
        }
        write_jsonl(reviewed, [
            {**base, "candidate_id": "candidate_1", "review_status": "accepted"},
            {**base, "candidate_id": "candidate_2", "review_status": "needs_review"},
        ])

        report = build_preview(
            root,
            [reviewed],
            [],
            plan,
            Path("derived/quality/preview"),
            "fixture",
            final_rows_only=True,
        )

        self.assertTrue(report["ready_for_apply"])
        self.assertEqual(2, report["counts"]["scanned_input_rows"])
        self.assertEqual(1, report["counts"]["ignored_nonfinal_rows"])
        self.assertEqual(1, report["counts"]["input_rows"])

    def test_accepted_tentative_visualdiff_description_is_held(self) -> None:
        root, temp, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        reviewed = root / "visual_reviewed.jsonl"
        write_jsonl(reviewed, [{
            "pair_id": "vdiff__visual_fixture__v1__to__v2__p0000__000",
            "project_id": "vdiff__visual_fixture__v1__to__v2",
            "page_index_old": 0, "page_index_new": 0,
            "bbox_old": [1, 2, 30, 40], "bbox_new": [1, 2, 30, 40],
            "image_old": "derived/pages_300dpi/visual_old/page_000.png",
            "image_new": "derived/pages_300dpi/visual_new/page_000.png",
            "change_type": "addition", "human_review_status": "accepted",
            "human_description": "Localized text may have been added: 'U3'.",
            "safe_to_merge_gold": False, "reserved_split": "test",
            "split_reservation_id": "r2",
        }])
        report = build_preview(root, [], [reviewed], plan, Path("derived/quality/preview"), "fixture")
        self.assertFalse(report["ready_for_apply"])
        self.assertEqual(1, report["hold_reasons"]["tentative_visualdiff_description"])
        self.assertEqual(0, report["counts"]["prepared_visualdiff_rows"])

    def test_cjk_visualdiff_description_is_held_for_english_localization(self) -> None:
        root, temp, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        reviewed = root / "visual_reviewed.jsonl"
        write_jsonl(reviewed, [{
            "pair_id": "vdiff__visual_fixture__v1__to__v2__p0000__000",
            "project_id": "vdiff__visual_fixture__v1__to__v2",
            "page_index_old": 0,
            "page_index_new": 0,
            "bbox_old": [1, 2, 30, 40],
            "bbox_new": [1, 2, 30, 40],
            "image_old": "derived/pages_300dpi/visual_old/page_000.png",
            "image_new": "derived/pages_300dpi/visual_new/page_000.png",
            "change_type": "text_change_candidate",
            "human_description": "电阻值由 10K 改为 5.1K。",
            "human_review_status": "edit",
            "safe_to_merge_gold": False,
            "reserved_split": "test",
            "split_reservation_id": "r2",
        }])

        report = build_preview(
            root,
            [],
            [reviewed],
            plan,
            Path("derived/quality/preview"),
            "fixture",
        )

        self.assertFalse(report["ready_for_apply"])
        self.assertEqual(1, report["counts"]["held_rows"])
        self.assertEqual(
            1,
            report["hold_reasons"]["visualdiff_description_requires_english_localization"],
        )
        hold_csv = root / report["artifacts"]["holds_csv"]
        with hold_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(1, len(rows))
        self.assertEqual("", rows[0]["corrected_english_description"])

    def test_human_correction_round_trip_becomes_preview_ready(self) -> None:
        root, temp, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        reviewed = root / "visual_reviewed.jsonl"
        write_jsonl(reviewed, [{
            "pair_id": "vdiff__visual_fixture__v1__to__v2__p0000__000",
            "project_id": "vdiff__visual_fixture__v1__to__v2",
            "page_index_old": 0,
            "page_index_new": 0,
            "bbox_old": [1, 2, 30, 40],
            "bbox_new": [1, 2, 30, 40],
            "image_old": "derived/pages_300dpi/visual_old/page_000.png",
            "image_new": "derived/pages_300dpi/visual_new/page_000.png",
            "change_type": "schematic_change_candidate",
            "human_description": "电阻值由 10K 改为 5.1K。",
            "human_review_status": "edit",
            "safe_to_merge_gold": False,
            "reserved_split": "test",
            "split_reservation_id": "r2",
        }])
        first = build_preview(
            root, [], [reviewed], plan, Path("derived/quality/preview1"), "fixture"
        )
        hold_csv = root / first["artifacts"]["holds_csv"]
        with hold_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fields = list(handle and rows[0].keys())
        rows[0]["human_status"] = "edited"
        rows[0]["corrected_english_description"] = (
            "The resistor value changed from 10K to 5.1K."
        )
        rows[0]["confirmed_change_type"] = "value+text"
        rows[0]["reviewer_notes"] = "Verified against both crops."
        with hold_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        corrected = root / "corrected.jsonl"
        apply_corrections(
            root / first["artifacts"]["holds"],
            hold_csv,
            corrected,
            root / "correction_report.json",
        )

        second = build_preview(
            root, [], [corrected], plan, Path("derived/quality/preview2"), "fixture"
        )

        self.assertTrue(second["ready_for_apply"])
        self.assertEqual(1, second["counts"]["prepared_visualdiff_rows"])
        self.assertEqual(0, second["counts"]["held_rows"])


if __name__ == "__main__":
    unittest.main()
